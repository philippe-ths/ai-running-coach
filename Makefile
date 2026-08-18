.PHONY: voice-probe voice-probe-selftest smoke backend-smoke frontend-smoke test backend-test frontend-test seed-local coach-review eval eval-selftest eval-memory eval-memory-selftest diagram-check alembic-check verify-local deployed-handshake-smoke post-deploy-verify preflight-env-check

# Prefer the project venv interpreter when it exists, fall back to bare python.
# Path is relative to backend/ since the targets cd there first. CI has no
# venv, so this resolves to `python` and matches the global install (issue #101).
BACKEND_PY := $(shell [ -x backend/.venv/bin/python ] && echo .venv/bin/python || echo python)

smoke: backend-smoke frontend-smoke

backend-smoke:
	cd backend && $(BACKEND_PY) -m pytest tests/test_smoke.py

frontend-smoke:
	cd frontend && npm run smoke

test: backend-test frontend-test

backend-test:
	cd backend && $(BACKEND_PY) -m pytest -m "not integration"

frontend-test:
	cd frontend && npm run test

# Seed the local DB from the production snapshot so local testing uses real data
# without a Strava OAuth link. Resets the local schema, then copies a snapshot
# with Strava tokens REDACTED by default. Requires a Railway project token at
# ~/.railway_token. Pass extra flags via SEED_ARGS, e.g.:
#   make seed-local SEED_ARGS="--activities 20"
#   make seed-local SEED_ARGS="--with-live-tokens"   # only to test real sync
seed-local:
	cd backend && \
	TOK="$$(tr -d '[:space:]' < $$HOME/.railway_token)" && \
	SRC="$$(RAILWAY_TOKEN="$$TOK" railway variables --service Postgres --kv | grep '^DATABASE_PUBLIC_URL=' | cut -d= -f2-)" && \
	SEED_SOURCE_URL="$$SRC" $(BACKEND_PY) scripts/seed_from_prod.py $(SEED_ARGS)

# Coach feedback loop: pull the latest coach reports + conversations from prod
# (READ-ONLY) and render a review HTML under docs/audit/. Same Railway-token
# source as seed-local. Pass flags via REVIEW_ARGS, e.g.:
#   make coach-review REVIEW_ARGS="--activities 40"
# To re-render with reviewer notes baked in (no re-pull), point --from-json at the
# snapshot and --notes at the notes file:
#   make coach-review REVIEW_ARGS="--from-json docs/audit/coach-review-<date>.json \
#       --notes docs/audit/coach-review-notes-<date>.json"
# (paths relative to backend/, so prefix ../ for repo-root paths)
coach-review:
	cd backend && \
	TOK="$$(tr -d '[:space:]' < $$HOME/.railway_token)" && \
	SRC="$$(RAILWAY_TOKEN="$$TOK" railway variables --service Postgres --kv | grep '^DATABASE_PUBLIC_URL=' | cut -d= -f2-)" && \
	SEED_SOURCE_URL="$$SRC" $(BACKEND_PY) scripts/coach_review_export.py $(REVIEW_ARGS)

# Bring up the seeded local stack UNGATED for browser verification (#488), so an
# agent or a quick local check can drive real read paths without a Clerk sign-in.
# Backend runs with LOCAL_NO_AUTH=true (degrades to the single seeded user) and
# the frontend with NEXT_PUBLIC_LOCAL_NO_AUTH=true (Clerk gate off); both are
# refused in production, so this is no prod bypass. Seed first: `make seed-local`,
# and have docker-compose Postgres/Redis up. Ctrl-C stops both. The rq worker is
# NOT started (read-path/UI verification); run it separately to exercise jobs.
verify-local:
	@echo "Seeded ungated stack (#488) -> backend :8000, frontend :3000. Ctrl-C stops both."
	( cd backend && LOCAL_NO_AUTH=true APP_ENV=local $(BACKEND_PY) -m uvicorn app.main:app --port 8000 ) & \
	BACK_PID=$$!; \
	trap "kill $$BACK_PID 2>/dev/null" EXIT INT TERM; \
	( cd frontend && NEXT_PUBLIC_LOCAL_NO_AUTH=true npm run dev )

# Drift guard for BOTH data-flow diagrams: fails if a CoachContextPack section or a
# DerivedMetric column has no representation in flow-nodes.js, or if the conversational
# coach's declared surface (its tools, skills, proposed actions, screen keys, prompt slots
# and baseline sections) has moved without coach-chat-nodes.js being regenerated (#855).
# Also runs automatically in the backend-test suite (backend/tests/test_diagram_drift.py).
diagram-check:
	cd backend && $(BACKEND_PY) ../docs/diagrams/check_diagram_drift.py

