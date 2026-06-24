# Phase 2 plan: multi-user readiness

How the single-user MVP becomes a multi-user app, on the current Railway (backend) + Vercel (frontend) stack. Identity decision is [ADR 0022](../adr/0022-identity-is-social-login-via-clerk-strava-stays-an-integration.md) (social login via Clerk, superseding the magic-link mechanism of ADR 0005). Sync decision is [ADR 0006](../adr/0006-multi-user-drops-polling-for-user-triggered-self-healing.md). Cost framing is `docs/vision/going-live-cost-control.md`.

This is the planning entry point for the #67 epic. Implementation lands across the sub-issues (#118–#124), one PR per phase below.

## Goal

Open the app to a handful of beta users (friends/beta scale) with per-user identity, strict tenant isolation, and a structural per-user LLM cost cap, without changing the deployment topology beyond what multi-user requires.

## Guiding constraints

- Easiest secure option wins; do not hand-roll auth (ADR 0022).
- Identity is the verified email; data sources (Strava, later Garmin/Apple) stay peer integrations.
- Per-user cost cap must be structural, not alert-only (the owner's hard rule).
- Tenant isolation is the top correctness risk; treat its phase as higher-risk.

## Phases

Sequenced by dependency. Each phase is one PR and maps to one issue.

### P2.0 — Auth foundation (#118, Clerk social login)

- **Modality:** Feature (new auth surface) + Configure (Clerk/Google as a real external system).
- **What changes (user-visible):** an unauthenticated visitor sees a Google sign-in; signing in lands on their own account; a returning user reaches the same account by email.
- **Approach:** Clerk app + Google provider; Next.js sign-in UI and session; Vercel proxy forwards the Clerk session token; FastAPI verifies it via Clerk JWKS and resolves `user_id` from the verified email; `User.email` becomes non-null + unique (migration + placeholder backfill reconciled to the owner's Google email on first sign-in); `app/api/profile.py` stops auto-creating the single user. `BasicAuthMiddleware` is repurposed as the frontend↔backend service secret, not removed.
- **Oracle:** the real Clerk/Google flow (Configure modality: exercise the live provider, not a mock); identity-by-email behaviour per ADR 0022.
- **Higher-risk:** crosses the Vercel↔Railway seam and touches a third-party integration. Runtime validation required: a real sign-in end-to-end, plus a verified-token rejection path (bad/expired token denied).
- **Verification:** end-to-end sign-in on the real provider; token-verification negative path; confirms backend resolves the correct `user_id`.

### P2.1 — Tenant-safe data isolation sweep (#119) — HIGHEST RISK

- **Modality:** Refactor (behaviour-preserving for one user) crossed with a security audit.
- **What changes:** every read/write is scoped to the authenticated `user_id`; a request for another user's resource is denied, not served. Covers activities, streams, derived metrics, blocks, exchanges, check-ins, coach reports, chat, profile, baseline, coaching relationship/context/narrative, user materials, trends, training load, Strava connection.
- **Approach:** audit every `db.query(...).first()` / `select(...)` that assumes the one local user (start from `activity_queries.py`, `trends.py`, `training_load.py`, `blocks.py`, the coach service report lookups, `checkins.py`); add `user_id` filters; add a DB index on `user_id` where it becomes a hot filter (the performance audit already flagged this for multi-user, [[project_performance_audit_2026_06_18]]).
- **Oracle:** current single-user behaviour is preserved for the owning user (captured behaviour); cross-user access is the new negative contract.
- **Higher-risk:** persistent state shared across the whole app; a missed filter is a cross-tenant leak.
- **Verification:** negative-path tests asserting cross-user access is rejected on list, detail, trends, and coach lookups; the existing suite must still pass for the owning user.

### P2.2 — Per-user LLM cost cap (#122)

- **Modality:** Feature, on the structural-cap design in the going-live doc.
- **What changes:** each user has a bounded LLM budget over a window; at the cap, generation is refused/deferred with a clear signal (degrade to the existing `is_fallback` / deterministic receipt path), never silently failed; one user hitting the cap never affects another.
- **Approach:** the Redis budget gate from the going-live doc (Ring 3), checked before every call in `llm.py`, keyed by activity-owner `user_id`; configurable per-user ceiling; plus the regenerate cooldown + deterministic `job_id` (closes the worst lever, going-live landmine 1).
- **Oracle:** the budget arithmetic (token counts × the per-MTok price table) and the degrade-not-crash contract.
- **Verification:** a user driven over the cap degrades to fallback while a second user is unaffected; the cap is observed before the call, not after.

### P2.3 — Account deletion with full cascade (#121)

- **Modality:** Feature + Delete (destructive, hard to reverse).
- **What changes:** a user can delete their own account and all derived data; no orphaned rows; one deletion never touches another user's data; the Clerk user is deleted too (ADR 0022) so a deleted account cannot sign back into an empty shell.
- **Approach:** deletion endpoint scoped to the authenticated user; cascade across the *current* table set — Activity, ActivityStream, DerivedMetric, Block, Exchange, CheckIn, CoachReport, CoachChatMessage, CoachingRelationship, CoachingContext, CoachNarrative, RunnerBaseline, UserMaterial, StravaAccount, UserProfile, User — plus the Clerk-side user delete.
- **Oracle:** the schema's FK graph (no row referencing the deleted user survives).
- **Higher-risk:** destructive and cross-table. Verification: post-deletion, assert zero rows remain for that user across every table, and another user's data is intact.

### P2.4 — Per-user notification routing (#120) — OPEN DESIGN

- **Modality:** Feature, but blocked on a design decision (see Open decisions).
- **What changes:** a coach-report notification reaches the owning user only, on their own channel; no deployment-wide recipient; at-most-once-per-activity-per-channel preserved; a user with no deliverable channel simply gets nothing without breaking the pipeline.
- **Constraint:** the deployed channel is Telegram bound to one `TELEGRAM_CHAT_ID`. Multi-user needs either per-user Telegram chat binding (each user links their own chat) or the HTTP email API ADR 0005 assumed (which also finally fixes coach email, [[project_railway_smtp_egress_blocked]]). Decide before building.

### P2.5 — Drop polling for self-healing sync (#123, ADR 0006)

- **Modality:** Refactor + Delete (remove the polling job and scheduler process).
- **What changes:** the recurring polling job and the `rqscheduler` process are gone (runtime drops to web + worker); missed activities are detected on authenticated app-open and via a manual refresh affordance; the check is bounded (≤ one per user per minute) so rapid repeats do not multiply Strava calls; per-activity dedup preserved; local single-user manual sync unaffected.
- **Oracle:** ADR 0006; the existing dedup guarantee (`coach_notification_sent_at` / receipt sentinels) is the invariant.
- **Higher-risk:** changes data-flow between processes and touches the Strava integration. Verification: a missed-webhook activity is caught on app-open end-to-end; the bound prevents call multiplication.

### P2.6 — Custom domain (#124) — operator action

- Acquire a domain; serve the Vercel frontend and reach the Railway backend on stable hostnames; confirm the proxy seam works over the new hostnames. Domain/DNS are operator actions outside the working tree; the only code-side concern is CORS origins and any hardcoded hostnames.

## Sequencing and dependencies

P2.0 is the prerequisite for P2.1–P2.4 (they all need `user_id`). Recommended order: **P2.0 → P2.1 → P2.2 → P2.3**, with P2.5 (sync) and P2.6 (domain) parallelizable once P2.0 lands. P2.4 (notifications) is gated on the open channel decision and can land last. Do not onboard a second real user until P2.1 (isolation) and P2.3 (deletion) are both done.

## Open decisions (owner)

1. **Notification channel for multi-user (P2.4):** per-user Telegram binding, or add the HTTP email API? The email API also fixes coach email; Telegram keeps the current channel but needs per-user linking UX.
2. **Monthly + per-user spend ceilings (P2.2):** the actual numbers, and "at the cap: degrade or hard-block?"
3. **Clerk vs Auth.js if vendor-aversion grows:** ADR 0022 picks Clerk; Auth.js is the documented fallback with no plan change beyond the auth wiring.

## Testing notes

- Backend has unit/integration coverage for analysis, coach, webhooks, sync; **no auth tests exist** (none needed under single-user Basic auth) and **no frontend component tests** beyond lint + build + smoke. P2.0 and P2.1 must add the first auth and isolation tests.
- The tenant-isolation phase needs negative-path tests as first-class coverage, not an afterthought; a green single-user suite is structurally blind to cross-tenant leaks.
- Auth and sync phases require runtime end-to-end validation against the real provider / real Strava behaviour, not mock-only.
