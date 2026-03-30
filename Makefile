.PHONY: test backend-test frontend-test

test: backend-test frontend-test

backend-test:
	cd backend && python -m pytest -m "not integration"

frontend-test:
	cd frontend && npm run test
