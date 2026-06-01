.PHONY: smoke backend-smoke frontend-smoke test backend-test frontend-test

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
