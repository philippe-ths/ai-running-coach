# Custom domain cutover (Phase 2, #124)

Putting a custom domain in front of the frontend (Vercel) and backend (Railway)
so user-visible URLs are stable for the multi-user rollout.

## The one thing to know: this is a config + DNS change, NOT a code change

The codebase carries **no hardcoded production hostnames**. Every URL on the
connection seam comes from an environment variable (audited for #124):

| Surface | Variable | Used for |
|---|---|---|
| Frontend (Vercel) | `BACKEND_URL` | server components + the `/api/[...path]` proxy reach the backend |
| Frontend (Vercel) | `NEXT_PUBLIC_API_BASE_URL` | client `fetchFromAPI` base — **empty in prod**, so browser calls go same-origin through the proxy |
| Backend (Railway) | `APP_BASE_URL` | the Strava OAuth success redirect (`/api/auth/strava/callback` → `${APP_BASE_URL}?connected=true`) and notification deep-links |
| Backend (Railway) | `API_BASE_URL` | the backend's own public base (informational/links) |
| Backend (Railway) | `CORS_ALLOWED_ORIGINS` | browsers allowed to call the backend cross-origin |
| Backend (Railway) | `STRAVA_REDIRECT_URI` | OAuth callback URL registered with Strava |
| Backend (Railway) | `STRAVA_WEBHOOK_CALLBACK_URL` | the webhook subscription callback URL |

So the cutover is: acquire the domain, point DNS at the platforms, and update the
env vars below. No deploy of new code is required for the domain itself.

## How the seam works (why CORS is usually not on the hot path)

```
browser ──> Vercel frontend (custom domain) ──> [server-side proxy: route.ts] ──> Railway backend
                                   client fetch is SAME-ORIGIN ("/api/*")
```

Client-side requests hit the frontend's own origin (`/api/*`) and are proxied to
`${BACKEND_URL}` **server-side**, with the Basic-auth (Phase 1) or Clerk-session
(Phase 2) credentials attached on the server. The browser never talks to the
backend directly, so `CORS_ALLOWED_ORIGINS` is **not** required for the normal
seam — it only matters if a future feature makes a direct browser→backend call.
Set it to the new frontend origin anyway (defensive + correct).

## Cutover checklist (operator actions, outside the working tree)

Assume the domain is `example.com`, the frontend is `app.example.com`, and
(optionally) the backend gets `api.example.com`. The backend does **not** need a
custom public hostname — the frontend reaches it server-side via `BACKEND_URL`,
which can stay the Railway-generated URL. Give the backend its own subdomain only
if you want a clean OAuth/webhook URL; the steps below cover both.

### 1. DNS + platform domains
- [ ] Acquire `example.com`.
- [ ] Vercel → project → Domains: add `app.example.com` (or the apex). Add the
      DNS records Vercel shows at the registrar.
- [ ] (Optional) Railway → backend `web` service → Settings → Networking: add the
      custom domain `api.example.com`; add the CNAME it shows at the registrar.
- [ ] (Phase 2 / Clerk) In the **production** Clerk instance, add the Clerk DNS
      records (CNAMEs for `clerk.example.com` etc.) and add `app.example.com` to
      the allowed origins / set the production sign-in/up redirect URLs. See the
      P2.0 notes — the dev instance `fine-octopus-89` does not need this.

### 2. Vercel env (frontend)
- [ ] `BACKEND_URL` — only change if the backend got a custom hostname
      (`https://api.example.com`); otherwise leave it on the Railway URL.
- [ ] Leave `NEXT_PUBLIC_API_BASE_URL` **empty** so client calls stay same-origin
      through the proxy.
- [ ] (Phase 2) update the Clerk publishable/secret keys to the **production**
      instance keys.
- [ ] Redeploy the frontend so the new env is picked up.

### 3. Railway env (backend `web` AND `worker` — vars are per-service)
- [ ] `APP_BASE_URL=https://app.example.com` (OAuth success redirect + notification links).
- [ ] `API_BASE_URL=https://api.example.com` (or the Railway URL if no backend subdomain).
- [ ] `CORS_ALLOWED_ORIGINS=https://app.example.com` (comma-add any extra origins, e.g. `https://www.example.com`).
- [ ] `STRAVA_REDIRECT_URI=https://api.example.com/api/auth/strava/callback`
      (use the backend's public hostname — Railway URL if no subdomain).
- [ ] `STRAVA_WEBHOOK_CALLBACK_URL=https://api.example.com/api/webhooks/strava`.
- [ ] (Phase 2) production Clerk `CLERK_*` keys / JWKS URL.
- [ ] Redeploy both services.

### 4. Strava app settings (external, operator)
- [ ] Strava API app → set the **Authorization Callback Domain** to the backend's
      public hostname (`api.example.com` or the Railway host).
- [ ] Re-register the webhook subscription against the new
      `STRAVA_WEBHOOK_CALLBACK_URL` (delete the old subscription, create the new
      one), then set `STRAVA_WEBHOOK_SUBSCRIPTION_ID` to the new id on Railway.

## Verification (after the env is set and both services redeploy)
- [ ] `GET https://api.example.com/api/health` (or via the frontend) returns 200.
- [ ] `https://app.example.com` loads the dashboard.
- [ ] Strava **Connect** round-trips: the OAuth redirect returns to
      `https://app.example.com?connected=true` and an account links.
- [ ] A new Strava activity delivers a webhook and a coach report/receipt arrives.
- [ ] (If any direct browser→backend call exists) a CORS preflight from
      `app.example.com` is allowed.

## Rollback
Revert the env vars to the platform-generated URLs and re-register the Strava
callback domain/webhook to the old host. No code is involved, so rollback is a
config change on both platforms.
