from datetime import date, datetime, timedelta
from typing import Annotated, List, Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import (
    CurrentUser,
    DbSession,
    LinkedStravaAccount,
    OptionalStravaAccount,
    OwnedActivity,
    OwnedActivityDetail,
)
from app.models.user_profile import UserProfile
from app.schemas import ActivityRead, ActivityDetailRead, CheckInCreate, CheckInRead, SyncResponse, ActivityIntentUpdate, DerivedMetricRead, TrainingLoadRead
from app.services import activity_queries, analysis
from app.services.checkins import write_checkin
from app.services.intents import write_activity_intent
from app.services.analysis.classifier import Classification, compose_headline
from app.services.analysis.splits import calculate_splits
from app.services.coach import service as coach_service
from app.services.laps import project_laps
from app.services.readiness import build_readiness
from app.services.strava_ingestion import get_strava_port, ingest_recent_activities

router = APIRouter()


@router.post("/activities/{activity_id}/process_deep", response_model=DerivedMetricRead)
async def process_activity_deep(
    activity: OwnedActivity,
    db: DbSession,
):
    """
    Fetches full streams from Strava (if Rate Limits allow) and re-runs processing.
    Useful for detailed breakdown of 'Complex' runs.
    """
    metrics = await analysis.analyze_with_streams(db, str(activity.id))
    if not metrics:
        raise HTTPException(status_code=400, detail="Processing failed or activity not found.")

    read = DerivedMetricRead.model_validate(metrics)
    read.headline = compose_headline(metrics.activity, Classification.from_metrics(metrics))
    return read

@router.post("/activities/backfill-streams")
def backfill_streams(
    db: DbSession,
    user: CurrentUser,
):
    """Kick off a paced backfill of stream-derived analysis for historical
    summary-only activities (#110).

    Enqueues a self-pacing job that fetches streams and re-runs analysis a small
    batch at a time, staying within Strava's rate limits and never notifying.
    Scoped to the authenticated runner's own history (#470). Returns how many of
    their activities are currently eligible.
    """
    from app.jobs.backfill_streams import enqueue_backfill

    eligible = enqueue_backfill(db, user.id)
    return {
        "eligible": eligible,
        "status": "scheduled" if eligible else "nothing_to_do",
    }

@router.post("/activities/reanalyze")
def reanalyze_history(
    db: DbSession,
    user: CurrentUser,
):
    """Kick off a paced bulk re-analysis of already-analyzed history (#300).

    After a change to how analysis derives a stored signal (e.g. #297's Strava
    zone binning), only newly-synced activities pick it up. This enqueues a
    self-pacing job that re-runs analysis from the streams already stored in the
    database — no Strava calls — a batch at a time, so the change propagates to
    historical DerivedMetric rows. It is resumable and never notifies. Scoped to
    the authenticated runner's own history (#470). Returns how many of their
    activities are currently eligible.
    """
    from app.jobs.reanalyze_history import enqueue_reanalyze

    eligible = enqueue_reanalyze(db, user.id)
    return {
        "eligible": eligible,
        "status": "scheduled" if eligible else "nothing_to_do",
    }


@router.put("/activities/{activity_id}/intent", response_model=ActivityRead)
def update_activity_intent(
    payload: ActivityIntentUpdate,
    activity: OwnedActivity,
    db: DbSession,
):
    """
    Updates the manual user intent for an activity and re-runs analysis.
    """
    return write_activity_intent(db, activity, user_intent=payload.user_intent)

# Default sync window (days). Sync imports summaries for this window in-request
# and defers all stream fetching to the gated background backfill (#596), so the
# window size no longer changes whether streams are fetched eagerly — it only
# bounds how far back the summary import reaches.
_DEFAULT_SYNC_WINDOW_DAYS = 30


@router.post("/sync", response_model=SyncResponse)
async def sync_activities(
    db: DbSession,
    account: LinkedStravaAccount,
    since_days: Annotated[
        int,
        Query(
            ge=1,
            description=(
                "How many days back to sync activity summaries. Stream-derived "
                "analysis is fetched afterwards by the background backfill."
            ),
        ),
    ] = _DEFAULT_SYNC_WINDOW_DAYS,
):
    """
    Triggers a manual sync of the last `since_days` days of activities.

    Imports activity summaries in-request (one paginated Strava call) and runs
    summary-level analysis immediately, then hands stream fetching + stream-derived
    analysis to the gated background backfill chain (#596). Streams are NOT fetched
    in-request: one Strava streams call per activity, ungated by the shared rate
    budget, meant a first sync could fire dozens of calls in-request — blowing
    Strava's 100/15min ceiling under concurrent onboarding and blocking the request
    to the gateway timeout. Metrics/coach then populate asynchronously, exactly as
    the webhook path already does.
    """
    since = datetime.now() - timedelta(days=since_days)
    activities, stats = await ingest_recent_activities(
        db, account, get_strava_port(), since=since, fetch_streams=False
    )

    for activity in activities:
        try:
            analysis.analyze(db, str(activity.id))
            stats.analyzed += 1
        except Exception as exc:
            stats.errors.append(
                f"Analysis failed for activity {activity.strava_activity_id}: {exc}"
            )

    # Populate stream-derived analysis in the background, gated by the Strava
    # budget and self-paced (#601), so concurrent first-syncs cannot overshoot the
    # shared ceiling. Idempotent + scoped to this user; a no-op when nothing lacks
    # streams (e.g. a routine re-sync of already-deep-processed activities).
    from app.jobs.backfill_streams import enqueue_backfill

    enqueue_backfill(db, account.user_id)

    return stats