# Model/migration drift guard (#839). Walks the migration history onto the
# database DATABASE_URL points at, then diffs the result against the model
# metadata. `make backend-test` is structurally blind to this: the suite builds
# its schema with `create_all` from the models, so a model edit with no
# migration passes every test and fails the deploy instead. Runs in CI as the
# `alembic-check` job against a throwaway Postgres; locally it uses whatever
# backend/.env points at (docker-compose Postgres on :5433), where the upgrade
# is a no-op once that database is already at head.
# Run the coach Voices against real stored baselines and write the result where
# a human can read it (#828). Needs a seeded DB (`make seed-local`) and an API
# key; nothing is regenerated, so the cost is one cheap voice-lane call per
# (case, voice) pair. Pass flags via PROBE_ARGS, e.g.:
#   make voice-probe PROBE_ARGS="--voice roast --report-id <uuid>"
voice-probe:
	cd backend && $(BACKEND_PY) scripts/probe_voice.py $(PROBE_ARGS)

# Grade the harness against scripted outcomes. No DB and no API key, so this is
# safe in CI -- and it is where the harness's own reporting is PROVED rather
# than observed passing.
voice-probe-selftest:
	cd backend && $(BACKEND_PY) scripts/probe_voice.py --self-test

alembic-check:
	cd backend && $(BACKEND_PY) -m alembic upgrade head
	cd backend && $(BACKEND_PY) -m alembic check

# Offline coach-report eval harness (M5) — THE GATE for the learning milestones.
# Scores the coach reports in the local DB against the deterministic rubric and
# prints a repeatable scorecard. Seed real data first: `make seed-local`. Pass
# extra flags via EVAL_ARGS, e.g.:
#   make eval EVAL_ARGS="--output before.json"
#   make eval EVAL_ARGS="--compare before.json"          # flag regressions
#   make eval EVAL_ARGS="--regenerate --activities 20"   # needs ANTHROPIC_API_KEY
eval:
	cd backend && $(BACKEND_PY) -m scripts.eval_coach_reports $(EVAL_ARGS)

# Validate the harness itself against its synthetic good/bad fixtures. No DB and
# no API key required, so this is safe to run in CI.
eval-selftest:
	cd backend && $(BACKEND_PY) -m scripts.eval_coach_reports --self-test

# Offline runner-memory eval harness (#658) — the durable-memory counterpart to
# `eval`. Scores memory writes against ADR 0025 rubric assertions.
#   make eval-memory EVAL_MEMORY_ARGS="--scan"   # score stored profiles (needs DB)
eval-memory:
	cd backend && $(BACKEND_PY) -m scripts.eval_runner_memory $(EVAL_MEMORY_ARGS)

# Validate the runner-memory rubric against its synthetic good/bad fixtures. No DB
# and no API key required, so this is safe to run in CI.
eval-memory-selftest:
	cd backend && $(BACKEND_PY) -m scripts.eval_runner_memory --self-test

# Deployed-only smoke for the Strava + Telegram handshake auth gates (#540).
# NOT a CI gate: needs a live deployment (real APP_ENV/secrets). Non-mutating —
# it only asserts the live gates reject unauthentic input; it never completes a
# real OAuth callback or sends a real /start/tap. The session-gated positive
# checks stay in the manual runbook (docs/testing/deployed-handshake-verification.md).
#   make deployed-handshake-smoke SMOKE_BASE_URL=https://<deployed-backend>
#   make deployed-handshake-smoke SMOKE_BASE_URL=... SMOKE_TELEGRAM_WEBHOOK_SECRET=<secret>
deployed-handshake-smoke:
	cd backend && $(BACKEND_PY) -m scripts.deployed_handshake_smoke

# Post-deploy verification (#550): poll the deployed /api/health until it is
# healthy, then run the deployed handshake smoke as a release smoke. The release
# gate that catches a crashed/regressed deploy (the #546 outage) automatically
# instead of waiting for someone to notice the platform's crash email. Run after
# a deploy (the CI post-deploy-verify job on push to main does this) or by hand.
#   make post-deploy-verify SMOKE_BASE_URL=https://<deployed-backend>
post-deploy-verify:
	cd backend && $(BACKEND_PY) -m scripts.post_deploy_verify

# Pre-deploy required-env preflight (#551): exit non-zero when a production deploy
# is missing a required env var, BEFORE the process boots. Meant to run as the
# Railway release command (in the deploy environment, where the vars live) so a
# misconfigured release fails the deploy instead of crash-looping on boot and
# taking prod down (the #546 failure mode). A no-op outside production unless
# PREFLIGHT_FORCE=1. See docs/deployment/deploy-checklist.md for the wiring.
#   APP_ENV=production make preflight-env-check          # the release-command form
#   PREFLIGHT_FORCE=1 make preflight-env-check           # dry run against this env
preflight-env-check:
	cd backend && $(BACKEND_PY) -m scripts.preflight_env_check
