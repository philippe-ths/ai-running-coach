"""Shared exchange plumbing the cadences orchestrate (#696).

Every post-activity cadence (single-shot, opener/fuller, receipt — see
`app/jobs/cadence/`) is assembled from the same small set of side effects:
notify one stage and mark its sentinel, resolve or assign an activity's block,
decide whether a block-complete check is still live, schedule a deferred check,
and run the fuller turn. Those operations are cadence-AGNOSTIC: they encode the
at-most-once notification invariant, the block/exchange lookups, and the RQ
scheduling conventions, not the policy of which cadence does what when.

Before #696 they lived as `_private` helpers in `process_new_activity` and the
cadence adapters reached back across the module boundary to call them, so no
cadence could be read in one place and the seam sat above logic it did not
encapsulate. They live here now: the cadence modules own the POLICY (which
effects, in what order, under what guard), this module owns the MECHANICS.

Import direction is one-way — `cadence/*` imports this module, this module never
imports a cadence. The one exception is deliberate: `schedule_block_complete` and
`schedule_fuller_turn` resolve their RQ entrypoints from `process_new_activity`
lazily, inside the call. Those entrypoints CANNOT move here: RQ serializes a
deferred job as its `module.function` path, so relocating them would strand every
block-complete check (up to BLOCK_GAP_SECONDS) and fuller timer (up to
EXCHANGE_STAGE2_DELAY_SECONDS) already sitting in Redis across a deploy.

Call these through the module (`exchange_ops.notify_stage(...)`), not via
`from ... import notify_stage` — the tests patch them as module attributes, and a
name bound at import time would silently escape the patch.
"""

import logging
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity, Block, Exchange
from app.schemas.coach import CoachReportRead
from app.services.analysis.classifier import Classification, compose_headline
from app.services.blocks import activity_end, assign_activity_to_block, is_run_activity
from app.services.coach import exchange_lifecycle as lifecycle
from app.services.coach.service import (
    generate_fuller,
    get_active_report_row,
    is_two_stage_prompt,
)
from app.services.notifications import (
    Notification,
    build_coach_notification,
    resolve_recipient,
)
from app.services.notifications.port import NotifierPort

logger = logging.getLogger(__name__)


