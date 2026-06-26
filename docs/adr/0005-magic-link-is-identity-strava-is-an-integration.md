# Email magic-link is the identity; data sources are connected integrations

> **Status: Superseded by [ADR 0022](0022-identity-is-social-login-via-clerk-strava-stays-an-integration.md).** The *principle* below stands unchanged (identity lives at the email level; Strava is a connected integration, not the identity). The magic-link *mechanism* was replaced by Clerk social login, which shipped in Phase 2 (2026-06-24), because Railway blocks the outbound SMTP the in-house link delivery relied on. This ADR is retained as the original record.

The current codebase has no auth layer: `app/api/profile.py` auto-creates a single `User` row on first read, and every other endpoint implicitly trusts that user. Multi-user deployment is on the roadmap, and the natural-looking shortcut is to promote Strava OAuth to the identity layer — the user signs in with Strava, `strava_athlete_id` becomes the user identity, no second IdP needed.

We reject that shortcut. Garmin Connect and Apple Health are planned future integrations, and they are peers of Strava, not children of it. A Garmin-first user with no Strava account must be able to sign up; a user who later disconnects Strava must not lose access to their own historical data. Strava-as-identity collapses identity and data-source into one concept, which produces a painful "decouple identity from Strava" migration the moment a second integration ships.

Instead, identity lives at the email level, behind a magic-link flow:

- `POST /api/auth/request-link` accepts an email, generates a single-use token (hashed at rest, ~15 min TTL), and emails the user a link.
- `GET /api/auth/verify?token=...` exchanges the token for a signed HttpOnly `SameSite=Lax` session cookie carrying `user_id`. First-time emails create the `User` row; subsequent verifies match by email.
- The session cookie's lifetime is independent of the user's data. Cookie expiry logs them out; signing in again with a fresh link restores the same account.

Connected integrations hang off `User` as peer side-tables: `StravaAccount` today; `GarminAccount`, `AppleHealthAccount` later. Each integration owns its own OAuth state, tokens, and webhook routing. The integration port pattern from [ADR 0002](0002-strava-adapter-is-pure-transport.md) extends naturally to the new providers.

Magic-link delivery reuses the existing `SMTPNotifier` (`app/services/notifications/smtp_adapter.py`). No new auth provider, no Auth0/Clerk dependency, no password storage.

## Consequences

- New tables (alembic migration): `login_token` (`token_hash`, `email`, `expires_at`, `used_at`); `session` (or signed-cookie-only if we skip server-side sessions).
- `User` gains a non-null `email` column with a unique index. Existing single-user rows in dev databases get a backfilled placeholder during the migration.
- Every `/api/*` route except `/api/auth/*`, `/api/webhooks/*`, and `/api/health` becomes session-gated. Currently no route inspects identity; this is a sweep through `app/api/`.
- Every persistence query that today reads "the one user" must filter by `user_id` derived from the session. This is the largest chunk of multi-user work and the highest source of correctness risk (cross-tenant data leakage). The audit must include `app/services/activity_queries.py`, `app/services/trends.py`, and the coach service's report lookup.
- The Phase 1 HTTP basic-auth middleware is throwaway. It is replaced wholesale by session middleware in Phase 2 and removed.
- A second outbound email type joins the existing coach-report email. The `email_template.py` module gains a `render_login_email` peer to `render_coach_report_email`.
- `request-link` must be rate-limited (per-IP and per-email) or it becomes an email-spam vector. Implementation can lean on Redis (already a dependency) with a counter keyed by `(email, hour)` and `(ip, minute)`.
- Future Garmin / Apple Health integrations slot in as peer side-tables and peer adapters behind a generic integration port. No change to the identity layer.
- Account deletion becomes a real concern the moment a second user exists. Out of scope for this ADR; tracked separately.
