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
`PREFLIGHT_FORCE=1`.

### Why it is not a CI gate

CI in GitHub cannot block the Railway deploy (Railway auto-deploys on the merge
commit, independent of GitHub Actions) and cannot see Railway's per-service env
anyway. The preflight only has authority where the env actually lives: as the
**Railway release command**. The script's logic is covered by
`backend/tests/test_preflight_env_check.py` in the normal `backend-test` run, so the
gate itself can't silently rot; the post-deploy verification job (#550) remains the
automated after-the-fact catch.

### Owner actions to activate (Railway dashboard config, not in-repo)

The script ships inside the runtime image (the Dockerfile copies `scripts/`), so it
runs as each service's **Pre-Deploy Command** — Railway runs that after the build and
before the new version takes traffic, and a non-zero exit fails the deploy. Env is
per-service on Railway, so each service passes its own scope (`web` checks the
fail-closed HTTP gates + the DB; `worker` checks only `DATABASE_URL`).

1. **Verify the platform assumption first** (the #546 caveat). On a throwaway/staging
   service, set the Pre-Deploy Command and deliberately unset one required var, then
   confirm Railway **fails the deploy and keeps the previous version serving** when
   the command exits non-zero — rather than removing the old one the way a *boot
   crash* did in #546. A Pre-Deploy Command failure is documented to halt promotion,
   but it is the same "platform keeps the old deploy" assumption, so prove it before
   relying on it.
2. **Set the Pre-Deploy Command** per service (Railway → service → Settings → Deploy
   → Pre-Deploy Command):
   - `web` service:    `python -m scripts.preflight_env_check --scope web`
   - `worker` service: `python -m scripts.preflight_env_check --scope worker`

   Put it ahead of (or alongside) `alembic upgrade head` if that also runs as a
   pre-deploy step; order it first so a missing env fails before any migration runs.
3. Sanity-check locally any time with `PREFLIGHT_FORCE=1 make preflight-env-check`
   (forces the check outside production against the current shell env).

## Deferred / open

- **A real staging environment** (its own backend + DB, not sharing prod) is the
  longer-term fix for the no-isolation gap and is out of scope here.
