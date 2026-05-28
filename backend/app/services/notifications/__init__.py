from app.services.notifications.in_memory_adapter import InMemoryNotifier
from app.services.notifications.noop_adapter import NoOpNotifier
from app.services.notifications.port import Notification, NotifierPort

_active: NotifierPort | None = None


def get_notifier() -> NotifierPort:
    """Return the active notifier.

    If overridden via `set_notifier`, returns the override.
    Otherwise constructs the default: SMTPNotifier when `SMTP_HOST` is set,
    NoOpNotifier when not.
    """
    global _active
    if _active is not None:
        return _active

    from app.core.config import settings

    if settings.SMTP_HOST:
        from app.services.notifications.smtp_adapter import SMTPNotifier

        _active = SMTPNotifier(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            from_addr=settings.SMTP_FROM,
            use_starttls=settings.SMTP_USE_STARTTLS,
        )
    else:
        _active = NoOpNotifier()
    return _active


def set_notifier(notifier: NotifierPort | None) -> None:
    """Override the active notifier. Pass None to reset to the default."""
    global _active
    _active = notifier


__all__ = [
    "InMemoryNotifier",
    "Notification",
    "NoOpNotifier",
    "NotifierPort",
    "get_notifier",
    "set_notifier",
]