@router.post("/activities/refresh")
def refresh_activities(
    account: OptionalStravaAccount,
):
    """Self-healing check for activities a Strava webhook missed (#123, ADR 0006).

    Triggered on app-open and by the user-facing Refresh affordance; replaces the
    removed time-based polling fallback. Enqueues a bounded per-user check (one
    Strava call) on the worker and returns immediately, so it never adds Strava
    latency to the page. The bound (one check per user per
    SELF_HEAL_MIN_INTERVAL_SECONDS) means rapid taps do not multiply Strava calls,
    and the existing per-activity dedup still guarantees once-only processing.
    """
    from app.jobs.self_heal import maybe_enqueue_self_heal

    if account is None:
        return {"status": "no_account"}
    triggered = maybe_enqueue_self_heal(account.user_id)
    return {"status": "checking" if triggered else "throttled"}


@router.get("/activities", response_model=List[ActivityRead])
def read_activities(
    db: DbSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = 20,
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    start_date: Optional[date] = Query(None, description="Only activities on/after this date (UTC, inclusive)"),
    end_date: Optional[date] = Query(None, description="Only activities on/before this date (UTC, inclusive)"),
):
    """
    Get stored activities (paginated), optionally filtered by type and date range (#404).

    Filters apply server-side before pagination, so they narrow the whole history
    rather than only the rows already loaded by the client.
    """
    activities = activity_queries.get_activities(
        db, skip=skip, limit=limit, user_id=user.id,
        types=types, start_date=start_date, end_date=end_date,
    )
    # #797: one batched lookup for the whole page, not one per row.
    coach_leads = coach_service.get_displayable_report_leads(db, activities)
    responses = []
    for activity in activities:
        response = ActivityRead.model_validate(activity)
        if activity.metrics:
            response.headline = compose_headline(
                activity, Classification.from_metrics(activity.metrics)
            )
        response.coach_lead = coach_leads.get(activity.id)
        responses.append(response)
    # #684: the models above are already validated once (model_validate). Returning
    # them directly would let FastAPI re-validate against `response_model`, re-running
    # `ActivityRead.normalize_run_cadence` — a NON-idempotent single-leg->two-leg
    # doubling — a second time, so avg_cadence would be doubled twice. Serialize the
    # already-normalized instances directly to keep normalization at exactly one pass.
    return JSONResponse(content=jsonable_encoder(responses))

@router.get("/activities/{activity_id}", response_model=ActivityDetailRead)
def read_activity(
    activity: OwnedActivityDetail,
    db: DbSession,
):
    # Calculate Splits
    effective_type = activity.user_intent if activity.user_intent else activity.type
    splits_data = calculate_splits(
        activity.streams or [], activity_type=effective_type
    )

    # Convert to Pydantic model manually to inject transient splits + headline
    response = ActivityDetailRead.model_validate(activity)
    response.splits = splits_data
    response.laps = project_laps(activity.raw_summary, effective_type)
    if response.metrics:
        response.metrics.headline = compose_headline(
            activity, Classification.from_metrics(activity.metrics)
        )
        # Inject the zone-binning source so the UI can caption time-in-zones
        # accurately (#301). Queried from UserProfile; not stored on DerivedMetric.
        profile = db.query(UserProfile).filter(
            UserProfile.user_id == activity.user_id
        ).first()
        if profile is not None:
            response.metrics.hr_zones_source = profile.hr_zones_source

    # P3 readiness (ADR 0016): the current-condition read as of this activity,
    # computed read-time from the user's windowed history. Shown regardless of the
    # active coach prompt; None (card hidden) when there is no history.
    # #507: anchor on the runner's LOCAL day so readiness and the volume signal agree
    # on the calendar-day boundary (the daily-load series is bucketed by local day).
    readiness = build_readiness(db, activity.user_id, activity.local_start)
    if readiness is not None:
        response.training_load = TrainingLoadRead(
            fitness=readiness.fitness,
            fatigue=readiness.fatigue,
            form=readiness.form,
            ramp_rate=readiness.ramp_rate,
            condition=readiness.condition,
            trend=readiness.trend,
            ramp_aggressive=readiness.ramp_aggressive,
            warming_up=readiness.warming_up,
            sample_count=readiness.sample_count,
        )

    # #684: `response` is already validated once (model_validate above). Returning it
    # directly would let FastAPI re-validate it against `response_model`, re-running
    # the cadence normalizers (`normalize_run_cadence`, `normalize_stream_cadence`) —
    # each a NON-idempotent `< 130 spm -> double` rule — a SECOND time, doubling
    # avg_cadence and the raw cadence stream twice while the guarded smoothed_cadence
    # recompute stays at one doubling (the 254-vs-127 split). Serialize the
    # already-normalized instance directly so normalization runs at exactly one pass.
    return JSONResponse(content=jsonable_encoder(response))

@router.post("/activities/{activity_id}/checkin", response_model=CheckInRead)
def create_checkin(
    checkin_data: CheckInCreate,
    activity: OwnedActivity,
    db: DbSession,
):
    # Shared with the Telegram inbound callback path (I1b) so both writes are
    # identical: upsert + re-analyze + conditional A4 fuller-turn trigger.
    return write_checkin(db, activity.id, checkin_data)
