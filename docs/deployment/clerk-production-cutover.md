# Clerk production-instance cutover (#626)

Production authenticates against a Clerk **development** instance
(`fine-octopus-89`). This is the runbook for moving it to a **production**
instance.

Every step here is an operator action on an external dashboard or a platform env
var. **No code change is involved** — the app reads its Clerk configuration
entirely from env (`CLERK_JWKS_URL`, `CLERK_SECRET_KEY`, `CLERK_ISSUER`,
`CLERK_AUTHORIZED_PARTIES` on the backend; `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`,
`CLERK_SECRET_KEY` on the frontend), so this cutover and its rollback are both
config changes.

## Why this matters, and why it is not urgent

A Clerk dev instance serves its account UI from a *different site* than the app
(`fine-octopus-89.accounts.dev` vs `pulsecoachai.com`) and stitches the session
across those origins with a `__clerk_db_jwt` token in the URL, because it cannot
set a shared first-party cookie across two unrelated domains. When that
cross-origin handoff does not complete on a **fresh signup** — new device, new
browser, social-login round trip, unrecognised redirect target — Clerk strands
the authenticated user on its own `accounts.dev` page with the banner
*"Development mode. You are signed in, but Clerk cannot redirect to your
application."* That is the failure a beta user hit on 2026-07-02.

It does not reproduce for anyone who already holds a session cookie, which is why
it reads as "works for me". Dev instances also cap at roughly 100 users and use
Clerk's shared OAuth credentials rather than the app's own.

The owner has accepted this up to ~100 signups, so this is a **tracker, not a
blocker**. The prerequisite that used to gate it — a custom domain, since Clerk
production requires DNS records on a domain you control and cannot run on
`*.vercel.app` — was met on 2026-07-16 when pulsecoachai.com went live.

## Before you start

- Domain: **pulsecoachai.com** (apex canonical → Vercel, `www` 307 → apex).
- The backend deliberately stays on its Railway-generated URL, so **Strava
  callbacks are untouched by this cutover**. Do not change
  `STRAVA_REDIRECT_URI` or `STRAVA_WEBHOOK_CALLBACK_URL`.
- Railway env vars are **per-service**. Clerk vars go on **both** `web` and
  `worker`.
- Have registrar access (Cloudflare) for the Clerk DNS records.

## 1. Create the production instance (Clerk dashboard)

- [ ] Create a **production** instance for pulsecoachai.com.
- [ ] Add the DNS records Clerk shows (CNAMEs for `clerk`, `accounts`,
      `clkmail`, and the two DKIM records) at Cloudflare. These must be
      **DNS-only, not proxied** — an orange-cloud proxy breaks Clerk's
      certificate issuance.
- [ ] Wait for Clerk to report the domain verified before going further.
- [ ] Reconfigure social login with **the app's own OAuth credentials**. A dev
      instance borrows Clerk's shared ones; a production instance will not start
      until you supply your own for each provider you offer.
- [ ] Set the sign-in / sign-up / after-sign-in URLs to the app's routes
      (`/sign-in`, `/sign-up`, `/`) and add `https://pulsecoachai.com` to the
      allowed origins.

Note that **users do not migrate between instances**. Anyone signed up against
the dev instance will be signing up fresh. Since the app resolves identity by
verified email (ADR 0022), a returning user who signs in with the same email
lands back on their existing rows — but check the owner account works before
announcing anything.

## 2. Swap the frontend env (Vercel)

- [ ] `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` → `pk_live_…`
- [ ] `CLERK_SECRET_KEY` → `sk_live_…`
- [ ] Leave `NEXT_PUBLIC_CLERK_SIGN_IN_URL` / `_SIGN_UP_URL` / `_AFTER_SIGN_IN_URL`
      as they are.
- [ ] Leave `NEXT_PUBLIC_API_BASE_URL` **empty** so client calls stay same-origin
      through the proxy.
- [ ] Redeploy.

## 3. Swap the backend env (Railway — `web` AND `worker`)

- [ ] `CLERK_JWKS_URL` → the production instance's JWKS URL
      (`https://clerk.pulsecoachai.com/.well-known/jwks.json`).
- [ ] `CLERK_SECRET_KEY` → `sk_live_…`
- [ ] `CLERK_ISSUER` — optional. The code derives it from `CLERK_JWKS_URL` by
      stripping `/.well-known/...`, which is correct for every Clerk instance.
      Set it only if you want it pinned explicitly.
- [ ] `CLERK_AUTHORIZED_PARTIES` — optional, and **already armed without it**.
      When unset the `azp` allowlist is derived from `CORS_ALLOWED_ORIGINS` plus
      `APP_BASE_URL` (`clerk_authorized_parties_list` in `core/config.py`), so a
      token minted by the same Clerk instance for a different frontend origin is
      already rejected. Set it explicitly only to narrow the list further.
- [ ] Redeploy both services.

**Safety property worth knowing (#480):** in production the `web` process
refuses to boot when `CLERK_JWKS_URL` is unset, crashing with a
`production_config_incomplete` CRITICAL log. So a half-applied swap crash-loops
and Railway keeps the previous healthy deploy serving, rather than promoting one
that 503s every route. If web crash-loops right after this cutover, check that
var first.

## 4. Verify — the step the readiness audit skipped

The 2026-07-01 multi-user readiness audit asserted "Clerk auth is sound" from a
read-only code review and missed this entirely, because the bug lives purely in
deployed config. Do not repeat that: the acceptance bar is a real signup on the
live environment, not a green build.

- [ ] From a **clean incognito browser on a device that has never signed in**,
      complete a fresh social **sign-up** at pulsecoachai.com. It must return
      into the app, not to a Clerk Account Portal.
- [ ] No "Development mode" banner anywhere in the flow.
- [ ] Repeat with a **second brand-new account**. One success can be a warm
      cache; two is a signal.
- [ ] Sign out and back in on the owner account; confirm existing activities and
      reports still resolve (identity is the verified email, so they should).
- [ ] Confirm the Telegram `/start` deep link still binds — it follows
      `APP_BASE_URL`, which this cutover does not change, so this is a
      regression check rather than a change.
- [ ] `grep -rn "accounts.dev\|pk_test_\|sk_test_"` across the deployed env to
      confirm nothing still points at the dev instance.

## 5. After it is live

- [ ] Update `docs/deployment/topology.md` to say prod runs a Clerk **production**
      instance (it currently records the dev instance as a known gap).
- [ ] Close #626.
- [ ] Optionally delete the `fine-octopus-89` dev instance, or keep it for local
      development — `backend/.env` and `frontend/.env.local` point at it and are
      gitignored, so local is unaffected either way.

## Rollback

Put the `pk_test_` / `sk_test_` keys and the `fine-octopus-89` JWKS URL back on
Vercel and both Railway services, and redeploy. No code is involved. Sessions
minted by the production instance stop validating, so everyone is signed out
once — expected, not a failure.

## Secrets note (a withdrawn finding, recorded so it is not re-raised)

An earlier pass claimed live Clerk keys were committed to the repo. That was
checked and does not hold: `backend/.env` and `frontend/.env.local` are
gitignored and untracked, and `git log --all -S` finds nothing. Rotating the dev
key is optional hygiene only, and the dev instance is being retired here anyway.
