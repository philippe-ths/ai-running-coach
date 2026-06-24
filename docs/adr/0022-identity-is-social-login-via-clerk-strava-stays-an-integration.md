# Identity is social login via Clerk; email is the durable key; data sources stay integrations

This supersedes the *mechanism* of [ADR 0005](0005-magic-link-is-identity-strava-is-an-integration.md) while keeping its *principle*. ADR 0005's principle stands unchanged: identity lives at the email level, and Strava (with Garmin Connect and Apple Health as planned peers) is a connected integration, not the identity. What changes is how a user proves that email.

## Why ADR 0005's mechanism no longer fits

ADR 0005 specified an in-house magic-link flow and explicitly delivered the link by reusing `SMTPNotifier`. The deployment stack has since moved to Railway, and **Railway blocks outbound SMTP** from the app services. That is not a minor detail: it is the same constraint that forced coach-report notifications off email and onto Telegram (#127). ADR 0005's delivery mechanism does not exist on the current stack, so the in-house magic-link plan cannot ship as written without first adding an HTTP email API anyway.

Re-opening the choice surfaced two standing constraints that now pull against ADR 0005's "build it ourselves, no managed provider" stance:

- The owner's stated priority is **"as easy as possible AND secure."** Hand-rolling auth (token tables, session crypto, rate limiting, CSRF, account recovery) is the canonical surface where "looks easy" hides a security minefield. It is the single highest-risk thing in the multi-user effort, alongside tenant isolation, and it is exactly the kind of work a managed provider exists to take off our hands.
- **Per-user LLM cost caps are the owner's #1 going-live concern** (see `docs/vision/going-live-cost-control.md`) and they cannot exist until per-user identity does. Whatever ships must put a reliable `user_id` on every request, not a fragile bespoke session layer.

The initial audience is friends/beta (a handful of known users), which removes any need for a heavyweight in-house identity build and argues for the lowest-friction option that is still secure.

## Decision

Identity is **social login through Clerk**, a managed auth provider integrated at the Vercel/Next.js layer.

- **One mechanism: social login, starting with Google only.** No magic-link, no passwords, no SMTP. One button, no inbox round-trip, the lowest-friction UX for a beta and the smallest security surface we own. A single mechanism keeps one identity per person and one code path.
- **The durable identity key is the verified email, not the social provider's user id.** This is precisely ADR 0005's identity model ("subsequent verifies match by email"). Keying on the verified email means we are not locked to Google: adding Apple, or even an email magic-link later, links to the same account, and Garmin-first / Apple-first users remain expressible without a Strava account. The principle of ADR 0005 is preserved; only the proof-of-email mechanism changes.
- **Data sources stay peer integrations.** `StravaAccount` today; `GarminAccount`, `AppleHealthAccount` later. Garmin/Apple are confirmed still on the roadmap, so identity must remain decoupled from any one source. Social login authenticates the *person*; it does not collapse identity into a data source the way "sign in with Strava" would. This is why we still reject Strava-as-identity.

### Why Clerk, and the trust boundary

We choose **Clerk** over rolling our own and over Auth.js (NextAuth):

- Drop-in Next.js components, social providers configured in a dashboard, hosted session management, CSRF and account-recovery handled, a free tier that comfortably covers a beta, and a verifiable JWT for the backend.
- The accepted cost is a vendor dependency and a bill at real scale. **Auth.js is the documented fallback** if we later want to drop the vendor and keep auth data in our own Postgres; it runs in the Next.js app and is free, at the price of wiring session storage and the backend token check ourselves. Either way the rest of this ADR (email-keyed identity, integrations-as-peers, the isolation sweep) is unchanged.

Trust boundary, exploiting the existing topology where the browser never talks to the backend directly:

- The Vercel proxy already fronts the Railway backend and injects a server-side credential. It now also forwards the Clerk session token.
- **The FastAPI backend verifies that token itself, via Clerk's JWKS**, and derives `user_id` from the verified email. The backend stays authoritative on identity and never blindly trusts an unsigned header. This matters because the webhook routes are publicly exposed.
- The existing HTTP Basic credential is **retained as the service-to-service secret** between Vercel and Railway (defense in depth), not as the identity. `BasicAuthMiddleware`'s role narrows from "proves it is the one user" to "proves it is our frontend"; per-user identity rides on top via the verified token.

This split is the security win: Clerk owns the auth-provider attack surface (credentials, sessions, social OAuth, recovery); we own only token verification and tenant isolation.

## Consequences

- **Clerk setup (operator action):** a Clerk application, Google OAuth credentials wired into it, and new env vars (publishable key on the frontend; secret key and the JWKS/issuer config on the backend). Documented in `topology.md` when the code lands.
- **`User` gains a non-null `email` with a unique index** (alembic migration). The existing single prod/dev user gets a backfilled placeholder, then is reconciled to the owner's real Google email on first sign-in by email match.
- **Backend session verification dependency** (JWKS-based) replaces auto-create-on-first-read in `app/api/profile.py`. Every `/api/*` route except `/api/auth/*`, `/api/webhooks/*`, and `/api/health` resolves `user_id` from the verified token.
- **The tenant-isolation sweep is unchanged and remains the largest, highest-risk chunk** (#119): every query that reads "the one user" must filter by the authenticated `user_id`. Cross-tenant leakage is the top correctness risk, identical under any auth choice.
- **Per-user notification routing becomes a real design item, not a swap of `NOTIFY_TO`.** The deployed channel is Telegram, bound to one `TELEGRAM_CHAT_ID` for the whole deployment. Multi-user routing needs a per-user channel binding (each user links their own Telegram chat, or we add the HTTP email API ADR 0005 assumed). Tracked in #120; not resolved by this ADR.
- **Account deletion (#121) must also delete the Clerk user**, not only cascade local rows, so a deleted account cannot sign back in into an empty shell.
- **`BasicAuthMiddleware` is not removed**, contrary to ADR 0005; it is repurposed as the frontend-to-backend service secret. The `TODO(phase-2): remove` in `app/core/auth.py` becomes "repurpose."
- **[ADR 0006](0006-multi-user-drops-polling-for-user-triggered-self-healing.md) is unaffected.** It is a Strava-rate-limit decision independent of how users authenticate.
- **Future Garmin / Apple Health integrations slot in as peer side-tables behind the integration port**, with no change to the identity layer. This is the property ADR 0005 was protecting, and social-login-by-email keeps it.