def notify_stage(
    db: Session,
    activity: Activity,
    *,
    report: CoachReportRead,
    stage: str,
    sentinel_attr: str,
    notifier: NotifierPort,
    sentinel_obj=None,
    presend_claimed: bool = False,
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

    `presend_claimed` (#506, fuller turn only): the caller already atomically
    claimed the sentinel pre-send via `lifecycle.fuller_claim`, so the sentinel
    IS the at-most-once claim — skip the already-sent guard (we own it) and do
    NOT re-mark on success (the claim already set it). RELEASING a claim this
    function did not take is not its business: returning None is the whole signal,
    and `lifecycle.fuller_claim` releases on any exit the caller did not `keep()`
    (#740). Releasing here as well was a second owner of the same invariant.
    """
    sentinel_obj = sentinel_obj if sentinel_obj is not None else activity
    if not presend_claimed and lifecycle.notification_already_sent(sentinel_obj, sentinel_attr):
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
        # P2.4 (#120): route to the activity owner's bound channel; an unbound
        # non-owner resolves to None (suppressed), and the global fallback is kept
        # only for the identified owner or a db-proven single-user deploy (#600).
        recipient=resolve_recipient(activity.user, db=db),
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

    # Under the #506 pre-send claim the sentinel was already set atomically by the
    # caller; do not re-mark (it would just rewrite the same value).
    if not presend_claimed:
        lifecycle.mark_notification_sent(db, sentinel_obj, sentinel_attr)
    return notification


def ensure_block(db: Session, activity: Activity) -> Block:
    """The activity's Block, assigning one if ingestion has not yet (e.g. an
    activity ingested by code that predates A1 block assignment)."""
    if activity.block_id is not None:
        return db.query(Block).filter(Block.id == activity.block_id).one()
    return assign_activity_to_block(db, activity)


def is_run_for_auto_report(activity: Activity) -> bool:
    """Whether an activity counts as a run for AUTOMATIC coach-report gating (#643).

    A coach report is only auto-generated for runs; every activity still gets its
    receipt / check-in notification, and on-demand regeneration stays ungated.
    Delegates to `blocks.is_run_activity`, the single run-family predicate shared
    with `pick_primary` (the report anchor), so the gate agrees with which activity
    the report generates on. The run family covers the coarse `type == "Run"`
    (ordinary, trail, treadmill) plus connected-platform "VirtualRun" (Zwift,
    Peloton), which #644 brought into scope alongside the anchor."""
    return is_run_activity(activity)


def exchange_for_activity(db: Session, activity: Activity) -> Optional[Exchange]:
    """The exchange owning this activity's block, via lazy block assignment."""
    block = ensure_block(db, activity)
    return lifecycle.get_exchange_for_block(db, block.id)


def resolve_completed_block(
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

    exchange = lifecycle.ensure_exchange_for_block(db, block)

    primary = db.query(Activity).filter(Activity.id == block.primary_activity_id).one()

    # #643: auto-generate a coach report only when the block contains a run. Because
    # `pick_primary` prefers a run, a non-run primary means the block has no run, so
    # neither the block-complete timer nor a "done" tap (both routed here) produces a
    # report. The receipt already fired on ingest, and on-demand regeneration is a
    # separate ungated path.
    if not is_run_for_auto_report(primary):
        logger.info(
            "block_complete: block %s primary %s is not a run (%s); no auto coach report",
            block_id, primary.strava_activity_id, primary.type,
        )
        return None

    return primary, exchange


def schedule_block_complete(block_id: str, activity_id: str) -> None:
    """Enqueue the block-complete check after the grouping gap (the fuller-timer
    pattern): every processed activity schedules one; stale checks no-op instead
    of being cancelled.

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006). The job entrypoint
    is imported lazily and still resolves to `app.jobs.process_new_activity`, whose
    module path is part of the on-Redis job payload and must not move (#696)."""
    from app.core.queue import queue
    from app.jobs.process_new_activity import block_complete_job

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


def schedule_fuller_turn(activity_id: str) -> None:
    """Enqueue the fuller-turn job after the stage-two delay (same pattern as the
    backfill self-pacing), so the worker stays free between stages. A reply may
    fire the fuller earlier; the fuller job's sentinel makes the late timer a
    harmless no-op (no timer cancellation).

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006). The job entrypoint
    is imported lazily and still resolves to `app.jobs.process_new_activity`, whose
    module path is part of the on-Redis job payload and must not move (#696)."""
    from app.core.queue import queue
    from app.jobs.process_new_activity import fuller_turn_job

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


async def run_fuller_turn(
    *, db: Session, activity: Activity, notifier: NotifierPort
) -> Optional[Notification]:
    """A4 stage two: generate (or finalize) the fuller turn and notify it.

    Shared by BOTH two-stage cadences — the opener/fuller cadence reaches it via the
    timer/reply job, the receipt cadence invokes it as the block's full report — so it
    lives with the mechanics rather than in either cadence module.

    At-most-once on the fuller sentinel (`fuller_sent_at`): a reply-fired run and the
    timer-fired run cannot double-send — the loser no-ops. The guard is an ATOMIC claim
    (#506): the old `is_closed` read was a non-atomic check-then-act with the slow
    `generate_fuller` sitting between it and the notification sentinel, so under multiple
    workers both triggers could pass the read, both generate, and both notify. We now
    claim `fuller_sent_at` via a conditional UPDATE BEFORE generating, so the database
    serializes the two triggers and only the winner proceeds. generate_fuller fills the
    opener's evolving row in place and fires the learning loop on completion. A fallback
    fuller / missing report / no-channel / send failure / RAISED exception RELEASES the
    claim (sentinel back to null) so the stage stays re-sendable, consistent with the
    single-shot fallback rule (#114) — the claim..send window is `lifecycle.fuller_claim`,
    which releases on any exit this job did not `keep()`, so a crash mid-generation cannot
    strand the turn CLOSED-but-unsent."""
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

    exchange = exchange_for_activity(db, activity)
    if exchange is None:
        logger.warning(
            "Fuller turn for activity %s: no exchange row; skipping",
            activity.strava_activity_id,
        )
        return None
    db.refresh(exchange)

    # #506/#740: atomically claim the turn for the length of this block. A non-winning
    # concurrent trigger (or a late timer after an early reply already closed the
    # exchange) loses the claim and bails BEFORE generating — exactly one generation +
    # one notification per exchange. The claim is released on EVERY exit that does not
    # `keep()` it: a None read, a fallback/missing report, a no-channel/send-failure
    # `notify_stage` return, OR a raised exception. That release rule is the lifecycle
    # module's (see `fuller_claim`); this function just says when the turn was earned.
    with lifecycle.fuller_claim(db, exchange) as claim:
        if not claim.won:
            logger.info(
                "Fuller turn already claimed/notified for block %s; no-op",
                exchange.block_id,
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

        notification = notify_stage(
            db, activity, report=read, stage="fuller",
            sentinel_attr="fuller_sent_at", notifier=notifier, sentinel_obj=exchange,
            presend_claimed=True,
        )
        # Kept only after a real successful send, so the happy path stays CLOSED and
        # never re-sends.
        if notification is not None:
            claim.keep()
        return notification
