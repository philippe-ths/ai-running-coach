"""Pipeline job for a fresh activity.

Both the Strava webhook (`aspect_type=create`) and the polling fallback enqueue
`process_new_activity_job`. Under the two-stage prompt (coach_message_v2) it runs
ingest -> analyze -> block assignment, then schedules a BLOCK-COMPLETE check at
+BLOCK_GAP_SECONDS (A1, ADR 0011) instead of opening the exchange inline: the
gap that groups activities into a Block doubles as the debounce that decides the
block is finished. `block_complete_job` no-ops when superseded (a newer member's
check owns the block — idempotency over cancellation, the same pattern as the
fuller timer) and otherwise runs the opener stage on the block's PRIMARY
activity; the conditional fuller turn runs later in the separate, idempotent
`fuller_turn_job`, fired by the timer or early by a reply. Under any single-shot
prompt it runs the prior per-activity pipeline (ingest -> analyze -> coach
report -> notify) untouched, so flipping COACH_PROMPT_ID back is a
zero-code-change rollback (AC6).

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
from datetime import timedelta
from typing import Optional

from rq import Retry
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import queue
from app.db.session import SessionLocal
from app.models import Activity, Block, Exchange, StravaAccount
from app.models.coaching_relationship import CoachingRelationship
from app.services.analysis import analyze_with_streams
from app.services.analysis.classifier import Classification, compose_headline
from app.services.blocks import activity_end, assign_activity_to_block
from app.services.coach import exchange_lifecycle as lifecycle
from app.services.coach.receipt import build_receipt
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
    build_receipt_notification,
    get_notifier,
    resolve_recipient,
)
from app.services.notifications.port import NotifierPort
from app.services.strava_ingestion import (
    StravaPort,
    get_strava_port,
    ingest_activity_by_id,
)

logger = logging.getLogger(__name__)

# #215: the recovery trigger for a worker crash mid-pipeline. Once ingest has
# persisted the Activity row, polling treats it as known and never re-picks it,
# so without a retry a crash between ingest and the opener notify/schedule
# permanently strands the exchange. Every stage is idempotent on re-entry
# (opener row reuse, per-stage sentinels, fuller sentinel), so a re-run is safe.
# RQ 2.x honours retries_left for failed, horse-dead, AND abandoned jobs (the
# whole-worker SIGKILL of a Railway deploy), re-enqueueing on registry cleanup.
PIPELINE_RETRY = Retry(max=3, interval=[60, 300, 900])


def _notify(
    db: Session,
    activity: Activity,
    *,
    report: CoachReportRead,
    stage: str,
    sentinel_attr: str,
    notifier: NotifierPort,
    sentinel_obj=None,
) -> Optional[Notification]:
    """Send one stage's notification, deduped by its own stage sentinel.

    The sentinel lives on `sentinel_obj` — the block's Exchange row under the
    two-stage path (A1), or the Activity itself on the single-shot rollback
    path. Skips when the stage sentinel is already set (at-most-once per
    exchange per stage). Builds the channel-shaped Notification (stage-aware:
    opener prose vs fuller message), sends it, and on success sets the
    sentinel. On a send failure the sentinel is left null so the stage stays
    re-sendable, mirroring the prior single-shot behaviour (#114). Returns the
    Notification sent, or None.
    """
    sentinel_obj = sentinel_obj if sentinel_obj is not None else activity
    if lifecycle.notification_already_sent(sentinel_obj, sentinel_attr):
        logger.info(
            "Skipping %s notification for activity %s: already sent at %s",
            stage, activity.strava_activity_id, getattr(sentinel_obj, sentinel_attr),
        )
        return None

    headline = compose_headline(activity, Classification.from_metrics(activity.metrics))
    notification = build_coach_notification(
        report=report,
        headline=headline,
        distance_m=activity.distance_m or 0,
        app_base_url=settings.APP_BASE_URL,
        stage=stage,
        # P2.4 (#120): route to the activity owner's bound channel; None falls
        # back to the configured global recipient (single-user back-compat).
        recipient=resolve_recipient(activity.user),
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

    lifecycle.mark_notification_sent(db, sentinel_obj, sentinel_attr)
    return notification


async def process_new_activity(
    *,
    db: Session,
    account: StravaAccount,
    strava_activity_id: int,
    strava_port: StravaPort,
    notifier: NotifierPort,
) -> Optional[Notification]:
    """Ingest + analyze the activity and assign its Block, then schedule the
    block-complete check (two-stage prompt) or run the single-shot pipeline
    (rollback prompts). Under two-stage nothing is generated or notified here —
    the opener fires from `block_complete_job` once the block has been quiet
    for BLOCK_GAP_SECONDS (A1). Returns the Notification sent (single-shot
    only), or None."""
    activity = await ingest_activity_by_id(
        db, account, strava_port, strava_activity_id
    )
    await analyze_with_streams(db, str(activity.id))

    block = _ensure_block(db, activity)

    # The post-activity cadence (single-shot / opener-fuller / receipt) is resolved
    # from config at dispatch time and owns what happens next — see app/jobs/cadence.py.
    # Lazy import keeps the cadence -> process_new_activity dependency one-directional.
    from app.jobs.cadence import get_active_cadence

    return await get_active_cadence(settings).on_ingest(
        db=db, activity=activity, block=block, notifier=notifier
    )


def _ensure_block(db: Session, activity: Activity) -> Block:
    """The activity's Block, assigning one if ingestion has not yet (e.g. an
    activity ingested by code that predates A1 block assignment)."""
    if activity.block_id is not None:
        return db.query(Block).filter(Block.id == activity.block_id).one()
    return assign_activity_to_block(db, activity)


def _exchange_for_activity(db: Session, activity: Activity) -> Optional[Exchange]:
    """The exchange owning this activity's block, via lazy block assignment."""
    block = _ensure_block(db, activity)
    return db.query(Exchange).filter(Exchange.block_id == block.id).first()


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


