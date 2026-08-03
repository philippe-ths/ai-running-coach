"""The A4 two-stage Exchange cadence (ADR 0010).

An immediate lightweight LLM opener once the block looks complete, then a
conditional deep fuller turn fired early by the runner's reply or later by a
timer. Active under a two-stage prompt with `COACH_RECEIPT_CADENCE` off.
"""

import logging
import uuid
from typing import Optional

from app.core.config import settings
from app.core.queue import queue
from app.jobs import exchange_ops
from app.jobs.cadence.base import PostActivityCadence
from app.models import Activity, Block
from app.services.coach import exchange_lifecycle as lifecycle
from app.services.coach.service import generate_opener
from app.services.notifications import Notification

logger = logging.getLogger(__name__)


class OpenerFullerCadence(PostActivityCadence):
    """Schedule the block-complete debounce on ingest, run the LLM opener when it
    fires (on the block's primary), and fire the conditional fuller turn early from
    a reply while the exchange is open."""

    async def on_ingest(self, *, db, activity, block, notifier) -> Optional[Notification]:
        exchange_ops.schedule_block_complete(str(block.id), str(activity.id))
        logger.info(
            "Scheduled block-complete check for block %s (activity %s) in %ds",
            block.id, activity.strava_activity_id, settings.BLOCK_GAP_SECONDS,
        )
        return None

    async def on_block_complete(
        self, *, db, block_id, activity_id, notifier
    ) -> Optional[Notification]:
        resolved = exchange_ops.resolve_completed_block(db, block_id, activity_id)
        if resolved is None:
            return None
        primary, exchange = resolved
        return await self.run_opener_stage(
            db=db, activity=primary, exchange=exchange, notifier=notifier
        )

    async def run_opener_stage(
        self, *, db, activity, exchange, notifier
    ) -> Optional[Notification]:
        """Stage one: generate + notify the opener for the block's primary activity,
        then conditionally schedule the fuller turn. Deduped by the exchange's
        `opener_sent_at` (the opener fires at most once per block); `opened_at`
        marks generation so a late arrival knows the exchange has spoken even if
        delivery failed or no channel is configured."""
        db.refresh(exchange)
        if lifecycle.notification_already_sent(exchange, "opener_sent_at"):
            logger.info(
                "Opener already sent for block %s; skipping", exchange.block_id
            )
            return None

        result = await generate_opener(db, str(activity.id))
        if result is None or result.report is None:
            logger.info("No opener generated for activity %s", activity.strava_activity_id)
            return None

        lifecycle.open_exchange(db, exchange)

        # Schedule the conditional fuller turn FIRST, on the salience decision (the
        # opener LLM's judgment OR-ed with the deterministic safety override; a fallback
        # opener also schedules a recovery fuller). Scheduling before notifying means a
        # crash in between still leaves the fuller scheduled. Independent of opener-
        # notify success: scheduling is about salience, not delivery. The timer is
        # idempotent against an early reply via the fuller sentinel (ADR 0010 chose
        # idempotency over racy timer cancellation).
        if result.schedule_fuller_turn:
            exchange_ops.schedule_fuller_turn(str(activity.id))
            logger.info(
                "Scheduled fuller turn for activity %s in %ds",
                activity.strava_activity_id, settings.EXCHANGE_STAGE2_DELAY_SECONDS,
            )

        # Notify the opener — even a fallback opener, whose templated prose ("Nice work
        # … I'll follow up shortly") is benign and non-medical, so a red-flag run whose
        # opener LLM hiccuped still gets a non-silent opener (AC3) and the scheduled
        # fuller carries the substantive coaching.
        return exchange_ops.notify_stage(
            db, activity, report=result.report, stage="opener",
            sentinel_attr="opener_sent_at", notifier=notifier, sentinel_obj=exchange,
        )

    def on_reply(self, *, db, activity_id) -> bool:
        """Enqueue the fuller turn early when the exchange owning this activity's block
        is OPEN.

        The exchange is open when (read from the exchanges row, A1): the opener has
        generated (`opened_at` set — independent of delivery, so it works for a NoOp
        local notifier too), the fuller is not yet sent (`fuller_sent_at` null), and
        the opener is within EXCHANGE_REPLY_WINDOW_SECONDS — so a reply on a stale
        exchange never spins up a fresh one (AC4, AC3 closed-never-refires). The
        fuller fires on the block's PRIMARY activity (where the report row lives).
        Idempotent against the racing timer (the fuller job's own sentinel guard); a
        deterministic job_id collapses rapid repeated replies while the job is still
        queued. Best-effort: a Redis hiccup never breaks the reply request. Returns
        True if it enqueued the fuller turn.
        """
        activity_uuid = activity_id if isinstance(activity_id, uuid.UUID) else uuid.UUID(str(activity_id))
        activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
        if activity is None or activity.block_id is None:
            return False  # no activity, or never grouped (nothing to advance)

        block = db.query(Block).filter(Block.id == activity.block_id).first()
        exchange = lifecycle.get_exchange_for_block(db, activity.block_id)
        if block is None or exchange is None:
            return False
        # Open (opener generated, not closed) and within the reply window: a closed or
        # stale or never-opened exchange never re-fires / spins up (AC3/AC4). The state
        # is owned by exchange_lifecycle, so the "never re-fire" guarantee lives once.
        if not lifecycle.can_fire_reply_fuller(exchange):
            return False

        primary_id = block.primary_activity_id
        # Lazy, and still resolved from `process_new_activity`: the job's module path is
        # part of its on-Redis payload, so the entrypoints stay put (#696).
        from app.jobs.process_new_activity import fuller_turn_job

        try:
            queue.enqueue(fuller_turn_job, str(primary_id), job_id=f"fuller_{primary_id}")
        except Exception:
            logger.exception(
                "failed to enqueue reply-triggered fuller turn for block %s", block.id
            )
            return False
        return True
