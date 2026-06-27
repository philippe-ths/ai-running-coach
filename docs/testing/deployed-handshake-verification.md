# Deployed handshake verification

How to confirm the two external handshakes that cannot be bootstrapped locally:

1. the Strava OAuth `state` round-trip (connect -> callback -> per-user link, #469/#532), and
2. the Telegram `/start <token>` chat-bind and inbound tap authorization (#477/#533).

Neither has a dev/local Strava OAuth app or a local bot, so unit and integration
tests only ever cover the codec + authorization logic, never the real round-trip.
This runbook is the manual checklist that exercises the live flow so a regression
in the deployed handshake is caught rather than only the offline logic. An
automated deployed smoke is a deferred stretch (see "Deferred" at the end).

Run it after any change to `app/api/auth.py`, `app/core/oauth_state.py`,
`app/api/webhooks.py`, `app/services/notifications/telegram_link_token.py`, the
Telegram link endpoints in `app/api/coach.py`, or the Vercel proxy.

## Who can run this

The owner of the deployed stack (`philippe@twohourssleep.com`). It needs a real
Strava account and the live bot, and it WRITES to production (preview deploys
point at the same backend/Postgres/Redis, so there is no isolated environment;
see `docs/testing/local-seed.md`). Treat it as a production smoke, not a CI gate.

## Config preflight

These must be set for the handshakes to work. Ownership is per Railway service
(`docs/deployment/topology.md`); the worker sends outbound, the web service
authenticates inbound.

| Var | Service | Required? | Notes |
| --- | --- | --- | --- |
| `STRAVA_OAUTH_STATE_SECRET` | web | optional | Signs the OAuth `state` (#469). Falls back to `BASIC_AUTH_PASSWORD`, then `CLERK_SECRET_KEY`, so it is secure-by-default without being set. |
| `STRAVA_REDIRECT_URI` | web | yes | Must match the callback URL registered on the Strava API application. |
| `STRAVA_WEBHOOK_SUBSCRIPTION_ID` | web | yes (prod) | Inbound Strava events are 403'd unless `owner_id` maps to a connected account and this matches (when non-zero). |
| `TELEGRAM_BOT_TOKEN` | web + worker | yes | Worker sends; web authenticates inbound and now sends the #533 link confirmation. |
| `TELEGRAM_CHAT_ID` | web + worker | yes | The global owner chat (single-owner back-compat). |
| `TELEGRAM_WEBHOOK_SECRET` | web | yes (prod) | The outer gate on `/api/webhooks/telegram`; fails closed in production when unset. |
| `TELEGRAM_BOT_USERNAME` | web | yes | Builds the `https://t.me/<username>?start=<token>` deep link; the `LinkTelegramButton` is hidden without it (`link-status.configured = false`). |

Quick preflight from a signed-in browser session (DevTools console on the
deployed frontend, so the Clerk session token is attached by the proxy):

```js
await fetch('/api/coach/telegram/link-status').then(r => r.json())
// -> { configured: true, linked: <bool> }   // configured:false => TELEGRAM_BOT_USERNAME missing
await fetch('/api/auth/strava/status').then(r => r.json())
// -> { connected: <bool>, athlete_id, scope, expires_at }
```

## Part A - Strava OAuth state round-trip (#469, #532)

Goal: confirm the connect -> callback flow links the new `StravaAccount` to the
signed-in user (not the first/owner account), and that the status read is scoped
to that user.

1. Sign in to the deployed frontend as the test runner (Clerk session).
2. Read the starting state: `GET /api/auth/strava/status`. Note `connected` and
   `athlete_id` (so you can tell a re-link from a fresh link).
3. Start the connect flow (`/api/auth/strava/login`, the profile "Connect Strava"
   affordance). Confirm the browser navigates to Strava's OAuth consent page and
   that the `state` query param is present and non-empty on that URL.
4. Approve on Strava. Confirm the callback returns you to `APP_BASE_URL?connected=true`.
5. Re-read `GET /api/auth/strava/status`. Expect `connected: true` and the
   `athlete_id` of the Strava account you just authorized.
6. Per-user scope check (#532): if a SECOND runner with their own connected
   Strava account exists, sign in as them and read `GET /api/auth/strava/status`.
   Each runner must see their OWN `athlete_id`/`scope`, never the other's. This
   is the cross-tenant-leak regression the status read used to have.

Pass criteria:
- The OAuth redirect carries a non-empty `state`.
- After the callback the new account is linked to the signed-in user (verify the
  status read reflects the just-authorized athlete id).
- Two runners read two different connection states; neither sees the other's
  athlete id or scope.

Failure signatures:
- No `state` on the redirect -> `strava_login` is not gated on the session, or
  `encode_state` returned empty. Check `STRAVA_OAUTH_STATE_SECRET`/fallbacks.
- Account links to the wrong (owner) user when a second user exists -> the
  callback fell back to the single-owner heuristic, meaning the signed `state`
  was absent/invalid (check the state secret is identical to what minted it).
- Both runners see the same athlete id -> the status read regressed to the
  first-row-globally query (#532).

## Part B - Telegram /start chat-bind + tap authorization (#477, #533)

Goal: confirm a deep link binds the chat to the right user, the in-chat
confirmation (#533) appears, and a bound user's tap acts only on their own
activity while an unbound/cross-user tap is rejected.

1. Sign in as the test runner. From the profile, use `LinkTelegramButton` (or
   `POST /api/coach/telegram/link-token`) to mint the deep link. Confirm the
   response `deep_link` is `https://t.me/<TELEGRAM_BOT_USERNAME>?start=<token>`.
2. Tap the deep link (or send `/start <token>` to the bot from the test runner's
   Telegram chat). Confirm:
   - the bot replies in-chat with the #533 confirmation
     ("I'll send your coach messages and check-in prompts here from now on");
   - `GET /api/coach/telegram/link-status` now reads `linked: true`.
3. Single-use check: tap the SAME deep link again. Expect NO second confirmation
   (the one-time token was already consumed; the webhook silently 200-acks). This
   is the no-spam guarantee.
4. Bound-tap check: trigger a coach receipt/opener for one of the test runner's
   own activities (a real synced run, or wait for the next ingest). Tap an
   RPE/pain/done button in Telegram. Confirm the check-in is recorded and the
   tapped button gets its check mark.
5. Cross-user rejection: if a second bound runner exists, have them attempt to
   tap a button whose token references the FIRST runner's activity (e.g. by
   forwarding/replaying the callback). Expect a silent 200 with no write
   (`reason: not_owner`). An unbound/unknown chat that is not the global owner
   chat is likewise a silent no-op.

Pass criteria:
- The deep link binds the correct user (`link-status.linked` flips to true for
  that runner only).
- A successful bind produces exactly one in-chat confirmation; a repeat `/start`
  produces none.
- A bound runner's tap writes a check-in only for their own activity; a
  cross-user or unbound tap writes nothing and returns 200.

Failure signatures:
- No deep link offered / `link-status.configured: false` -> `TELEGRAM_BOT_USERNAME`
  unset on the web service.
- `/start` does nothing and no confirmation -> either the bind failed (check the
  webhook secret and that the token was minted by the same deployment) or the web
  service has no `TELEGRAM_BOT_TOKEN` to send the confirmation. The bind can
  still succeed while the confirmation send fails (best-effort, #533); check the
  web logs for `telegram_chat_linked` vs `failed to send telegram link confirmation`.
- A non-Telegram caller is NOT rejected with 403 -> `TELEGRAM_WEBHOOK_SECRET`
  unset (it must fail closed in production).

## What to capture

Record, per run: the deployment commit/date, which config vars were present, and
the pass/fail of each numbered step. A regression in the live flow is only caught
if this is run after the relevant changes and the result is written down.

## Deferred

An automated deployed smoke (a script that mints a state, drives a scripted
Strava callback, and replays a `/start` + tap against the live stack) is the
stretch goal in #534. It needs a dedicated test Strava app and a test bot so it
does not mutate the owner's real account; until those exist this manual checklist
is the verification path. Related: #488 (local browser-verification blocker keeps
the frontend affordances from being checked locally).
