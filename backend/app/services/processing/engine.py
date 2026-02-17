from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Activity, DerivedMetric, CheckIn, ActivityStream, StravaAccount, UserProfile
from app.services.processing.metrics import compute_derived_metrics_data
from app.services.processing.classifier import classify_activity
from app.services.processing.flags import generate_flags
from app.services.processing.intervals import detect_intervals
from app.services.processing.risk import compute_risk_score
from app.services.activity_service import fetch_and_store_streams

# Classes that warrant detailed stream processing
DEEP_PROCESSING_CLASSES = ["Tempo", "Intervals", "Long Run", "Race", "Hills"]

async def process_deep(db: Session, activity_id: str) -> Optional[DerivedMetric]:
    """
    Explicitly fetches streams and re-runs processing.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity: return None

    # Fetch streams
    account = db.query(StravaAccount).filter(StravaAccount.user_id == activity.user_id).first()
    if account:
        await fetch_and_store_streams(db, account, activity)

    # Run normal processing (which now picks up streams)
    return process_activity(db, activity_id)


def compute_confidence(activity, streams_dict, check_in):
    """
    Determine confidence level and reasons based on available data.
    Returns (level, reasons) tuple.
    """
    reasons = []

    if not activity.avg_hr:
        reasons.append("no_heart_rate_data")
    if not streams_dict:
        reasons.append("no_stream_data")
    elif not streams_dict.get("latlng"):
        reasons.append("no_gps_data")
    if not check_in:
        reasons.append("no_user_checkin")

    if "no_heart_rate_data" in reasons and "no_stream_data" in reasons:
        level = "low"
    elif "no_heart_rate_data" in reasons or "no_stream_data" in reasons:
        level = "medium"
    else:
        level = "high"

    return level, reasons


def process_activity(db: Session, activity_id: str) -> Optional[DerivedMetric]:
    """
    Main entry point.
    Loads activity, history, computes all metrics, saves DerivedMetric.
    """
    # 1. Load Activity
    stmt = select(Activity).where(Activity.id == activity_id)
    activity = db.execute(stmt).scalars().first()
    if not activity:
        return None

    # 2. Load History (last 20 activities before this one)
    history = db.query(Activity).filter(
        Activity.user_id == activity.user_id,
        Activity.start_date < activity.start_date
    ).order_by(Activity.start_date.desc()).limit(20).all()

    # 2.5: Load Streams if available
    streams = db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).all()
    streams_dict = {s.stream_type: s.data for s in streams}

    # 3. Load CheckIn (if exists)
    check_in = db.query(CheckIn).filter(CheckIn.activity_id == activity.id).first()

    # 4. Fetch Profile for Max HR
    profile = db.query(UserProfile).filter(UserProfile.user_id == activity.user_id).first()
    max_hr = 190
    if profile and profile.max_hr and profile.max_hr > 100:
        max_hr = profile.max_hr

    # 5. Compute metrics
    metrics_data = compute_derived_metrics_data(activity, streams_dict, max_hr=max_hr)

    # 6. Classify
    classification = classify_activity(activity, history)
    metrics_data["activity_class"] = classification

    # 6.5 Interval segmentation
    metrics_data["interval_structure"] = detect_intervals(streams_dict, classification) if streams_dict else None

    # 7. Load history metrics for load spike detection
    history_metrics = (
        db.query(DerivedMetric)
        .filter(DerivedMetric.activity_id.in_([h.id for h in history]))
        .all()
        if history else []
    )

    # 8. Flags (all flag logic consolidated in flags.py)
    all_flags = generate_flags(
        activity, metrics_data, history, check_in,
        history_metrics=history_metrics,
    )
    metrics_data["flags"] = all_flags

    # 8.5 Risk score (deterministic, based on flags + check-in + training context)
    check_in_data = {
        "sleep_quality": check_in.sleep_quality if check_in else None,
        "rpe": check_in.rpe if check_in else None,
    }
    # Compute training context for risk scoring
    from app.services.coach.context import _build_training_context
    training_ctx = _build_training_context(db, activity)
    risk_result = compute_risk_score(all_flags, check_in_data, training_ctx)
    metrics_data["risk_level"] = risk_result["risk_level"]
    metrics_data["risk_score"] = risk_result["risk_score"]
    metrics_data["risk_reasons"] = risk_result["risk_reasons"]

    # 9. Confidence
    confidence, confidence_reasons = compute_confidence(activity, streams_dict, check_in)
    metrics_data["confidence"] = confidence
    metrics_data["confidence_reasons"] = confidence_reasons

    # 10. Upsert DerivedMetric
    existing_dm = db.query(DerivedMetric).filter(DerivedMetric.activity_id == activity.id).first()

    if existing_dm:
        for k, v in metrics_data.items():
            setattr(existing_dm, k, v)
        dm = existing_dm
    else:
        dm = DerivedMetric(activity_id=activity.id, **metrics_data)
        db.add(dm)

    db.commit()
    db.refresh(dm)
    return dm
