from html import escape
from typing import Optional

from app.schemas.coach import CoachReportRead
from app.services.notifications.callback_token import encode as encode_callback_token
from app.services.notifications.in_memory_adapter import InMemoryNotifier
from app.services.notifications.noop_adapter import NoOpNotifier
from app.services.notifications.port import (
    Notification,
    NotificationAction,
    NotifierPort,
)


def _opener_actions(report: CoachReportRead) -> tuple[NotificationAction, ...]:
    """Build tappable RPE/pain actions from a message report's questions (I1b).

    Mirrors the in-app contract (`CoachReportPanel.handleOptionTap`): only `rpe`
    and `pain` options are tappable, and a tap carries the option's numeric
    payload. Skips any option whose payload is not an integer (so a malformed
    option drops out rather than producing a dead button). Returns empty for the
    structured legacy shape or a report with no tappable options."""
    content = report.report
    questions = getattr(content, "questions", None)
    if not questions:
        return ()
    actions: list[NotificationAction] = []
    for q in questions:
        for opt in getattr(q, "options", []) or []:
            if opt.kind not in ("rpe", "pain"):
                continue
            try:
                value = int(opt.payload)
            except (TypeError, ValueError):
                continue
            try:
                token = encode_callback_token(
                    kind=opt.kind,
                    activity_id=str(report.activity_id),
                    value=value,
                )
            except ValueError:
                continue
            actions.append(NotificationAction(label=opt.label, token=token))
    return tuple(actions)

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

    `stage` is the A4 Exchange stage ("fuller" default, "opener" for the
    stage-one notification). Both stages are CoachMessageReport rows, so the stage
    cannot be sniffed from the report shape — it is passed explicitly by the job.
    """
    from app.core.config import settings

    channel = _active_channel()
    if channel == "telegram":
        from app.services.notifications.telegram_template import (
            render_coach_report_telegram,
        )

        subject, text, url = render_coach_report_telegram(
            report=report,
            headline=headline,
            distance_m=distance_m,
            app_base_url=app_base_url,
            stage=stage,
        )
        # I1b: only the opener carries tappable RPE/pain buttons (the fuller turn
        # is the response to that input, so it has no quick-reply affordances).
        actions = _opener_actions(report) if stage == "opener" else ()
        return Notification(
            to=str(settings.TELEGRAM_CHAT_ID),
            subject=subject,
            html="",
            text=text,
            url=url,
            actions=actions,
        )
    if channel == "email":
        from app.services.notifications.email_template import (
            render_coach_report_email,
        )

        subject, html, text = render_coach_report_email(
            report=report,
            headline=headline,
            distance_m=distance_m,
            app_base_url=app_base_url,
            stage=stage,
        )
        return Notification(
            to=settings.NOTIFY_TO,
            subject=subject,
            html=html,
            text=text,
        )
    return None


def _receipt_actions(activity_id: str) -> tuple[NotificationAction, ...]:
    """Build the deterministic receipt tap keyboard (#296): the fixed RPE/pain
    quick-replies plus the "done" control, each carrying its opaque callback token.
    A tap whose token cannot be encoded is dropped rather than producing a dead
    button (mirrors `_opener_actions`)."""
    from app.services.coach.receipt import RECEIPT_TAPS

    actions: list[NotificationAction] = []
    for tap in RECEIPT_TAPS:
        try:
            token = encode_callback_token(
                kind=tap.kind, activity_id=str(activity_id), value=tap.value
            )
        except ValueError:
            continue
        actions.append(NotificationAction(label=tap.label, token=token))
    return tuple(actions)


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
    tap). The pipeline stays channel-agnostic; channel knowledge lives here."""
    from app.core.config import settings

    channel = _active_channel()
    if channel is None:
        return None

    distance_km = round((distance_m or 0) / 1000.0, 1)
    label = headline or "Activity"
    title = f"{label} — {distance_km}km" if distance_km > 0 else label
    url = f"{app_base_url.rstrip('/')}/activity/{activity_id}"

    if channel == "telegram":
        return Notification(
            to=str(settings.TELEGRAM_CHAT_ID),
            subject=title,
            html="",
            text=receipt_text,
            url=url,
            actions=_receipt_actions(activity_id),
        )
    # email: no tap affordances, render the prose + link in the body.
    return Notification(
        to=settings.NOTIFY_TO,
        subject=title,
        html=f"<p>{escape(receipt_text)}</p>",
        text=receipt_text,
        url=url,
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
