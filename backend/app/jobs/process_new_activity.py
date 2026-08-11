"""Pipeline job for a fresh activity.

Both the Strava webhook (`aspect_type=create`) and the user-triggered self-heal
check (#123/ADR 0006, which replaced the removed polling fallback) enqueue
`process_new_activity_job`. It runs ingest -> analyze -> block assignment and then
hands off to the active POST-ACTIVITY CADENCE (`app/jobs/cadence/`), which owns what
happens next: schedule a block-complete check and open an exchange (two-stage), send
an instant receipt (#296), or run the prior per-activity report inline (single-shot).
Flipping COACH_PROMPT_ID / COACH_RECEIPT_CADENCE is therefore a zero-code-change
rollback (AC6) — the cadence is resolved at dispatch time, never baked into a job's
args.

This module is the JOB layer: the RQ entrypoints, the ingest orchestration, and the
stable delegators other modules import (`process_block_complete`,
`maybe_enqueue_fuller_turn`, `mark_done_and_schedule`). The cadence BODIES live in
`app/jobs/cadence/*` and the shared side effects in `app/jobs/exchange_ops.py` (#696);
before that split they were `_private` helpers here that the cadence adapters called
back across the module boundary, so no cadence could be read in one place.

The RQ entrypoints below must KEEP this module path. RQ serializes a deferred job as
its `module.function` string, so relocating them would strand every block-complete
check (scheduled up to BLOCK_GAP_SECONDS out) and fuller timer (up to
EXCHANGE_STAGE2_DELAY_SECONDS out) already sitting in Redis across a deploy.

Per-stage dedup lives on the block's `exchanges` row (`opener_sent_at`,
`fuller_sent_at`); `opened_at` marks the lifecycle (opener generated) independent
of delivery. The legacy Activity sentinels are no longer written by the
two-stage path; the single-shot rollback path still uses
`Activity.coach_notification_sent_at` because per-activity dedup needs a
per-activity store.
"""

import asyncio
import logging
import uuid
from typing import Optional

from rq import Retry
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.jobs import exchange_ops
from app.jobs.cadence import get_active_cadence
from app.jobs.reanalyze_history import reanalyze_activity_if_streams
from app.models import Activity, StravaAccount
from app.services.analysis import analyze_with_streams
from app.services.coach.service import get_or_generate_coach_report
from app.services.notifications import Notification, get_notifier
from app.services.notifications.port import NotifierPort
from app.services.strava_ingestion import (
    StravaPort,
    get_strava_port,
    ingest_activity_by_id,
)

logger = logging.getLogger(__name__)

# #215: the recovery trigger for a worker crash mid-pipeline. Once ingest has
# persisted the Activity row, the self-heal check treats it as known and never re-picks it,
# so without a retry a crash between ingest and the opener notify/schedule
# permanently strands the exchange. Every stage is idempotent on re-entry
# (opener row reuse, per-stage sentinels, fuller sentinel), so a re-run is safe.
# RQ 2.x honours retries_left for failed, horse-dead, AND abandoned jobs (the
# whole-worker SIGKILL of a Railway deploy), re-enqueueing on registry cleanup.
PIPELINE_RETRY = Retry(max=3, interval=[60, 300, 900])


async def process_new_activity(
    *,
    db: Session,
    account: StravaAccount,
    strava_activity_id: int,
    strava_port: StravaPort,
    notifier: NotifierPort,
) -> Optional[Notification]:
    """Ingest + analyze the activity and assign its Block, then hand off to the
    active cadence's `on_ingest`. Returns the Notification sent (the single-shot
    report or the #296 receipt), or None."""
    activity = await ingest_activity_by_id(
        db, account, strava_port, strava_activity_id
    )
    await analyze_with_streams(db, str(activity.id))

    # #830: tick off whatever planned session this run satisfies, before the
    # cadence speaks. Guarded because the schedule is an addition to the
    # pipeline, not a dependency of it — a runner's run must still be ingested,
    # analysed and reported on if the schedule cannot answer.
    _autocomplete_planned_session(db, activity)

    block = exchange_ops.ensure_block(db, activity)

    return await get_active_cadence(settings).on_ingest(
        db=db, activity=activity, block=block, notifier=notifier
    )


def _autocomplete_planned_session(db: Session, activity) -> None:
    """Credit this activity to the planned session it satisfies, if any.

    One of three routes to the same write (#830) — the other two are the runner's
    tap and telling the coach. Never fatal: a schedule fault must not cost the
    runner their report.
    """
    try:
        from app.services.schedule.completion import complete_from_activity

        complete_from_activity(db, activity)
    except Exception:  # noqa: BLE001 — the schedule is additive to the pipeline
        logger.exception(
            "schedule: auto-completion failed for activity %s", activity.id
        )


