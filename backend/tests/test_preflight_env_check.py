"""Pre-deploy required-env preflight (#551).

The preflight is the release-command gate that exits non-zero BEFORE cutover when
a production deploy is missing a required env var, so the deploy fails while the
old one keeps serving instead of crash-looping on boot (the #546 failure mode).
These tests pin the firing condition against a synthetic environment: enforce in
production (or when forced) and fail on any missing var, stay a no-op otherwise.
"""

from app.core.required_env import REQUIRED_FOR_PRODUCTION_DEPLOY, missing_env
from scripts.preflight_env_check import check

# Clearly-fake values; never real secrets (aiw-security-testing rule 6).
_COMPLETE_PROD_ENV = {
    "APP_ENV": "production",
    "DATABASE_URL": "postgresql://test-do-not-use/preflight",
    "CLERK_JWKS_URL": "https://example-test.clerk.accounts.dev/.well-known/jwks.json",
    "BASIC_AUTH_USER": "svc-test-do-not-use",
    "BASIC_AUTH_PASSWORD": "test-secret-do-not-use-in-prod",
}


def test_noop_outside_production_even_when_all_unset():
    # Local dev and the test suite run with every required var unset and degrade;
    # the preflight must stay silent there, or it would block every non-prod run.
    code, message = check({"APP_ENV": "local"})
    assert code == 0
    assert "skipped" in message


def test_passes_in_production_when_fully_configured():
    code, message = check(_COMPLETE_PROD_ENV)
    assert code == 0
    assert "OK" in message


def test_forced_check_enforces_outside_production():
    # PREFLIGHT_FORCE makes the check run even when APP_ENV is not production, so a
    # dry run / self-test can exercise the failing path.
    code, message = check({"APP_ENV": "local", "PREFLIGHT_FORCE": "1"})
    assert code == 1
    assert "FAILED" in message


def test_fails_in_production_when_clerk_unset():
    env = dict(_COMPLETE_PROD_ENV)
    env["CLERK_JWKS_URL"] = ""
    code, message = check(env)
    assert code == 1
    assert "CLERK_JWKS_URL" in message


def test_fails_in_production_when_database_url_unset():
    env = dict(_COMPLETE_PROD_ENV)
    del env["DATABASE_URL"]
    code, message = check(env)
    assert code == 1
    assert "DATABASE_URL" in message


def test_error_lists_every_missing_var():
    code, message = check({"APP_ENV": "production"})
    assert code == 1
    for name in REQUIRED_FOR_PRODUCTION_DEPLOY:
        assert name in message


def test_whitespace_only_value_counts_as_missing():
    # A fat-fingered blank secret must be treated as unset (boundary on .strip()).
    env = dict(_COMPLETE_PROD_ENV)
    env["BASIC_AUTH_PASSWORD"] = "   "
    code, message = check(env)
    assert code == 1
    assert "BASIC_AUTH_PASSWORD" in message


def test_missing_env_helper_treats_blank_and_absent_alike():
    env = {"A": "x", "B": "", "C": "   "}
    assert missing_env(["A", "B", "C", "D"], env) == ["B", "C", "D"]
