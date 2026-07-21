"""Clerk authorized-parties (`azp`) allowlist derivation (#707).

Security-first coverage (aiw-security-testing): the point is that azp validation
is ARMED by default from existing frontend/origin config, with no new required
env var, and that a foreign same-instance origin is rejected while the real
frontend always passes. These pin the derivation and normalisation logic on the
`Settings.clerk_authorized_parties_list` property; the token-level enforcement
lives in test_clerk_auth.py (TestAuthorizedParties).
"""

from app.core.config import settings


def _set(monkeypatch, **kwargs):
    for key, value in kwargs.items():
        monkeypatch.setattr(settings, key, value)


def test_explicit_value_wins(monkeypatch):
    # An explicit allowlist is used verbatim (normalised), ignoring the derived
    # origins, so an operator can widen/narrow it deliberately.
    _set(
        monkeypatch,
        CLERK_AUTHORIZED_PARTIES="https://app.example.com, https://satellite.example.com",
        CORS_ALLOWED_ORIGINS="https://ignored.example.com",
        APP_BASE_URL="https://also-ignored.example.com",
    )
    assert settings.clerk_authorized_parties_list == [
        "https://app.example.com",
        "https://satellite.example.com",
    ]


def test_derived_from_cors_and_app_base_url_when_unset(monkeypatch):
    # The default-armed path: with no explicit setting the allowlist is the union
    # of the CORS origins and APP_BASE_URL, so azp validation is on by default
    # from config the operator already set.
    _set(
        monkeypatch,
        CLERK_AUTHORIZED_PARTIES="",
        CORS_ALLOWED_ORIGINS="https://app.example.com,https://www.example.com",
        APP_BASE_URL="https://app.example.com",
    )
    parties = settings.clerk_authorized_parties_list
    assert "https://app.example.com" in parties
    assert "https://www.example.com" in parties


def test_real_frontend_origin_is_always_present(monkeypatch):
    # The frontend must be a CORS-allowed origin to reach the backend, so its
    # azp is guaranteed to be in the derived allowlist -- no false 401s for a
    # correctly-configured deploy.
    _set(
        monkeypatch,
        CLERK_AUTHORIZED_PARTIES="",
        CORS_ALLOWED_ORIGINS="https://frontend.example.com,http://localhost:8000",
        APP_BASE_URL="https://frontend.example.com",
    )
    assert "https://frontend.example.com" in settings.clerk_authorized_parties_list


def test_derived_list_deduped_and_slash_normalised(monkeypatch):
    # A trailing-slash origin and a duplicate collapse to one bare-origin entry,
    # so the membership test matches the bare-origin azp Clerk mints.
    _set(
        monkeypatch,
        CLERK_AUTHORIZED_PARTIES="",
        CORS_ALLOWED_ORIGINS="https://app.example.com/,https://app.example.com",
        APP_BASE_URL="https://app.example.com/",
    )
    assert settings.clerk_authorized_parties_list == ["https://app.example.com"]


def test_all_sources_blank_yields_empty_inert_list(monkeypatch):
    # Degenerate (not a real deploy): every source blank -> empty list, which
    # leaves the token-level check inert rather than rejecting everything.
    _set(
        monkeypatch,
        CLERK_AUTHORIZED_PARTIES="",
        CORS_ALLOWED_ORIGINS="",
        APP_BASE_URL="",
    )
    assert settings.clerk_authorized_parties_list == []
