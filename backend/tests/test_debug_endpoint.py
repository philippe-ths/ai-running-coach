"""Tests for the diagnostic /api/_debug/sentry-test endpoint."""

import pytest

from app.api.debug import SentrySmokeError
from app.core.config import settings


def test_returns_404_when_sentry_dsn_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "SENTRY_DSN", "")
    response = client.get("/api/_debug/sentry-test")
    assert response.status_code == 404


def test_raises_smoke_error_when_sentry_dsn_set(client, monkeypatch):
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://x@example.ingest.sentry.io/1")
    with pytest.raises(SentrySmokeError, match="phase-1 step 3 smoke test"):
        client.get("/api/_debug/sentry-test")
