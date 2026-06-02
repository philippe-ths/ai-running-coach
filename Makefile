.PHONY: smoke backend-smoke frontend-smoke test backend-test frontend-test seed-local

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