async def _send_receipt(
    *, db: Session, activity: Activity, block: Block, notifier: NotifierPort
) -> Optional[Notification]:
    """#296: send the instant, deterministic, block-aware receipt for this activity
    and open the exchange.

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
        # P2.4 (#120): route the receipt to the activity owner's bound channel;
        # None falls back to the configured global recipient.
        recipient=resolve_recipient(activity.user),
    )

    # Open the exchange on the first receipt, independent of delivery (A4 posture):
    # the reply window anchors here and must work even for a NoOp local notifier.
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).first()
    if exchange is not None:
        lifecycle.open_exchange(db, exchange)

    if notification is None:
        logger.info(
            "No receipt notification for activity %s: no channel configured",
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
    from app.jobs.cadence import get_active_cadence

    return await get_active_cadence(settings).on_block_complete(
        db=db, block_id=block_id, activity_id=activity_id, notifier=notifier
    )


def _resolve_completed_block(
    db: Session, block_id: str, activity_id: str
) -> Optional[tuple[Activity, Exchange]]:
    """Shared block-complete setup for the two-stage cadences: resolve the block,
    no-op (return None) when this check is superseded — a newer member now owns the
    block (idempotency over cancellation) — or the block is gone or empty, ensure the
    exchange row (defensive; blocks are created with theirs), and return the block's
    PRIMARY activity plus its exchange. The single-shot cadence never reaches here; it
    no-ops before any DB work."""
    block = db.query(Block).filter(Block.id == uuid.UUID(str(block_id))).first()
    if block is None:
        logger.warning("block_complete: unknown block %s", block_id)
        return None

    members = db.query(Activity).filter(Activity.block_id == block.id).all()
    if not members:
        logger.warning("block_complete: block %s has no members", block_id)
        return None
    last = max(members, key=activity_end)
    if str(last.id) != str(activity_id):
        logger.info(
            "block_complete superseded for block %s: %s is no longer the last member",
            block_id, activity_id,
        )
        return None

    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).first()
    if exchange is None:  # defensive: blocks are created with their exchange
        exchange = Exchange(user_id=block.user_id, block_id=block.id)
        db.add(exchange)
        db.commit()

    primary = db.query(Activity).filter(Activity.id == block.primary_activity_id).one()
    return primary, exchange


async def _run_opener_stage(
    *, db: Session, activity: Activity, exchange: Exchange, notifier: NotifierPort
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
        _schedule_fuller_turn(str(activity.id))
        logger.info(
            "Scheduled fuller turn for activity %s in %ds",
            activity.strava_activity_id, settings.EXCHANGE_STAGE2_DELAY_SECONDS,
        )

    # Notify the opener — even a fallback opener, whose templated prose ("Nice work
    # … I'll follow up shortly") is benign and non-medical, so a red-flag run whose
    # opener LLM hiccuped still gets a non-silent opener (AC3) and the scheduled
    # fuller carries the substantive coaching.
    notification = _notify(
        db, activity, report=result.report, stage="opener",
        sentinel_attr="opener_sent_at", notifier=notifier, sentinel_obj=exchange,
    )

    return notification


def _schedule_block_complete(block_id: str, activity_id: str) -> None:
    """Enqueue the block-complete check after the grouping gap (the fuller-timer
    pattern): every processed activity schedules one; stale checks no-op instead
    of being cancelled.

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006)."""
    from app.core.queue import queue

    # job_timeout (#264): block-complete runs the opener generation, so it needs
    # the same generous death-penalty ceiling as the rest of the coach jobs rather
    # than RQ's 180s default. RQ-native enqueue_in uses `job_timeout` (rq-scheduler
    # used `timeout`); the shared queue's default_timeout already supplies it, but
    # we pass it explicitly to keep the #264 intent legible.
    queue.enqueue_in(
        timedelta(seconds=settings.BLOCK_GAP_SECONDS),
        block_complete_job,
        block_id,
        activity_id,
        job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS,
    )


def _schedule_fuller_turn(activity_id: str) -> None:
    """Enqueue the fuller-turn job after the stage-two delay (same pattern as the
    backfill self-pacing), so the worker stays free between stages. A reply may
    fire the fuller earlier; the fuller job's sentinel makes the late timer a
    harmless no-op (no timer cancellation).

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006)."""
    from app.core.queue import queue

    # job_timeout (#264): the fuller turn is the heavy two-stage generation
    # (~120-360s), so it must outlast RQ's 180s default or the death penalty kills
    # it mid-write. RQ-native enqueue_in uses `job_timeout` (rq-scheduler used
    # `timeout`).
    queue.enqueue_in(
        timedelta(seconds=settings.EXCHANGE_STAGE2_DELAY_SECONDS),
        fuller_turn_job,
        activity_id,
        job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS,
    )


def maybe_enqueue_fuller_turn(db: Session, activity_id) -> bool:
    """Reply path entry point — called when a runner replies (a CheckIn or a
    CoachChatMessage) on ANY block member. Delegates to the active cadence: only the
    opener/fuller cadence fires the fuller turn early; single-shot has no exchange and
    the #296 receipt cadence waits for the block-complete timer or a "done" tap (so the
    report covers the whole session). Kept here as the stable import for checkins.py /
    chat.py and as the patch target their tests use."""
    from app.jobs.cadence import get_active_cadence

    return get_active_cadence(settings).on_reply(db=db, activity_id=activity_id)


def _enqueue_reply_fuller(db: Session, activity_id) -> bool:
    """Enqueue the fuller turn early when the exchange owning this activity's block is
    OPEN (the opener/fuller cadence only).

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
    exchange = db.query(Exchange).filter(Exchange.block_id == activity.block_id).first()
    if block is None or exchange is None:
        return False
    # Open (opener generated, not closed) and within the reply window: a closed or
    # stale or never-opened exchange never re-fires / spins up (AC3/AC4). The state
    # is owned by exchange_lifecycle, so the "never re-fire" guarantee lives once.
    if not lifecycle.can_fire_reply_fuller(exchange):
        return False

    primary_id = block.primary_activity_id
    try:
        queue.enqueue(fuller_turn_job, str(primary_id), job_id=f"fuller_{primary_id}")
    except Exception:
        logger.exception(
            "failed to enqueue reply-triggered fuller turn for block %s", block.id
        )
        return False
    return True


