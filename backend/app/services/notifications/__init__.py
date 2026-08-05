import logging
from typing import Optional

from app.schemas.coach import CoachReportRead
from app.services.notifications.in_memory_adapter import InMemoryNotifier
from app.services.notifications.noop_adapter import NoOpNotifier
from app.services.notifications.port import (
    Notification,
    NotificationRenderer,
    NotifierPort,
)

logger = logging.getLogger(__name__)

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
    return None


def _resolve_to(channel: Optional[str], recipient: Optional[str]) -> Optional[str]:
    """The address to deliver to, or None to SUPPRESS the notification (#542, #795).

    No recipient means NO notification. This layer never substitutes an address of
    its own, so no configuration an individual process happens to hold can turn a
    suppressed runner into a delivery.

    #795: it used to. With no recipient it re-derived single-vs-multi-user from
    `OWNER_EMAIL` alone and, reading "unset" as single-user, substituted the global
    chat. That is the one question this layer cannot answer — it has no `db`. On the
    deployed stack `OWNER_EMAIL` was set on `web` but not on `worker`, and `worker`
    is the process that sends: `resolve_recipient` correctly suppressed an unbound
    non-owner (it asked the db and saw more than one user) and this function handed
    their run to the owner's chat anyway. Two copies of one decision, and only the
    copy with the evidence got hardened by #600.

    The single-user global fallback still exists, in `resolve_recipient`, where a
    `db` can prove the deployment has earned it. Telegram is the only channel
    (#595); `channel` is retained because suppression is only meaningful once a
    channel is configured at all.
    """
    if recipient:
        return recipient
    if channel is not None:
        logger.info(
            "Suppressing %s notification: no recipient resolved for this runner. "
            "An unbound runner is only delivered to the global chat when they are "
            "the identified owner or the deployment is provably single-user (#795).",
            channel,
        )
    return None


def _user_count(db) -> int:
    """How many User rows exist. `limit(2)` is enough to answer "more than one?"."""
    from app.models import User

    return db.query(User.id).limit(2).count()


def resolve_owner_user(db):
    """The `User` who is the deployment owner, or None if not identifiable (#600/#608).

    Identity, in order:
      1. `OWNER_EMAIL` set -> the user whose email matches it (case-insensitive),
         or None when no user matches (a mistyped/stale OWNER_EMAIL identifies
         nobody rather than falling through to a wrong user).
      2. `OWNER_EMAIL` unset + exactly one user -> that user is the implicit owner
         (single-user back-compat).
      3. `OWNER_EMAIL` unset + more than one user -> the owner is not identifiable
         -> None (fail closed).

    Shared by the outbound recipient resolver and the inbound global-owner-chat
    ownership check, so both agree on who "the owner" is without keying safety on
    `OWNER_EMAIL` being present (#600).
    """
    from sqlalchemy import func

    from app.core.config import settings
    from app.models import User

    owner_email = (settings.OWNER_EMAIL or "").strip().lower()
    if owner_email:
        return (
            db.query(User)
            .filter(func.lower(User.email) == owner_email)
            .first()
        )
    users = db.query(User).limit(2).all()
    return users[0] if len(users) == 1 else None


def resolve_recipient(user, db=None) -> Optional[str]:
    """The per-user recipient address for the active channel (P2.4, #120, #542, #600).

    Telegram routes to the user's bound chat (`telegram_chat_id`) — the decided
    per-user channel (ADR 0023). When the user has NOT bound a chat, the global
    `TELEGRAM_CHAT_ID` is delivered ONLY to the identified deployment owner; every
    other unbound runner resolves to None, which the composer turns into NO
    notification rather than delivering to the owner's chat (the #542 multi-user
    leak).

    #600 hardening: cross-tenant safety no longer HINGES on `OWNER_EMAIL` being
    set. The owner is identified by `OWNER_EMAIL` when present; when it is unset,
    the global fallback is kept ONLY for a deployment `db` proves is single-user
    (exactly one user). With `OWNER_EMAIL` unset AND more than one user — or with
    no `db` to check — an unbound runner fails closed to None, so a dropped or
    mistyped `OWNER_EMAIL` on a multi-user deploy cannot reopen the leak. Tolerant
    of a None/partial user so a missing relationship never breaks the pipeline.
    """
    if _active_channel() != "telegram":
        return None
    from app.core.config import settings

    bound = getattr(user, "telegram_chat_id", None) if user is not None else None
    if bound:
        return bound

    owner_email = (settings.OWNER_EMAIL or "").strip().lower()
    if owner_email:
        user_email = (getattr(user, "email", "") or "").strip().lower()
        if user_email and user_email == owner_email:
            return str(settings.TELEGRAM_CHAT_ID)
        return _suppress(user)  # non-owner, unbound -> no leak to the owner chat
    # OWNER_EMAIL unset: keep the single-user global fallback ONLY when we can prove
    # the deployment has at most one user; otherwise fail closed (#600).
    if db is not None and _user_count(db) <= 1:
        return str(settings.TELEGRAM_CHAT_ID)
    return _suppress(user)


