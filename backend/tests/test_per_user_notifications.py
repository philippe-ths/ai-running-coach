"""Per-user coach-report notification routing (P2.4, #120, ADR 0023).

Covers the routing FOUNDATION: the resolver (per-user Telegram chat vs the global
fallback), the composer threading `recipient` into `Notification.to`, and the
Telegram adapter honoring `to`. The linking flow + inbound multi-user callback
auth are the deferred follow-up (need P2.0's authenticated user).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.services.notifications import (
    build_receipt_notification,
    resolve_recipient,
)
from app.services.notifications.port import Notification
from app.services.notifications.telegram_adapter import TelegramNotifier


def _telegram_active(monkeypatch, *, chat_id="GLOBAL"):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", chat_id)
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "NOTIFY_TO", "")


# --- resolver ------------------------------------------------------------------

def test_resolve_recipient_returns_bound_telegram_chat(monkeypatch):
    _telegram_active(monkeypatch)
    user = SimpleNamespace(telegram_chat_id="USER_CHAT_1", email="a@x.dev")
    assert resolve_recipient(user) == "USER_CHAT_1"


def test_resolve_recipient_unbound_user_returns_none(monkeypatch):
    # None => the composer falls back to the global recipient (back-compat).
    _telegram_active(monkeypatch)
    assert resolve_recipient(SimpleNamespace(telegram_chat_id=None, email="a@x.dev")) is None
    assert resolve_recipient(None) is None


def test_resolve_recipient_email_channel_is_deferred(monkeypatch):
    # ADR 0023 defers per-user email; the email path stays on the global NOTIFY_TO.
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "NOTIFY_TO", "global@x.dev")
    user = SimpleNamespace(telegram_chat_id="USER_CHAT_1", email="a@x.dev")
    assert resolve_recipient(user) is None


# --- composer threads recipient into Notification.to ---------------------------

def test_receipt_routes_to_per_user_recipient(monkeypatch):
    _telegram_active(monkeypatch)
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app", recipient="USER_CHAT_1",
    )
    assert n.to == "USER_CHAT_1"  # the owner's chat, not the global


def test_receipt_falls_back_to_global_when_recipient_none(monkeypatch):
    _telegram_active(monkeypatch, chat_id="GLOBAL")
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app",  # no recipient
    )
    assert n.to == "GLOBAL"  # single-user back-compat


# --- adapter honors Notification.to --------------------------------------------

def _ok_response():
    resp = MagicMock()
    resp.json.return_value = {"ok": True}
    resp.raise_for_status.return_value = None
    return resp


def test_telegram_adapter_sends_to_notification_recipient():
    n = Notification(to="USER_CHAT_1", subject="s", html="h", text="t")
    with patch("app.services.notifications.telegram_adapter.httpx.post",
               return_value=_ok_response()) as post:
        TelegramNotifier(bot_token="b", chat_id="GLOBAL").send(n)
    assert post.call_args.kwargs["json"]["chat_id"] == "USER_CHAT_1"


def test_telegram_adapter_falls_back_to_configured_chat_when_to_empty():
    n = Notification(to="", subject="s", html="h", text="t")
    with patch("app.services.notifications.telegram_adapter.httpx.post",
               return_value=_ok_response()) as post:
        TelegramNotifier(bot_token="b", chat_id="GLOBAL").send(n)
    assert post.call_args.kwargs["json"]["chat_id"] == "GLOBAL"


# --- tenant isolation (the point of the phase) ---------------------------------

def test_one_users_notification_never_routes_to_another_users_chat(monkeypatch):
    """A's notification carries A's chat, never B's — proven across the full
    resolve -> compose -> send chain."""
    _telegram_active(monkeypatch)
    user_a = SimpleNamespace(telegram_chat_id="A_CHAT", email="a@x.dev")
    user_b = SimpleNamespace(telegram_chat_id="B_CHAT", email="b@x.dev")

    recipient_a = resolve_recipient(user_a)
    assert recipient_a == "A_CHAT" and recipient_a != user_b.telegram_chat_id

    n = build_receipt_notification(
        receipt_text="run", headline="h", activity_id="act-a",
        distance_m=0, app_base_url="http://app", recipient=recipient_a,
    )
    with patch("app.services.notifications.telegram_adapter.httpx.post",
               return_value=_ok_response()) as post:
        TelegramNotifier(bot_token="b", chat_id="GLOBAL").send(n)
    sent_chat = post.call_args.kwargs["json"]["chat_id"]
    assert sent_chat == "A_CHAT"
    assert sent_chat != "B_CHAT"  # no cross-tenant leak
