from typing import Optional

from app.schemas.coach import CoachReportRead
from app.services.notifications.in_memory_adapter import InMemoryNotifier
from app.services.notifications.noop_adapter import NoOpNotifier
from app.services.notifications.port import Notification, NotifierPort

_active: NotifierPort | None = None


def _active_channel() -> Optional[str]:
    """Return the configured notification channel, or None if unconfigured.

    Telegram takes priority because it is the channel that works from the
    deployed (Railway) worker; email is the local/Pro-plan fallback. Each
    channel requires both of its settings before it activates.
    """
    from app.core.config import settings

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        return "telegram"
    if settings.SMTP_HOST and settings.NOTIFY_TO:
        return "email"
    return None


def get_notifier() -> NotifierPort:
    """Return the active notifier.

    If overridden via `set_notifier`, returns the override. Otherwise builds the
    default for the configured channel (see `_active_channel`), falling back to
    NoOpNotifier when nothing is configured.
    """
    global _active
    if _active is not None:
        return _active

    from app.core.config import settings

    channel = _active_channel()
    if channel == "telegram":
        from app.services.notifications.telegram_adapter import TelegramNotifier

        _active = TelegramNotifier(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID,
        )
    elif channel == "email":
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


def build_coach_notification(
    *,
    report: CoachReportRead,
    activity_class: str,
    distance_m: int,
    app_base_url: str,
) -> Optional[Notification]:
    """Render a coach report into a Notification for the configured channel.

    Returns None when no channel is configured, which the pipeline treats as
    "notifications off" (the same skip the email-only path expressed via an
    unset NOTIFY_TO). Keeps the pipeline channel-agnostic: channel knowledge
    lives here next to `get_notifier`.
    """
    from app.core.config import settings

    channel = _active_channel()
    if channel == "telegram":
        from app.services.notifications.telegram_template import (
            render_coach_report_telegram,
        )

        subject, text, url = render_coach_report_telegram(
            report=report,
            activity_class=activity_class,
            distance_m=distance_m,
            app_base_url=app_base_url,
        )
        return Notification(
            to=str(settings.TELEGRAM_CHAT_ID),
            subject=subject,
            html="",
            text=text,
            url=url,
        )
    if channel == "email":
        from app.services.notifications.email_template import (
            render_coach_report_email,
        )

        subject, html, text = render_coach_report_email(
            report=report,
            activity_class=activity_class,
            distance_m=distance_m,
            app_base_url=app_base_url,
        )
        return Notification(
            to=settings.NOTIFY_TO,
            subject=subject,
            html=html,
            text=text,
        )
    return None


def set_notifier(notifier: NotifierPort | None) -> None:
    """Override the active notifier. Pass None to reset to the default."""
    global _active
    _active = notifier


__all__ = [
    "InMemoryNotifier",
    "Notification",
    "NoOpNotifier",
    "NotifierPort",
    "build_coach_notification",
    "get_notifier",
    "set_notifier",
]
