"""Production config preflight (#480).

`assert_production_config` crashes the boot when a setting that fails closed in
production is unset, so the platform keeps the last healthy deploy instead of
promoting one that 503s every route. These tests pin the exact firing condition:
it must raise in production with any required setting missing, and stay a no-op
everywhere else (the test suite and local dev run with these unset).
"""

import pytest

from app.core.config import settings
from app.core.observability import ProductionConfigError, assert_production_config

# Clearly-fake test values; never real secrets (aiw-security-testing rule 6).
_FAKE_JWKS = "https://example-test.clerk.accounts.dev/.well-known/jwks.json"
_FAKE_USER = "svc-test-do-not-use"
_FAKE_PASSWORD = "test-secret-do-not-use-in-prod"


def _set(monkeypatch, **kwargs):
    for key, value in kwargs.items():
        monkeypatch.setattr(settings, key, value)


def test_noop_outside_production_even_when_all_unset(monkeypatch):
    # The degrade path: local dev and the test suite run with every fail-closed
    # setting unset and fall back to the single local user. The preflight must
    # stay silent, or it would break every non-production boot.
    _set(
        monkeypatch,
        APP_ENV="local",
        CLERK_JWKS_URL="",
        BASIC_AUTH_USER="",
        BASIC_AUTH_PASSWORD="",
    )
    assert_production_config()  # does not raise


def test_passes_in_production_when_fully_configured(monkeypatch):
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL=_FAKE_JWKS,
        BASIC_AUTH_USER=_FAKE_USER,
        BASIC_AUTH_PASSWORD=_FAKE_PASSWORD,
    )
    assert_production_config()  # does not raise


def test_raises_in_production_when_clerk_unset(monkeypatch):
    # The exact Phase 2 outage: code deployed before CLERK_JWKS_URL was set.
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL="",
        BASIC_AUTH_USER=_FAKE_USER,
        BASIC_AUTH_PASSWORD=_FAKE_PASSWORD,
    )
    with pytest.raises(ProductionConfigError) as exc:
        assert_production_config()
    assert "CLERK_JWKS_URL" in str(exc.value)


def test_raises_in_production_when_basic_auth_unset(monkeypatch):
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL=_FAKE_JWKS,
        BASIC_AUTH_USER="",
        BASIC_AUTH_PASSWORD="",
    )
    with pytest.raises(ProductionConfigError) as exc:
        assert_production_config()
    msg = str(exc.value)
    assert "BASIC_AUTH_USER" in msg
    assert "BASIC_AUTH_PASSWORD" in msg


def test_error_lists_every_missing_setting(monkeypatch):
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL="",
        BASIC_AUTH_USER="",
        BASIC_AUTH_PASSWORD="",
    )
    with pytest.raises(ProductionConfigError) as exc:
        assert_production_config()
    msg = str(exc.value)
    for name in ("CLERK_JWKS_URL", "BASIC_AUTH_USER", "BASIC_AUTH_PASSWORD"):
        assert name in msg


def test_raises_in_production_on_cors_wildcard(monkeypatch):
    # #707: `*` origins with credentials enabled is a real cross-origin hole; the
    # boot must fail closed on it even when every other setting is present.
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL=_FAKE_JWKS,
        BASIC_AUTH_USER=_FAKE_USER,
        BASIC_AUTH_PASSWORD=_FAKE_PASSWORD,
        CORS_ALLOWED_ORIGINS="https://app.example.com,*",
    )
    with pytest.raises(ProductionConfigError) as exc:
        assert_production_config()
    assert "*" in str(exc.value)


def test_explicit_cors_allowlist_passes_in_production(monkeypatch):
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL=_FAKE_JWKS,
        BASIC_AUTH_USER=_FAKE_USER,
        BASIC_AUTH_PASSWORD=_FAKE_PASSWORD,
        CORS_ALLOWED_ORIGINS="https://app.example.com",
    )
    assert_production_config()  # does not raise


def test_cors_wildcard_ignored_outside_production(monkeypatch):
    _set(monkeypatch, APP_ENV="local", CORS_ALLOWED_ORIGINS="*")
    assert_production_config()  # does not raise


def test_whitespace_only_value_counts_as_missing(monkeypatch):
    # A fat-fingered blank secret must be treated as unset, not "present", so it
    # cannot satisfy the gate (boundary case for the .strip() check).
    _set(
        monkeypatch,
        APP_ENV="production",
        CLERK_JWKS_URL="   ",
        BASIC_AUTH_USER=_FAKE_USER,
        BASIC_AUTH_PASSWORD=_FAKE_PASSWORD,
    )
    with pytest.raises(ProductionConfigError) as exc:
        assert_production_config()
    assert "CLERK_JWKS_URL" in str(exc.value)
