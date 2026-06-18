from typing import Optional

from app.schemas.coach import CoachReportRead
from app.services.notifications.in_memory_adapter import InMemoryNotifier
from app.services.notifications.noop_adapter import NoOpNotifier
from app.services.notifications.port import (
    Notification,
    NotificationRenderer,
    NotifierPort,
)

_active: NotifierPort | None = None


def _renderer_for_channel(channel: Optional[str]) -> Optional[NotificationRenderer]:
    """Return the rendering adapter for the active channel, or None.

    Rendering is keyed to the active CHANNEL (`_active_channel`), not to whichever
    transport `get_notifier` returns, so a `set_notifier` test override that swaps
    the transport does not change which channel's wire shape is produced. The
    `render_*` methods are stateless class methods, so no transport is built here.
    """
    if channel == "telegram":
        from app.services.notifications.telegram_adapter import TelegramNotifier

        return TelegramNotifier
    if channel == "email":
        from app.services.notifications.smtp_adapter import SMTPNotifier

        return SMTPNotifier
    return None


def _recipient_for_channel(channel: str) -> str:
    """The channel-specific recipient address (transport config, not shape)."""
    from app.core.config import settings

    if channel == "telegram":
        return str(settings.TELEGRAM_CHAT_ID)
    return settings.NOTIFY_TO


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
    headline: str,
    distance_m: int,
    app_base_url: str,
    stage: str = "fuller",
) -> Optional[Notification]:
    """Render a coach report into a Notification for the configured channel.

    Returns None when no channel is configured, which the pipeline treats as
    "notifications off" (the same skip the email-only path expressed via an
    unset NOTIFY_TO). Keeps the pipeline channel-agnostic: channel knowledge
    lives here next to `get_notifier`.

    A thin dispatcher (#333): it resolves the active channel and delegates the
    actual rendering (report shape, stage, and any tappable affordances) to that
    channel's adapter. A new output shape is handled inside one adapter; a new
    channel is one more adapter wired into `_renderer_for_channel`.

    `stage` is the A4 Exchange stage ("fuller" default, "opener" for the
    stage-one notification). Both stages are CoachMessageReport rows, so the stage
    cannot be sniffed from the report shape — it is passed explicitly by the job.
    """
    channel = _active_channel()
    renderer = _renderer_for_channel(channel)
    if renderer is None:
        return None
    return renderer.render_coach_report(
        report=report,
        headline=headline,
        distance_m=distance_m,
        app_base_url=app_base_url,
        stage=stage,
        to=_recipient_for_channel(channel),
    )


def build_receipt_notification(
    *,
    receipt_text: str,
    headline: str,
    activity_id: str,
    distance_m: int,
    app_base_url: str,
) -> Optional[Notification]:
    """Render a deterministic receipt (#296) into a Notification for the configured
    channel, or None when no channel is configured.

    Unlike `build_coach_notification`, this takes plain deterministic inputs (the
    receipt has no CoachReport): the rendered receipt prose, the activity headline
    for the title, and the activity id for the deep link + tap tokens. Telegram
    carries the RPE/pain/done tap keyboard; email renders the prose only (it cannot
    tap). A thin dispatcher (#333): channel selection here, rendering in the
    adapter."""
    channel = _active_channel()
    renderer = _renderer_for_channel(channel)
    if renderer is None:
        return None
    return renderer.render_receipt(
        receipt_text=receipt_text,
        headline=headline,
        activity_id=activity_id,
        distance_m=distance_m,
        app_base_url=app_base_url,
        to=_recipient_for_channel(channel),
    )


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
    "build_receipt_notification",
    "get_notifier",
    "set_notifier",
]