async def process_block_complete(
    *, db: Session, block_id: str, activity_id: str, notifier: NotifierPort
) -> Optional[Notification]:
    """Block-complete check: under the two-stage cadence open the block's exchange
    (LLM opener); under the #296 receipt cadence fire the FULL report.

    Scheduled at +BLOCK_GAP_SECONDS by every processed activity (and by a "done"
    tap); idempotency over cancellation: the check no-ops unless its activity is
    still the block's LAST member (a newer member means a fresh check is already
    pending). Gated on the two-stage prompt so a check pending across a rollback to
    a single-shot prompt is inert (AC6); the receipt-vs-opener branch is decided at
    FIRE time, so a check pending across a cadence-flag flip does the now-current
    thing rather than a stale one.
    """
    return await get_active_cadence(settings).on_block_complete(
        db=db, block_id=block_id, activity_id=activity_id, notifier=notifier
    )


def maybe_enqueue_fuller_turn(db: Session, activity_id) -> bool:
    """Reply path entry point — called when a runner replies (a CheckIn or a
    CoachChatMessage) on ANY block member. Delegates to the active cadence: only the
    opener/fuller cadence fires the fuller turn early; single-shot has no exchange and
    the #296 receipt cadence waits for the block-complete timer or a "done" tap (so the
    report covers the whole session). Kept here as the stable import for checkins.py /
    chat.py and as the patch target their tests use."""
    return get_active_cadence(settings).on_reply(db=db, activity_id=activity_id)


def mark_done_and_schedule(db: Session, activity_id) -> bool:
    """#296 "done" tap entry point — called from the Telegram "done" callback on any
    block member. Delegates to the active cadence: only the receipt cadence records the
    completion and schedules the full report (single-shot and opener/fuller have no
    "done" affordance). Kept here as the stable import for webhooks.py."""
    return get_active_cadence(settings).on_done(db=db, activity_id=activity_id)


def process_new_activity_job(
    strava_athlete_id: int, strava_activity_id: int
) -> None:
    """RQ entrypoint. Opens its own DB session and uses the active ports."""
    db = SessionLocal()
    try:
        account = (
            db.query(StravaAccount)
            .filter(StravaAccount.strava_athlete_id == strava_athlete_id)
            .first()
        )
        if account is None:
            logger.warning(
                "process_new_activity_job: unknown athlete %s", strava_athlete_id
            )
            return

        asyncio.run(
            process_new_activity(
                db=db,
                account=account,
                strava_activity_id=strava_activity_id,
                strava_port=get_strava_port(),
                notifier=get_notifier(),
            )
        )
    finally:
        db.close()


def block_complete_job(block_id: str, activity_id: str) -> None:
    """RQ entrypoint for the A1 block-complete check (scheduled at
    +BLOCK_GAP_SECONDS by every processed activity). Opens its own session and
    the active notifier; stale checks no-op inside process_block_complete."""
    db = SessionLocal()
    try:
        asyncio.run(
            process_block_complete(
                db=db, block_id=block_id, activity_id=activity_id, notifier=get_notifier()
            )
        )
    finally:
        db.close()


def regenerate_report_job(activity_id: str) -> None:
    """RQ entrypoint for an on-demand coach-report regeneration (the UI "Re-run"
    button, #260). Runs the force regeneration in the WORKER so the slow two-stage
    LLM call (30-120s) never blocks the HTTP request or exceeds the gateway timeout
    — the request only enqueues this job and the frontend polls for the result.

    #646 fix-refresh: re-derive analysis from the activity's ALREADY-STORED streams
    first (cheap, local, NO Strava call), so the regenerated report reflects the
    current deployed analysis code rather than a stale `DerivedMetric`. Skips
    gracefully when there are no stored streams, and a re-analysis failure never
    blocks the regeneration (it proceeds with the existing metrics). The force regen
    itself is non-destructive: it archives the prior report and mints a new current
    row stamped with the regeneration date + memory as-of date."""
    db = SessionLocal()
    try:
        try:
            reanalyzed = reanalyze_activity_if_streams(db, str(activity_id))
            logger.info(
                "regenerate_report_job re-analysis %s for activity %s",
                "ran" if reanalyzed else "skipped (no stored streams)",
                activity_id,
            )
        except Exception:  # noqa: BLE001 — re-analysis must never block the regen
            db.rollback()
            logger.exception(
                "regenerate_report_job re-analysis failed for activity %s; "
                "proceeding with existing metrics",
                activity_id,
            )
        asyncio.run(get_or_generate_coach_report(db, str(activity_id), force=True))
    finally:
        db.close()


def fuller_turn_job(activity_id: str) -> None:
    """RQ entrypoint for the A4 fuller turn (scheduled by the opener via the timer,
    or enqueued early by a reply). Opens its own session and the active notifier."""
    db = SessionLocal()
    try:
        activity_uuid = activity_id if isinstance(activity_id, uuid.UUID) else uuid.UUID(str(activity_id))
        activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
        if activity is None:
            logger.warning("fuller_turn_job: unknown activity %s", activity_id)
            return
        asyncio.run(
            exchange_ops.run_fuller_turn(
                db=db, activity=activity, notifier=get_notifier()
            )
        )
    finally:
        db.close()
