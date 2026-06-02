"""Tests for the diagnostic /api/_debug/sentry-test endpoint.

The endpoint is gated on `sentry_capture_active()` so it only fires when Sentry
capture is actually live (SDK installed AND DSN set). It must return 404 when
capture is off rather than raise an exception nothing records (issue #105).
"""

import pytest

from app.api import debug
from app.api.debug import SentrySmokeError


def test_returns_404_when_capture_inactive(client, monkeypatch):
    monkeypatch.setattr(debug, "sentry_capture_active", lambda: False)
    response = client.get("/api/_debug/sentry-test")
    assert response.status_code == 404


def test_raises_smoke_error_when_capture_active(client, monkeypatch):
    monkeypatch.setattr(debug, "sentry_capture_active", lambda: True)
    with pytest.raises(SentrySmokeError, match="phase-1 step 3 smoke test"):
        client.get("/api/_debug/sentry-test")