def _suppress(user) -> None:
    """Suppress delivery for an unbound runner, loudly (#795).

    Suppression is the SAFE outcome, but it is not a neutral one: this runner
    receives nothing at all, indefinitely, and until now that was invisible from
    both ends. The #795 leak ran for a week partly because the affected runner had
    never received a single notification and so had nothing to report — silence
    reads as "no runs yet", not "misrouted".

    WARNING rather than INFO on purpose: a runner getting nothing is a broken
    product experience, and it is fixed by them binding a chat, which they will
    only do if someone notices. Never raises and never blocks delivery decisions.
    """
    logger.warning(
        "notification_suppressed_unbound_runner: user_id=%s has no linked Telegram "
        "chat and is not the identified owner, so this notification is not "
        "delivered to anyone. They receive nothing until they link a chat (#795).",
        getattr(user, "id", None),
        extra={"user_id": str(getattr(user, "id", None))},
    )
    return None


def _active_channel() -> Optional[str]:
    """Return the configured notification channel, or None if unconfigured.

    Telegram is the only channel (#595); the email (SMTP) channel was removed. It
    activates when both of its settings are set, otherwise the no-op path is used.
    """
    from app.core.config import settings

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        return "telegram"
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
    recipient: Optional[str] = None,
) -> Optional[Notification]:
    """Render a coach report into a Notification for the configured channel.

    Returns None when no channel is configured, which the pipeline treats as
    "notifications off". Keeps the pipeline channel-agnostic: channel knowledge
    lives here next to `get_notifier`.

    A thin dispatcher (#333): it resolves the active channel and delegates the
    actual rendering (report shape, stage, and any tappable affordances) to that
    channel's adapter. A new output shape is handled inside one adapter; a new
    channel is one more adapter wired into `_renderer_for_channel`.

    `stage` is the A4 Exchange stage ("fuller" default, "opener" for the
    stage-one notification). Both stages are CoachMessageReport rows, so the stage
    cannot be sniffed from the report shape — it is passed explicitly by the job.

    `recipient` (P2.4, #120) is the activity owner's per-user address on the
    active channel (from `resolve_recipient`). Returns None (no notification) when
    `_resolve_to` suppresses — a non-owner unbound Telegram runner in multi-user
    mode, never delivered to the owner's chat (#542). See `_resolve_to` for the
    single-user fallback.
    """
    channel = _active_channel()
    renderer = _renderer_for_channel(channel)
    if renderer is None:
        return None
    to = _resolve_to(channel, recipient)
    if to is None:
        # Telegram, non-owner runner with no linked chat: suppress rather than
        # deliver to the global owner chat (#542).
        return None
    return renderer.render_coach_report(
        report=report,
        headline=headline,
        distance_m=distance_m,
        app_base_url=app_base_url,
        stage=stage,
        to=to,
    )


def build_receipt_notification(
    *,
    receipt_text: str,
    headline: str,
    activity_id: str,
    distance_m: int,
    app_base_url: str,
    recipient: Optional[str] = None,
) -> Optional[Notification]:
    """Render a deterministic receipt (#296) into a Notification for the configured
    channel, or None when no channel is configured.

    Unlike `build_coach_notification`, this takes plain deterministic inputs (the
    receipt has no CoachReport): the rendered receipt prose, the activity headline
    for the title, and the activity id for the deep link + tap tokens. Telegram
    carries the RPE/pain/done tap keyboard. A thin dispatcher (#333): channel
    selection here, rendering in the adapter.

    `recipient` (P2.4, #120) is the activity owner's per-user address. Returns None
    (no notification) when `_resolve_to` suppresses — a non-owner unbound Telegram
    runner in multi-user mode, never delivered to the owner's chat (#542). See
    `_resolve_to` for the single-user fallback."""
    channel = _active_channel()
    renderer = _renderer_for_channel(channel)
    if renderer is None:
        return None
    to = _resolve_to(channel, recipient)
    if to is None:
        # Telegram, non-owner runner with no linked chat: suppress rather than
        # deliver to the global owner chat (#542).
        return None
    return renderer.render_receipt(
        receipt_text=receipt_text,
        headline=headline,
        activity_id=activity_id,
        distance_m=distance_m,
        app_base_url=app_base_url,
        to=to,
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
    "resolve_owner_user",
    "resolve_recipient",
    "set_notifier",
]
