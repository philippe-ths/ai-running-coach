"""Post-activity cadence seam (#330).

After an activity is ingested, analyzed, and block-assigned, what the coach does
next — the shape and timing of the `Exchange` — depends on the active configuration.
There are three cadences:

- SINGLE-SHOT (any non-two-stage prompt: coach_message_v1, coach_report_v*): one
  report generated and notified inline on ingest. The prior pipeline, untouched.
- OPENER/FULLER (a two-stage prompt, COACH_RECEIPT_CADENCE off; A4 / ADR 0010): a
  debounced LLM opener once the block looks complete, then a conditional fuller turn
  fired by the runner's reply (early) or a timer.
- RECEIPT (a two-stage prompt, COACH_RECEIPT_CADENCE on; #296 / ADR 0018): an instant
  deterministic per-activity receipt on ingest, then one full LLM report ~BLOCK_GAP
  after the session (the block-complete debounce) or on a "done" tap.

Before this seam, the decision was ~10 `is_two_stage_prompt` / `is_receipt_cadence`
gates scattered across six functions in `process_new_activity`. This module is now
the one place it lives. `get_active_cadence(settings)` reads the config AT DISPATCH
TIME (never baked into a scheduled job's args), so flipping COACH_PROMPT_ID /
COACH_RECEIPT_CADENCE stays a zero-code-change rollback: a job scheduled under one
cadence that fires after a flip does the now-current thing (AC6).

The adapters are THIN. They orchestrate the unchanged side-effect helpers in
`process_new_activity` (notification, scheduling, generation, sentinels); the
dangerous code is not rewritten, only re-dispatched. Each event maps to one method,
so one cadence's whole lifecycle reads in one class, and a new cadence is a new
adapter rather than a new gate in six functions.

The post-activity events:
- on_ingest         — a fresh activity has been ingested + analyzed + block-assigned
- on_block_complete — the block-complete debounce fired (scheduled at +BLOCK_GAP)
- on_reply          — the runner replied (a CheckIn or chat message) on a block member
- on_done           — the runner tapped "done" (receipt cadence only)

The fuller GENERATION itself stays in `process_new_activity.process_fuller_turn`, a
shared helper both two-stage cadences invoke (the receipt cadence as its full report,
opener/fuller via the timer/reply job), with its own #216 prompt-family safety guard.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings as _settings
from app.jobs import process_new_activity as pna
from app.models import Activity, Block
from app.services.coach.service import is_receipt_cadence, is_two_stage_prompt
from app.services.notifications import Notification
from app.services.notifications.port import NotifierPort

logger = logging.getLogger(__name__)


class PostActivityCadence:
    """Interface for a post-activity cadence. Each method handles one event; the
    base no-ops every event, so an adapter only overrides the ones it acts on."""

    async def on_ingest(
        self, *, db: Session, activity: Activity, block: Block, notifier: NotifierPort
    ) -> Optional[Notification]:
        return None

    async def on_block_complete(
        self, *, db: Session, block_id: str, activity_id: str, notifier: NotifierPort
    ) -> Optional[Notification]:
        return None

    def on_reply(self, *, db: Session, activity_id) -> bool:
        return False

    def on_done(self, *, db: Session, activity_id) -> bool:
        return False


class SingleShotCadence(PostActivityCadence):
    """The prior per-activity pipeline: one report, one notification, inline on
    ingest (coach_message_v1 / coach_report_v*). Every later event is a no-op —
    there is no exchange, opener, or fuller. on_block_complete also covers the AC6
    rollback case (a two-stage block-complete check fires after a flip back to a
    single-shot prompt): it no-ops BEFORE any DB work, as the prior gate did."""

    async def on_ingest(self, *, db, activity, block, notifier):
        return await pna._run_single_shot(
            db=db,
            activity=activity,
            strava_activity_id=activity.strava_activity_id,
            notifier=notifier,
        )

    async def on_block_complete(self, *, db, block_id, activity_id, notifier):
        logger.info(
            "Skipping block-complete check for block %s: active prompt %s is single-shot",
            block_id, _settings.COACH_PROMPT_ID,
        )
        return None


class OpenerFullerCadence(PostActivityCadence):
    """A4 two-stage Exchange (ADR 0010): schedule the block-complete debounce on
    ingest, run the LLM opener when it fires (on the block's primary), and fire the
    conditional fuller turn early from a reply while the exchange is open."""

    async def on_ingest(self, *, db, activity, block, notifier):
        pna._schedule_block_complete(str(block.id), str(activity.id))
        logger.info(
            "Scheduled block-complete check for block %s (activity %s) in %ds",
            block.id, activity.strava_activity_id, _settings.BLOCK_GAP_SECONDS,
        )
        return None

    async def on_block_complete(self, *, db, block_id, activity_id, notifier):
        resolved = pna._resolve_completed_block(db, block_id, activity_id)
        if resolved is None:
            return None
        primary, exchange = resolved
        return await pna._run_opener_stage(
            db=db, activity=primary, exchange=exchange, notifier=notifier
        )

    def on_reply(self, *, db, activity_id):
        return pna._enqueue_reply_fuller(db, activity_id)


class ReceiptCadence(PostActivityCadence):
    """#296 receipt cadence (ADR 0018): an instant deterministic receipt on ingest,
    then the full LLM report when the block-complete debounce fires (the no-"done"
    path) or after a "done" tap. A reply only records the check-in (no early fire),
    so the report covers the whole session."""

    async def on_ingest(self, *, db, activity, block, notifier):
        receipt = await pna._send_receipt(
            db=db, activity=activity, block=block, notifier=notifier
        )
        pna._schedule_block_complete(str(block.id), str(activity.id))
        logger.info(
            "Sent receipt + scheduled full-report check for block %s (activity %s) in %ds",
            block.id, activity.strava_activity_id, _settings.BLOCK_GAP_SECONDS,
        )
        return receipt

    async def on_block_complete(self, *, db, block_id, activity_id, notifier):
        resolved = pna._resolve_completed_block(db, block_id, activity_id)
        if resolved is None:
            return None
        primary, _exchange = resolved
        return await pna.process_fuller_turn(db=db, activity=primary, notifier=notifier)

    def on_done(self, *, db, activity_id):
        return pna._record_done_and_schedule(db, activity_id)


def get_active_cadence(settings) -> PostActivityCadence:
    """The cadence for the active configuration, resolved AT CALL TIME so a config
    flip is a zero-code-change rollback. Receipt requires the flag AND a two-stage
    prompt; opener/fuller is any other two-stage prompt; everything else is
    single-shot."""
    if is_receipt_cadence(settings.COACH_PROMPT_ID):
        return ReceiptCadence()
    if is_two_stage_prompt(settings.COACH_PROMPT_ID):
        return OpenerFullerCadence()
    return SingleShotCadence()
