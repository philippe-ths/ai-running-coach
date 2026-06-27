"""#477: the Telegram chat-link endpoints under /api/coach/telegram."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.models import User


def test_link_status_reports_unlinked_and_config(client: TestClient, db, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "mybot")
    res = client.get("/api/coach/telegram/link-status")
    assert res.status_code == 200
    assert res.json() == {"configured": True, "linked": False}


def test_create_link_mints_deep_link(client: TestClient, db, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "mybot")
    from app.services.notifications import telegram_link_token

    with patch.object(telegram_link_token, "redis_conn", MagicMock()):
        res = client.post("/api/coach/telegram/link-token")
    assert res.status_code == 200
    deep_link = res.json()["deep_link"]
    assert deep_link.startswith("https://t.me/mybot?start=")
    assert res.json()["linked"] is False


def test_create_link_503_when_bot_unconfigured(client: TestClient, db, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "")
    res = client.post("/api/coach/telegram/link-token")
    assert res.status_code == 503


def test_delete_link_unbinds_the_chat(client: TestClient, db):
    # The degrade-mode authenticated user is created on first request; bind a
    # chat to it, then unlink.
    client.get("/api/coach/telegram/link-status")
    user = db.query(User).filter(User.email == "local@runner.com").first()
    user.telegram_chat_id = "12345"
    db.commit()

    assert client.get("/api/coach/telegram/link-status").json()["linked"] is True

    res = client.delete("/api/coach/telegram/link")
    assert res.status_code == 200
    assert res.json() == {"linked": False}
    assert client.get("/api/coach/telegram/link-status").json()["linked"] is False
