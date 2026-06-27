"""Pre-deploy required-env preflight (#551).

The preflight is the release-command gate that exits non-zero BEFORE cutover when
a production deploy is missing a required env var, so the deploy fails while the
old one keeps serving instead of crash-looping on boot (the #546 failure mode).
These tests pin the firing condition against a synthetic environment: enforce in
production (or when forced) and fail on any missing var, stay a no-op otherwise.
"""

from app.core.required_env import REQUIRED_FOR_PRODUCTION_DEPLOY, missing_env
from scripts.preflight_env_check import _parse_args, check

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


# --- per-service scope (env is per-service on Railway) -------------------------

def test_worker_scope_requires_only_database_url():
    # The worker serves no HTTP, so the fail-closed web gates are irrelevant there:
    # with only DATABASE_URL set, the worker scope passes while the web scope fails.
    env = {"APP_ENV": "production", "DATABASE_URL": "postgresql://test/preflight"}
    worker_code, _ = check(env, scope="worker")
    web_code, web_msg = check(env, scope="web")
    assert worker_code == 0
    assert web_code == 1
    assert "CLERK_JWKS_URL" in web_msg


def test_worker_scope_still_fails_without_database_url():
    code, message = check({"APP_ENV": "production"}, scope="worker")
    assert code == 1
    assert "DATABASE_URL" in message


def test_unknown_scope_falls_back_to_strict_web_set():
    # A mistyped scope must over-check (fail safe), never under-check.
    env = {"APP_ENV": "production", "DATABASE_URL": "postgresql://test/preflight"}
    code, _ = check(env, scope="wrkr")
    assert code == 1  # treated as web -> the web gates are required


def test_parse_args_reads_scope_and_require_production():
    assert _parse_args(["--scope", "worker"]) == ("worker", False)
    assert _parse_args(["--scope=web"]) == ("web", False)
    assert _parse_args([]) == ("web", False)  # defaults
    assert _parse_args(["--scope", "worker", "--require-production"]) == ("worker", True)


# --- --require-production: the hardened release-command form -------------------

def test_require_production_fails_when_app_env_not_production():
    # The silent-disarm footgun: an empty/dropped APP_ENV would normally make the
    # gate SKIP. --require-production turns that into a hard failure instead, even
    # though every required var is present.
    env = {
        "DATABASE_URL": "postgresql://test/preflight",
        "CLERK_JWKS_URL": "https://example-test.clerk.accounts.dev/.well-known/jwks.json",
        "BASIC_AUTH_USER": "svc-test-do-not-use",
        "BASIC_AUTH_PASSWORD": "test-secret-do-not-use-in-prod",
    }  # note: APP_ENV unset
    code, message = check(env, scope="web", require_production=True)
    assert code == 1
    assert "APP_ENV" in message


def test_require_production_passes_when_fully_configured():
    code, message = check(_COMPLETE_PROD_ENV, scope="web", require_production=True)
    assert code == 0
    assert "OK" in message


def test_require_production_reports_app_env_and_missing_vars_together():
    code, message = check({}, scope="web", require_production=True)
    assert code == 1
    assert "APP_ENV" in message
    assert "DATABASE_URL" in message  # both problems surfaced in one message


def test_require_production_enforces_even_without_app_env_or_force():
    # Without the flag and with APP_ENV unset, the same env would SKIP (exit 0);
    # the flag is what makes the release command stay armed.
    env = {"DATABASE_URL": "postgresql://test/preflight"}  # web gates missing
    skipped_code, skipped_msg = check(env, scope="web")
    assert skipped_code == 0 and "skipped" in skipped_msg
    forced_code, _ = check(env, scope="web", require_production=True)
    assert forced_code == 1
