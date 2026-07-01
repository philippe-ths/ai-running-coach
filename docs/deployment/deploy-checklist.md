# Deploy checklist & config-ordering discipline

Merging to `main` auto-deploys to production: Railway redeploys the backend `web`
and `worker` services and Vercel redeploys the frontend, with no manual promote
step and no isolated staging in between (preview deploys point at the same prod
backend/Postgres/Redis, so they are not a safety net — see
`docs/testing/local-seed.md`). This file is the lightweight discipline that keeps
a config-dependent change from racing its own deploy. It exists because of the
#546 incident (2026-06-27): a change that needed an env var set *first* merged
before the env was set, and prod went down.

This is the process half of the mitigation (#551). The automated half is the
post-deploy verification job (#550, `make post-deploy-verify` / the CI
`post-deploy-verify` job), which catches a crashed/regressed deploy after the
fact. This checklist is about not shipping the bad config in the first place.

## The rule

> If a change needs a new or changed environment variable to run correctly, the
> env must be set on every affected service **before** the PR merges — never
> "merge, then set the env." On Railway a bad boot is a full outage, not a
> blocked deploy (the platform removed the prior healthy deploy in #546), so the
> safe ordering is: set env → confirm → merge.

## Per-PR checklist (for any config-dependent change)

When a PR adds, renames, removes, or changes the required value of an env var,
the PR description must state it, and the author must do the ordering:

- [ ] **List the env changes** in the PR body: var name, which service(s) it
  belongs on (`web`, `worker`, both, or Vercel — env is per-service, see
  `project-context.md` / `docs/deployment/topology.md`), and the value or how to
  derive it. Never the secret value itself in the PR.
- [ ] **Classify the failure mode if the env is missing at deploy time:**
  - *Crashes the boot* (a settings guard, a required `Settings` field with no
    default) → this is the dangerous class. Set the env on all affected services
    and confirm before merge, or make the change non-fatal first (see below).
  - *Degrades but boots* (a feature flag, an optional integration) → lower risk,
    but still set it before merge so the first prod run is correct.
- [ ] **Set the env on every affected service first**, on Railway `web` and
  `worker` (and Vercel if frontend) — then confirm it is present.
- [ ] **Only then merge.** After the deploy, confirm health: the
  `post-deploy-verify` CI job runs automatically, or run
  `make post-deploy-verify SMOKE_BASE_URL=<prod-backend>` by hand.

## Prefer non-fatal config handling

The #546 root cause was a crash-on-boot guard for a setting prod runs *fine*
without (a missing cost cap = uncapped spend, not a broken app). The fix (#549)
replaced it with a non-fatal safe default. Apply the same preference for new
config:

- For a setting prod can run without, prefer a **safe default** (a generous
  backstop, a degrade-to-fallback) plus a loud log over crashing the boot. See
  `app/services/coach/budget.py` (`production_default_ceiling`) and
  `app/core/observability.py` (`log_budget_cap_status`) for the pattern.
- Reserve crash-on-boot for settings without which the app is genuinely broken
  anyway (every route 503s), where booting buys nothing — and even then know
  that on Railway it means an outage, not a blocked promotion. See
  `assert_production_config`'s scope note.

## Build-time required-env preflight (#551)

`scripts/preflight_env_check.py` (`make preflight-env-check`) is the release-command
gate: run in the deploy environment it exits non-zero when a required production env
var is unset, *before* the process boots. The required list is the canonical one in
`app/core/required_env.py` — the same `CLERK_JWKS_URL` / `BASIC_AUTH_USER` /
`BASIC_AUTH_PASSWORD` the boot guard `assert_production_config` enforces, plus
`DATABASE_URL` — so a release missing one fails the deploy instead of crash-looping
on boot (the #546 failure mode). It is a no-op outside production unless
`--require-production` is passed (the release-command form, below) or
`PREFLIGHT_FORCE=1` is set.

### Why it is not a CI gate

CI in GitHub cannot block the Railway deploy (Railway auto-deploys on the merge
commit, independent of GitHub Actions) and cannot see Railway's per-service env
anyway. The preflight only has authority where the env actually lives: as the
**Railway release command**. The script's logic is covered by
`backend/tests/test_preflight_env_check.py` in the normal `backend-test` run, so the
gate itself can't silently rot; the post-deploy verification job (#550) remains the
automated after-the-fact catch.

### Platform assumption — VERIFIED (2026-06-27)

The #546 caveat is resolved. On a throwaway `preflight-test` service (mirroring web:
same repo, Root Directory `/backend`, Dockerfile builder, Pre-Deploy Command set,
`APP_ENV=production` + `DATABASE_URL` set but `CLERK_JWKS_URL` left unset), a failing
Pre-Deploy Command behaved exactly as needed: the deployment was marked **FAILED**
(during deploy, before traffic), while the previously-successful deployment stayed
**ACTIVE** and the service stayed **Online**. So unlike a *boot crash* (#546, which
removed the old deploy and took prod down), a failing **Pre-Deploy Command** does NOT
disturb the already-serving version — the gate is safe to rely on for blocking.

### Owner actions to activate (Railway dashboard config, not in-repo)

The script ships inside the runtime image (the Dockerfile copies `scripts/`), so it
runs as each service's **Pre-Deploy Command** (Railway → service → Settings → Deploy →
Pre-Deploy Command). As of #593 the wired Pre-Deploy Command is `scripts.pre_deploy`
(which runs this preflight first, then optionally migrates — see
"Auto-applied migrations" below), not `scripts.preflight_env_check` directly. The
preflight logic and per-scope wiring are unchanged; only the entrypoint moved.

Sanity-check the preflight locally any time with `PREFLIGHT_FORCE=1 make preflight-env-check`.

## Auto-applied migrations on deploy (#593, fixes #586)

Railway production deploys do NOT auto-run Alembic migrations, so code could ship
ahead of its schema and 500 for every user until someone migrated by hand (#586).
`scripts/pre_deploy.py` (`python -m scripts.pre_deploy`) closes that gap: it runs
the #551 env preflight FIRST (fail-fast — a failing preflight exits non-zero and
migrations never run, so the previous deploy keeps serving), then, only when the
`RUN_MIGRATIONS` env flag is truthy, applies `alembic upgrade head` in-process.

The `RUN_MIGRATIONS` gate keeps migrations to ONE service so the two never migrate
concurrently: **set `RUN_MIGRATIONS=true` on the `web` service only; leave it unset
on `worker`.** With the flag off the script is the plain preflight (worker
behaviour). `RUN_MIGRATIONS` is read straight from the deploy env (an ops flag, not
in `app/core/config.py`).

Wire the Pre-Deploy Command on each service (env is per-service on Railway, so each
passes its own scope; `--require-production` keeps the gate armed — see the gotcha
below). The migration flag is what differs, not the command:

- `web` service:    `python -m scripts.pre_deploy --scope web --require-production`
  with `RUN_MIGRATIONS=true` set on the service.
- `worker` service: `python -m scripts.pre_deploy --scope worker --require-production`
  with `RUN_MIGRATIONS` unset.

This supersedes the old `... && alembic upgrade head` chaining idea: ordering
(preflight before migrate) and fail-fast are now built into the script.

### Two gotchas found while wiring it (2026-06-27)

- **`--require-production` is not optional, it is the point.** Without it, the gate
  only enforces when `APP_ENV=production`; an empty/missing `APP_ENV` makes it
  silently **skip** (exit 0) and provide no protection. That is not hypothetical:
  Railway's *inline* variable editor was observed saving `APP_ENV` with an **empty
  value**, which silently disarmed the gate (and would equally disarm the boot guard
  and the production auth path). `--require-production` turns a non-`production`
  `APP_ENV` into a hard failure, and being a CLI flag baked into the command string it
  cannot be dropped the way a separate env var can. Set env values via Railway's
  **Raw Editor** and re-check them.
- **A new service defaults to Railpack and scans the repo root.** Stand up any service
  (including the throwaway test one) with **Root Directory `/backend`** so the
  Dockerfile is found, matching how `web`/`worker` are configured.

## Deferred / open

- **A real staging environment** (its own backend + DB, not sharing prod) is the
  longer-term fix for the no-isolation gap and is out of scope here.