def mark_done_and_schedule(db: Session, activity_id) -> bool:
    """#296 "done" tap entry point — called from the Telegram "done" callback on any
    block member. Delegates to the active cadence: only the receipt cadence records the
    completion and schedules the full report (single-shot and opener/fuller have no
    "done" affordance). Kept here as the stable import for webhooks.py."""
    from app.jobs.cadence import get_active_cadence

    return get_active_cadence(settings).on_done(db=db, activity_id=activity_id)


def _record_done_and_schedule(db: Session, activity_id) -> bool:
    """Record the runner's explicit completion signal and schedule the full report at
    +BLOCK_GAP_SECONDS (owner-locked: "done" -> full report in 30 min; receipt cadence).

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
    exchange = db.query(Exchange).filter(Exchange.block_id == activity.block_id).first()
    if block is None or exchange is None:
        return False
    if lifecycle.is_closed(exchange):
        return False  # closed: the full report already went
    # A "done" tap cannot resurrect a stale exchange (an unopened one is allowed —
    # the receipt opens it on ingest, but a defensive mark_done opens it anyway).
    if exchange.opened_at is not None and not lifecycle.within_reply_window(exchange):
        return False

    # Record the explicit completion signal (idempotent), opening the exchange if
    # the receipt somehow had not (defensive — the receipt opens it on ingest).
    lifecycle.mark_done(db, exchange)

    last = max(
        db.query(Activity).filter(Activity.block_id == block.id).all(),
        key=activity_end,
    )
    try:
        _schedule_block_complete(str(block.id), str(last.id))
    except Exception:
        logger.exception(
            "failed to schedule done-triggered full report for block %s", block.id
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
    # #216 / AC6 rollback inertness: a fuller scheduled via enqueue_in before a
    # rollback to a single-shot prompt can still fire up to the stage-two delay
    # after the flip. Gate it like every other two-stage trigger so the stale
    # timer is a no-op instead of leaking a wrong-prompt generation + notification.
    if not is_two_stage_prompt(settings.COACH_PROMPT_ID):
        logger.info(
            "Skipping fuller turn for activity %s: active prompt %s is single-shot",
            activity.strava_activity_id, settings.COACH_PROMPT_ID,
        )
        return None

    exchange = _exchange_for_activity(db, activity)
    if exchange is None:
        logger.warning(
            "Fuller turn for activity %s: no exchange row; skipping",
            activity.strava_activity_id,
        )
        return None
    db.refresh(exchange)
    if lifecycle.is_closed(exchange):
        logger.info(
            "Fuller turn already notified for block %s; no-op", exchange.block_id
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
        sentinel_attr="fuller_sent_at", notifier=notifier, sentinel_obj=exchange,
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

    Delegates to the same force path the synchronous endpoint used, so the result
    is identical; only the place it runs changes. Idempotent enough for a double
    click: a second run just replaces the active-version row again."""
    db = SessionLocal()
    try:
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
            process_fuller_turn(db=db, activity=activity, notifier=get_notifier())
        )
    finally:
        db.close()
