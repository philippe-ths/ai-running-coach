from app.services.notifications import (
    InMemoryNotifier,
    Notification,
    NotifierPort,
    _active_channel,
    get_notifier,
    set_notifier,
)
from app.services.notifications.telegram_adapter import TelegramNotifier


def test_in_memory_notifier_captures_sends():
    notifier = InMemoryNotifier()
    notifier.send(
        Notification(
            to="me@example.com",
            subject="Run analysis: easy 8km",
            html="<p>Body</p>",
            text="Body",
        )
    )
    notifier.send(
        Notification(
            to="other@example.com",
            subject="Another",
            html="<p>X</p>",
            text="X",
        )
    )
    assert len(notifier.sent) == 2
    assert notifier.sent[0].to == "me@example.com"
    assert notifier.sent[0].subject == "Run analysis: easy 8km"
    assert notifier.sent[1].to == "other@example.com"


def test_in_memory_notifier_satisfies_port_protocol():
    notifier = InMemoryNotifier()
    assert isinstance(notifier, NotifierPort)


def test_set_and_get_notifier_override():
    custom = InMemoryNotifier()
    set_notifier(custom)
    try:
        assert get_notifier() is custom
    finally:
        set_notifier(None)


def test_default_notifier_is_no_op_when_telegram_unset(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    set_notifier(None)
    notifier = get_notifier()
    notifier.send(
        Notification(
            to="x@example.com",
            subject="s",
            html="<p>h</p>",
            text="t",
        )
    )


def test_active_channel_is_telegram_when_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    assert _active_channel() == "telegram"


def test_active_channel_never_selects_email(monkeypatch):
    """Email (SMTP) was removed (#595): the channel selector only ever yields
    telegram (when configured) or the no-op path (None), never an email channel."""
    from app.core.config import settings

    # Telegram unconfigured -> no channel (the no-op selection), never "email".
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    assert _active_channel() is None

    # Telegram configured -> telegram.
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    assert _active_channel() == "telegram"

    # The removed SMTP settings are gone from the config surface entirely.
    assert not hasattr(settings, "SMTP_HOST")
    assert not hasattr(settings, "NOTIFY_TO")


def test_active_channel_requires_both_telegram_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    assert _active_channel() is None


def test_get_notifier_builds_telegram_when_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    set_notifier(None)
    try:
        notifier = get_notifier()
        assert isinstance(notifier, TelegramNotifier)
        assert notifier.bot_token == "123:ABC"
        assert notifier.chat_id == "42"
    finally:
        set_notifier(None)
