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


def _telegram_active(monkeypatch, *, chat_id="GLOBAL", owner_email=""):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", chat_id)
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "NOTIFY_TO", "")
    # Default to single-user mode (no owner concept) unless a test opts in, so the
    # #542 owner-only fallback is exercised explicitly where it matters.
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner_email)


# --- resolver ------------------------------------------------------------------

def test_resolve_recipient_returns_bound_telegram_chat(monkeypatch):
    _telegram_active(monkeypatch)
    user = SimpleNamespace(telegram_chat_id="USER_CHAT_1", email="a@x.dev")
    assert resolve_recipient(user) == "USER_CHAT_1"


def test_resolve_recipient_unbound_single_user_falls_back_to_global(monkeypatch):
    # OWNER_EMAIL unset => single-user mode: an unbound user keeps the original
    # global fallback (back-compat), so the single-owner deployment is unchanged.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="")
    assert resolve_recipient(SimpleNamespace(telegram_chat_id=None, email="a@x.dev")) == "GLOBAL"
    assert resolve_recipient(None) == "GLOBAL"


def test_resolve_recipient_unbound_owner_falls_back_to_global(monkeypatch):
    # Multi-user (OWNER_EMAIL set): the owner, still unbound, keeps the global chat
    # so the owner's own notifications are unaffected.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="owner@x.dev")
    owner = SimpleNamespace(telegram_chat_id=None, email="Owner@X.dev")  # case-insensitive
    assert resolve_recipient(owner) == "GLOBAL"


def test_resolve_recipient_unbound_non_owner_is_suppressed(monkeypatch):
    # The #542 fix: a non-owner runner who has not linked Telegram resolves to
    # None (suppress), never to the owner's global chat.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="owner@x.dev")
    non_owner = SimpleNamespace(telegram_chat_id=None, email="runner@x.dev")
    assert resolve_recipient(non_owner) is None
    # A partial/None user in multi-user mode is also suppressed (no email to match).
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


def test_receipt_telegram_single_user_falls_back_to_global(monkeypatch):
    # OWNER_EMAIL unset => single-user mode: a recipientless Telegram build keeps
    # the original global fallback (back-compat), so single-owner is unchanged.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="")
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app",  # no recipient
    )
    assert n is not None and n.to == "GLOBAL"


def test_receipt_telegram_multiuser_suppressed_when_recipient_none(monkeypatch):
    # OWNER_EMAIL set => multi-user: a recipientless Telegram build is SUPPRESSED,
    # never routed to the global owner chat (#542). Defense in depth: holds at the
    # builder even if a caller bypassed resolve_recipient.
    _telegram_active(monkeypatch, chat_id="OWNER_CHAT", owner_email="owner@x.dev")
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app",  # no recipient
    )
    assert n is None  # suppressed, not routed to OWNER_CHAT


def test_receipt_email_falls_back_to_global_notify_to(monkeypatch):
    # Email has no per-user routing yet (ADR 0023): a None recipient keeps the
    # global NOTIFY_TO, unchanged by #542 (which is Telegram-only).
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "NOTIFY_TO", "global@x.dev")
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app",  # no recipient
    )
    assert n is not None and n.to == "global@x.dev"


def test_non_owner_unbound_receipt_is_not_built_end_to_end(monkeypatch):
    # The full #542 guarantee: a non-owner unbound runner produces NO Telegram
    # notification (the leak was their receipt going to the owner's chat).
    _telegram_active(monkeypatch, chat_id="OWNER_CHAT", owner_email="owner@x.dev")
    non_owner = SimpleNamespace(telegram_chat_id=None, email="runner@x.dev")
    n = build_receipt_notification(
        receipt_text="run", headline="h", activity_id="act-1",
        distance_m=0, app_base_url="http://app",
        recipient=resolve_recipient(non_owner),
    )
    assert n is None


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
