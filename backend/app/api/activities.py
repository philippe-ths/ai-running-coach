from datetime import date, datetime, timedelta
from typing import Annotated, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.clerk_auth import require_current_user
from app.db.session import get_db
from app.models import Activity, StravaAccount, User
from app.models.user_profile import UserProfile
from app.schemas import ActivityRead, ActivityDetailRead, CheckInCreate, CheckInRead, SyncResponse, ActivityIntentUpdate, DerivedMetricRead, TrainingLoadRead
from app.services import activity_queries, analysis
from app.services.checkins import write_checkin
from app.services.analysis.classifier import Classification, compose_headline
from app.services.analysis.splits import calculate_splits
from app.services.laps import project_laps
from app.services.readiness import build_readiness
from app.services.strava_ingestion import get_strava_port, ingest_recent_activities

router = APIRouter()


def _require_owned_activity(db: Session, activity_id: UUID, user: User) -> Activity:
    """Load an activity, 404ing if it does not exist OR is not the user's (P2.1).

    A cross-tenant id must be indistinguishable from a missing one, so the same
    404 covers both — no information about other users' activities leaks.
    """
    activity = activity_queries.get_owned_activity(db, activity_id, user.id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@router.post("/activities/{activity_id}/process_deep", response_model=DerivedMetricRead)
async def process_activity_deep(
    activity_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """
    Fetches full streams from Strava (if Rate Limits allow) and re-runs processing.
    Useful for detailed breakdown of 'Complex' runs.
    """
    _require_owned_activity(db, activity_id, user)
    metrics = await analysis.analyze_with_streams(db, str(activity_id))
    if not metrics:
        raise HTTPException(status_code=400, detail="Processing failed or activity not found.")

    read = DerivedMetricRead.model_validate(metrics)
    read.headline = compose_headline(metrics.activity, Classification.from_metrics(metrics))
    return read

@router.post("/activities/backfill-streams")
def backfill_streams(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
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
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
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
    activity_id: UUID,
    payload: ActivityIntentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """
    Updates the manual user intent for an activity and re-runs analysis.
    """
    activity = _require_owned_activity(db, activity_id, user)

    activity.user_intent = payload.user_intent
    db.add(activity)
    db.commit()
    db.refresh(activity)
    
    # Re-run processing pipeline with new intent
    analysis.analyze(db, str(activity_id))
    
    return activity

# Sync windows up to this many days fetch streams eagerly (full deep
# processing). Beyond it, sync is treated as a historical backfill and imports
# summaries only, because one stream call per activity over a long window would
# exceed Strava's 100-requests/15-min limit. See #109.
_STREAM_FETCH_WINDOW_DAYS = 30


@router.post("/sync", response_model=SyncResponse)
async def sync_activities(
    since_days: Annotated[
        int,
        Query(
            ge=1,
            description=(
                "How many days back to sync. Windows up to "
                f"{_STREAM_FETCH_WINDOW_DAYS} days fetch full streams; larger "
                "windows import activity summaries only (historical backfill)."
            ),
        ),
    ] = _STREAM_FETCH_WINDOW_DAYS,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """
    Triggers a manual sync of the last `since_days` days of activities.

    The default window fetches streams and is the routine sync. A larger window
    backfills summaries only (streams cost one Strava call each and would breach
    the rate limit over a long window); analysis still runs from the summary.
    """
    # P2.1: sync only the authenticated user's own Strava account.
    account = db.execute(
        select(StravaAccount).where(StravaAccount.user_id == user.id)
    ).scalars().first()

    if not account:
        raise HTTPException(status_code=404, detail="No linked Strava account found. Connect Strava first.")

    since = datetime.now() - timedelta(days=since_days)
    fetch_streams = since_days <= _STREAM_FETCH_WINDOW_DAYS
    activities, stats = await ingest_recent_activities(
        db, account, get_strava_port(), since=since, fetch_streams=fetch_streams
    )

    for activity in activities:
        try:
            analysis.analyze(db, str(activity.id))
            stats.analyzed += 1
        except Exception as exc:
            stats.errors.append(
                f"Analysis failed for activity {activity.strava_activity_id}: {exc}"
            )

    return stats


@router.post("/activities/refresh")
def refresh_activities(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
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

    # P2.1: self-heal only the authenticated user's own Strava account (#502).
    account = db.execute(
        select(StravaAccount).where(StravaAccount.user_id == user.id)
    ).scalars().first()
    if account is None:
        return {"status": "no_account"}
    triggered = maybe_enqueue_self_heal(account.user_id)
    return {"status": "checking" if triggered else "throttled"}


@router.get("/activities", response_model=List[ActivityRead])
def read_activities(
    skip: int = 0,
    limit: int = 20,
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    start_date: Optional[date] = Query(None, description="Only activities on/after this date (UTC, inclusive)"),
    end_date: Optional[date] = Query(None, description="Only activities on/before this date (UTC, inclusive)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
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
    responses = []
    for activity in activities:
        response = ActivityRead.model_validate(activity)
        if activity.metrics:
            response.headline = compose_headline(
                activity, Classification.from_metrics(activity.metrics)
            )
        responses.append(response)
    return responses

@router.get("/activities/{activity_id}", response_model=ActivityDetailRead)
def read_activity(
    activity_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    activity = activity_queries.get_activity(db, str(activity_id), user_id=user.id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
        
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

    return response

@router.post("/activities/{activity_id}/checkin", response_model=CheckInRead)
def create_checkin(
    activity_id: UUID,
    checkin_data: CheckInCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    # P2.1: verify the activity is the user's before writing (404 otherwise).
    _require_owned_activity(db, activity_id, user)
    # Shared with the Telegram inbound callback path (I1b) so both writes are
    # identical: upsert + re-analyze + conditional A4 fuller-turn trigger.
    return write_checkin(db, activity_id, checkin_data)
