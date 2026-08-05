"""The #296 receipt cadence (ADR 0018).

An instant, deterministic, LLM-free per-activity receipt on ingest, then ONE full
LLM report — fired ~BLOCK_GAP_SECONDS after the session goes quiet (the
block-complete debounce) or on a "done" tap. Active under a two-stage prompt with
`COACH_RECEIPT_CADENCE` on.

A reply only records the check-in here (the base `on_reply` no-op): the report waits
for the block-quiet timer or the "done" tap so it covers the whole session rather
than firing early on the first activity of a multi-part morning.
"""

import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs import exchange_ops
from app.jobs.cadence.base import PostActivityCadence
from app.models import Activity, Block
from app.models.coaching_relationship import CoachingRelationship
from app.services.analysis.classifier import Classification, compose_headline
from app.services.blocks import activity_end
from app.services.coach import exchange_lifecycle as lifecycle
from app.services.coach.receipt import build_receipt
from app.services.notifications import (
    Notification,
    build_receipt_notification,
    resolve_recipient,
)

logger = logging.getLogger(__name__)


def _receipt_templates_for(db: Session, user_id) -> Optional[dict]:
    """The runner's pre-generated voiced receipt templates, or None for the
    house-default floor. Read-only on the hot path: generation runs OFFLINE, never
    here, so the receipt stays instant and LLM-free. A null column resolves to the
    floor inside `build_receipt`. If a voice is declared but no set has been
    generated yet, a lazy background refresh is enqueued (best-effort) so the NEXT
    receipt speaks in voice — this receipt still uses the floor."""
    from app.services.coach.receipt_voice import maybe_enqueue_lazy_refresh

    rel = (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == user_id)
        .first()
    )
    if rel is None:
        return None
    maybe_enqueue_lazy_refresh(rel)
    return rel.receipt_templates


