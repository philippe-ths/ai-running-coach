.PHONY: smoke backend-smoke frontend-smoke test backend-test frontend-test

smoke: backend-smoke frontend-smoke

backend-smoke:
	cd backend && python -m pytest tests/test_smoke.py

frontend-smoke:
	cd frontend && npm run smoke

test: backend-test frontend-test

backend-test:
	cd backend && python -m pytest -m "not integration"

frontend-test:
	cd frontend && npm run test
