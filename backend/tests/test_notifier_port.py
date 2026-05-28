from app.services.notifications import (
    InMemoryNotifier,
    Notification,
    NotifierPort,
    get_notifier,
    set_notifier,
)


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


def test_default_notifier_is_no_op_when_smtp_unset(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "")
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