class ReceiptCadence(PostActivityCadence):
    """Instant receipt on ingest, then the full LLM report when the block-complete
    debounce fires (the no-"done" path) or after a "done" tap."""

    async def on_ingest(self, *, db, activity, block, notifier) -> Optional[Notification]:
        receipt = await self.send_receipt(
            db=db, activity=activity, block=block, notifier=notifier
        )
        exchange_ops.schedule_block_complete(str(block.id), str(activity.id))
        logger.info(
            "Sent receipt + scheduled full-report check for block %s (activity %s) in %ds",
            block.id, activity.strava_activity_id, settings.BLOCK_GAP_SECONDS,
        )
        return receipt

    async def send_receipt(
        self, *, db: Session, activity: Activity, block: Block, notifier
    ) -> Optional[Notification]:
        """Send the instant, deterministic, block-aware receipt for this activity and
        open the exchange.

        Deduped by `Activity.receipt_sent_at` (one receipt per activity, idempotent on a
        pipeline retry). The FIRST receipt opens the exchange (`opened_at`), anchoring
        the reply window — independent of delivery, so the reply path works under a NoOp
        local notifier. The receipt text is filled deterministically from the block
        facts using the runner's voiced templates (or the house-default floor), so no
        LLM is on the hot path and the receipt can never fall back. On a send failure the
        sentinel is left null so the receipt stays re-sendable. Returns the Notification
        sent, or None (already sent / no channel / send failed)."""
        db.refresh(activity)
        if activity.receipt_sent_at is not None:
            logger.info(
                "Skipping receipt for activity %s: already sent at %s",
                activity.strava_activity_id, activity.receipt_sent_at,
            )
            return None

        members = (
            db.query(Activity)
            .filter(Activity.block_id == block.id)
            .order_by(Activity.start_date, Activity.id)
            .all()
        )
        member_types = [m.type for m in members]
        subject_index = next(
            (i for i, m in enumerate(members) if m.id == activity.id), len(members) - 1
        )
        receipt = build_receipt(
            member_types,
            subject_index,
            seed=activity.strava_activity_id,
            templates=_receipt_templates_for(db, activity.user_id),
        )
        headline = compose_headline(activity, Classification.from_metrics(activity.metrics))
        notification = build_receipt_notification(
            receipt_text=receipt.text,
            headline=headline,
            activity_id=str(activity.id),
            distance_m=activity.distance_m or 0,
            app_base_url=settings.APP_BASE_URL,
            # P2.4 (#120): route the receipt to the activity owner's bound channel; an
            # unbound non-owner is suppressed, and the global fallback is kept only for
            # the identified owner or a db-proven single-user deploy (#600).
            recipient=resolve_recipient(activity.user, db=db),
        )

        # Open the exchange on the first receipt, independent of delivery (A4 posture):
        # the reply window anchors here and must work even for a NoOp local notifier.
        exchange = lifecycle.get_exchange_for_block(db, block.id)
        if exchange is not None:
            lifecycle.open_exchange(db, exchange)

        if notification is None:
            # Either no channel, or no recipient resolved for this runner (#795 —
            # suppression is the SAFE outcome). `_resolve_to` logs the recipient
            # case with its reason just before this; don't assert a cause here.
            logger.info(
                "No receipt notification for activity %s: nothing to deliver "
                "(no channel configured, or no recipient for this runner)",
                activity.strava_activity_id,
            )
            return None

        try:
            notifier.send(notification)
        except Exception:
            logger.exception(
                "Receipt send failed for activity %s; sentinel left unset",
                activity.strava_activity_id,
            )
            return None

        lifecycle.record_receipt_sent(db, activity)
        return notification

    async def on_block_complete(
        self, *, db, block_id, activity_id, notifier
    ) -> Optional[Notification]:
        resolved = exchange_ops.resolve_completed_block(db, block_id, activity_id)
        if resolved is None:
            return None
        primary, _exchange = resolved
        return await exchange_ops.run_fuller_turn(
            db=db, activity=primary, notifier=notifier
        )

    def on_done(self, *, db, activity_id) -> bool:
        """Record the runner's explicit completion signal and schedule the full report at
        +BLOCK_GAP_SECONDS (owner-locked: "done" -> full report in 30 min).

        Only while the exchange is OPEN, not yet CLOSED (`fuller_sent_at` null), and within
        the reply window — so a "done" tap on a stale exchange never spins one up. The
        full-report check is scheduled on the block's CURRENT last member, so a straggler
        that syncs before it fires still supersedes it and is folded into the report
        (idempotent over cancellation, and idempotent with the ingest-time check via
        `fuller_sent_at`). Best-effort: a Redis hiccup never breaks the tap. Returns True if
        it scheduled the report."""
        activity_uuid = activity_id if isinstance(activity_id, uuid.UUID) else uuid.UUID(str(activity_id))
        activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
        if activity is None or activity.block_id is None:
            return False

        block = db.query(Block).filter(Block.id == activity.block_id).first()
        exchange = lifecycle.get_exchange_for_block(db, activity.block_id)
        if block is None or exchange is None:
            return False
        # A "done" tap acts only on an OPEN, in-window exchange — the SAME guard the reply
        # path uses (`can_fire_reply_fuller`), so the "reply/done may act only on an open,
        # in-window exchange" invariant has one implementation, not two (#697). The receipt
        # opens the exchange on ingest, so a delivered receipt (the only surface carrying the
        # "done" button) always sees an opened exchange; a closed/stale one never re-fires
        # (AC3/AC4). `mark_done` still opens defensively.
        if not lifecycle.can_fire_reply_fuller(exchange):
            return False

        # Record the explicit completion signal (idempotent), opening the exchange if
        # the receipt somehow had not (defensive — the receipt opens it on ingest).
        # #505: mark_done returns False when the exchange was ALREADY done, so a second/
        # rapid "done" tap must NOT schedule another full-report generation — the first
        # tap already scheduled it. Bail before re-scheduling so repeated taps queue exactly
        # one generation (the fire-time dedup was the only prior backstop, by which point
        # concurrent LLM generations could already be running).
        if not lifecycle.mark_done(db, exchange):
            logger.info(
                "Ignoring duplicate done tap for block %s: already done", block.id
            )
            return False

        last = max(
            db.query(Activity).filter(Activity.block_id == block.id).all(),
            key=activity_end,
        )
        try:
            exchange_ops.schedule_block_complete(str(block.id), str(last.id))
        except Exception:
            logger.exception(
                "failed to schedule done-triggered full report for block %s", block.id
            )
            return False
        return True
