"""Pipeline job for a fresh activity.

Both the Strava webhook (`aspect_type=create`) and the polling fallback enqueue
`process_new_activity_job`. Under the A4 two-stage prompt (coach_message_v2) it
runs the OPENER stage (ingest -> analyze -> opener generate+store -> notify opener
-> conditionally schedule the fuller turn via rq-scheduler); the conditional fuller
turn runs later in the separate, idempotent `fuller_turn_job`, fired by the timer
or early by a reply. Under any single-shot prompt it runs the prior pipeline
(ingest -> analyze -> coach report -> notify), so flipping COACH_PROMPT_ID back is
a zero-code-change rollback (AC8).

Per-stage dedup uses the Activity sentinels: `opener_notification_sent_at` (opener)
and `coach_notification_sent_at` (fuller, also the fuller job's idempotency guard).
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import queue
from app.db.session import SessionLocal
from app.models import Activity, StravaAccount
from app.services.analysis import analyze_with_streams
from app.services.analysis.classifier import Classification, compose_headline
from app.services.coach.output_contract import is_opener_only
from app.services.coach.service import (
    generate_fuller,
    generate_opener,
    get_active_report_row,
    get_or_generate_coach_report,
    is_two_stage_prompt,
)
from app.schemas.coach import CoachReportRead
from app.services.notifications import (
    Notification,
    build_coach_notification,
    get_notifier,
)
from app.services.notifications.port import NotifierPort
from app.services.strava_ingestion import (
    StravaPort,
    get_strava_port,
    ingest_activity_by_id,
)

logger = logging.getLogger(__name__)


def _notify(
    db: Session,
    activity: Activity,
    *,
    report: CoachReportRead,
    stage: str,
    sentinel_attr: str,
    notifier: NotifierPort,
) -> Optional[Notification]:
    """Send one stage's notification, deduped by its own Activity sentinel.

    Skips when the stage sentinel is already set (at-most-once per activity per
    stage). Builds the channel-shaped Notification (stage-aware: opener prose vs
    fuller message), sends it, and on success sets the sentinel. On a send failure
    the sentinel is left null so the activity stays re-sendable, mirroring the
    prior single-shot behaviour (#114). Returns the Notification sent, or None.
    """
    if getattr(activity, sentinel_attr) is not None:
        logger.info(
            "Skipping %s notification for activity %s: already sent at %s",
            stage, activity.strava_activity_id, getattr(activity, sentinel_attr),
        )
        return None

    headline = compose_headline(activity, Classification.from_metrics(activity.metrics))
    notification = build_coach_notification(
        report=report,
        headline=headline,
        distance_m=activity.distance_m or 0,
        app_base_url=settings.APP_BASE_URL,
        stage=stage,
    )
    if notification is None:
        logger.info(
            "Skipping %s notification for activity %s: no channel configured",
            stage, activity.strava_activity_id,
        )
        return None

    try:
        notifier.send(notification)
    except Exception:
        logger.exception(
            "%s notification send failed for activity %s; sentinel left unset",
            stage, activity.strava_activity_id,
        )
        return None

    setattr(activity, sentinel_attr, datetime.now(timezone.utc))
    db.add(activity)
    db.commit()
    return notification


async def process_new_activity(
    *,
    db: Session,
    account: StravaAccount,
    strava_activity_id: int,
    strava_port: StravaPort,
    notifier: NotifierPort,
) -> Optional[Notification]:
    """Ingest + analyze the activity, then run the opener stage (two-stage prompt)
    or the single-shot pipeline (rollback prompts). Returns the Notification sent
    (the opener's, under two-stage), or None if skipped."""
    activity = await ingest_activity_by_id(
        db, account, strava_port, strava_activity_id
    )
    await analyze_with_streams(db, str(activity.id))

    if is_two_stage_prompt(settings.COACH_PROMPT_ID):
        return await _run_opener_stage(
            db=db, activity=activity, strava_activity_id=strava_activity_id, notifier=notifier
        )
    return await _run_single_shot(
        db=db, activity=activity, strava_activity_id=strava_activity_id, notifier=notifier
    )


async def _run_single_shot(
    *, db: Session, activity: Activity, strava_activity_id: int, notifier: NotifierPort
) -> Optional[Notification]:
    """The prior single-shot pipeline (coach_message_v1 / coach_report_v*): one
    report, one notification, gated by `coach_notification_sent_at`."""
    report = await get_or_generate_coach_report(db, str(activity.id))
    if report is None:
        logger.info(
            "Skipping notification for activity %s: no coach report", strava_activity_id
        )
        return None

    db.refresh(activity)
    # Read the *active*-version row: prior versions may be retained alongside it,
    # so a version-unaware query could gate notification on the wrong report.
    coach_row = get_active_report_row(db, activity.id)
    if coach_row is None or coach_row.is_fallback:
        logger.info(
            "Skipping notification for activity %s: report is fallback or missing",
            strava_activity_id,
        )
        return None

    return _notify(
        db, activity, report=report, stage="fuller",
        sentinel_attr="coach_notification_sent_at", notifier=notifier,
    )


async def _run_opener_stage(
    *, db: Session, activity: Activity, strava_activity_id: int, notifier: NotifierPort
) -> Optional[Notification]:
    """A4 stage one: generate + notify the opener, then conditionally schedule the
    fuller turn. Deduped by `opener_notification_sent_at` (the opener fires at most
    once per activity)."""
    db.refresh(activity)
    if activity.opener_notification_sent_at is not None:
        logger.info(
            "Opener already sent for activity %s; skipping", strava_activity_id
        )
        return None

    result = await generate_opener(db, str(activity.id))
    if result is None or result.report is None:
        logger.info("No opener generated for activity %s", strava_activity_id)
        return None

    db.refresh(activity)

    # Schedule the conditional fuller turn FIRST, on the salience decision (the
    # opener LLM's judgment OR-ed with the deterministic safety override; a fallback
    # opener also schedules a recovery fuller). Scheduling before notifying means a
    # crash in between still leaves the fuller scheduled. Independent of opener-
    # notify success: scheduling is about salience, not delivery. The timer is
    # idempotent against an early reply via the fuller sentinel (ADR 0010 chose
    # idempotency over racy timer cancellation).
    if result.schedule_fuller_turn:
        _schedule_fuller_turn(str(activity.id))
        logger.info(
            "Scheduled fuller turn for activity %s in %ds",
            strava_activity_id, settings.EXCHANGE_STAGE2_DELAY_SECONDS,
        )

    # Notify the opener — even a fallback opener, whose templated prose ("Nice work
    # … I'll follow up shortly") is benign and non-medical, so a red-flag run whose
    # opener LLM hiccuped still gets a non-silent immediate opener (AC3) and the
    # scheduled fuller carries the substantive coaching.
    notification = _notify(
        db, activity, report=result.report, stage="opener",
        sentinel_attr="opener_notification_sent_at", notifier=notifier,
    )

    return notification


def _schedule_fuller_turn(activity_id: str) -> None:
    """Enqueue the fuller-turn job after the stage-two delay via rq-scheduler (same
    pattern as the backfill self-pacing), so the worker stays free between stages.
    A reply may fire the fuller earlier; the fuller job's sentinel makes the late
    timer a harmless no-op (no timer cancellation, which rq-scheduler does racily)."""
    from redis import Redis
    from rq_scheduler import Scheduler

    scheduler = Scheduler(connection=Redis.from_url(settings.REDIS_URL))
    scheduler.enqueue_in(
        timedelta(seconds=settings.EXCHANGE_STAGE2_DELAY_SECONDS),
        fuller_turn_job,
        activity_id,
    )


def maybe_enqueue_fuller_turn(db: Session, activity_id) -> bool:
    """Reply path (A4 D6): if the activity's two-stage exchange is OPEN, enqueue the
    fuller turn early. Called when a runner replies — a CheckIn or a CoachChatMessage.

    The exchange is open when (in-band, no notification-sentinel dependency, so it
    works for a NoOp local notifier too): the active report row is an opener-only
    row, the fuller is not yet done (coach_notification_sent_at null), and the
    opener row was created within EXCHANGE_REPLY_WINDOW_SECONDS — so a reply on an
    activity whose opener is stale never spins up a fresh exchange (AC4). Idempotent
    against the racing timer (the fuller job's own sentinel guard); a deterministic
    job_id collapses rapid repeated replies while the job is still queued.
    Best-effort: a Redis hiccup never breaks the reply request. Returns True if it
    enqueued the fuller turn.
    """
    if not is_two_stage_prompt(settings.COACH_PROMPT_ID):
        return False
    activity_uuid = activity_id if isinstance(activity_id, uuid.UUID) else uuid.UUID(str(activity_id))
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if activity is None or activity.coach_notification_sent_at is not None:
        return False  # no activity, or the fuller turn is already done/closed

    row = get_active_report_row(db, activity_uuid)
    if row is None or not is_opener_only(row.report):
        return False  # no open opener-stage exchange to advance

    created = row.created_at
    if created is None:
        return False
    created_aware = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created_aware).total_seconds()
    if age > settings.EXCHANGE_REPLY_WINDOW_SECONDS:
        return False  # opener too old: a reply on a stale activity (AC4)

    try:
        queue.enqueue(fuller_turn_job, str(activity_uuid), job_id=f"fuller_{activity_uuid}")
    except Exception:
        logger.exception(
            "failed to enqueue reply-triggered fuller turn for activity %s", activity_uuid
        )
        return False
    return True


async def process_fuller_turn(
    *, db: Session, activity: Activity, notifier: NotifierPort
) -> Optional[Notification]:
    """A4 stage two: generate (or finalize) the fuller turn and notify it.

    Idempotent on the fuller sentinel (`coach_notification_sent_at`): a reply-fired
    run and the timer-fired run cannot double-send — the second no-ops. generate_fuller
    fills the opener's evolving row in place and fires the learning loop on
    completion; a notify-retry re-sends the cached fuller without re-generating.
    A fallback fuller is not notified (and leaves the sentinel null), consistent
    with the single-shot fallback rule."""
    db.refresh(activity)
    if activity.coach_notification_sent_at is not None:
        logger.info(
            "Fuller turn already notified for activity %s; no-op", activity.strava_activity_id
        )
        return None

    read = await generate_fuller(db, str(activity.id))
    if read is None:
        logger.info("No fuller report for activity %s", activity.strava_activity_id)
        return None

    db.refresh(activity)
    coach_row = get_active_report_row(db, activity.id)
    if coach_row is None or coach_row.is_fallback:
        logger.info(
            "Skipping fuller notification for activity %s: report is fallback or missing",
            activity.strava_activity_id,
        )
        return None

    return _notify(
        db, activity, report=read, stage="fuller",
        sentinel_attr="coach_notification_sent_at", notifier=notifier,
    )


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
            process_fuller_turn(db=db, activity=activity, notifier=get_notifier())
        )
    finally:
        db.close()
