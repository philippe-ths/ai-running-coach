"""Boot-time Telegram webhook registration (#1024): a revoked token drops the
registration, so the web process re-registers itself on every boot."""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services.notifications.telegram_webhook import register_webhook, webhook_url


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "8849636880:new-token")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setattr(settings, "API_BASE_URL", "http://localhost:8000")
    return settings


RAILWAY = {"RAILWAY_PUBLIC_DOMAIN": "web-production-b64d8.up.railway.app"}


@pytest.mark.parametrize(
    "api_base, environ, expected",
    [
        ("https://api.example.com/", {}, "https://api.example.com/api/webhooks/telegram"),
        ("https://api.example.com", RAILWAY, "https://api.example.com/api/webhooks/telegram"),
        ("http://localhost:8000", RAILWAY, "https://web-production-b64d8.up.railway.app/api/webhooks/telegram"),
        ("http://localhost:8000", {}, None),
    ],
    ids=["declared-base", "declared-base-wins", "railway-fallback", "no-public-address"],
)
def test_webhook_url_is_this_backends_public_address(prod, monkeypatch, api_base, environ, expected):
    monkeypatch.setattr(settings, "API_BASE_URL", api_base)
    assert webhook_url(settings, environ) == expected


def test_registers_this_service_with_the_secret(prod):
    ok = httpx.Response(200, json={"ok": True, "result": True}, request=httpx.Request("POST", "https://x"))
    with patch("app.services.notifications.telegram_adapter.httpx.post", return_value=ok) as post:
        assert register_webhook(settings, RAILWAY) is True
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    assert url.endswith("/bot8849636880:new-token/setWebhook")
    assert body == {
        "url": "https://web-production-b64d8.up.railway.app/api/webhooks/telegram",
        "secret_token": "hook-secret",
        "allowed_updates": ["message", "callback_query"],
    }


@pytest.mark.parametrize(
    "field, value",
    [("APP_ENV", "local"), ("TELEGRAM_BOT_TOKEN", ""), ("TELEGRAM_WEBHOOK_SECRET", "")],
)
def test_does_nothing_outside_a_configured_production(prod, monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    with patch("app.services.notifications.telegram_adapter.httpx.post") as post:
        assert register_webhook(settings, RAILWAY) is False
    post.assert_not_called()


def test_a_telegram_failure_never_fails_the_boot_or_logs_the_token(prod, caplog):
    def refuse(url, **_):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"}, request=httpx.Request("POST", url))

    with patch("app.services.notifications.telegram_adapter.httpx.post", side_effect=refuse):
        assert register_webhook(settings, RAILWAY) is False
    assert "Unauthorized" in caplog.text
    assert "new-token" not in caplog.text


def test_web_app_registers_on_boot():
    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.services.notifications.telegram_webhook.register_webhook") as register:
        with TestClient(app):
            pass
    register.assert_called_once_with(settings)
