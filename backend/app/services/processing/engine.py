from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Activity, DerivedMetric, CheckIn, ActivityStream, StravaAccount, UserProfile
from app.services.processing.metrics import compute_derived_metrics_data
from app.services.processing.classifier import classify_activity
from app.services.processing.flags import generate_flags
from app.services.processing.intervals import detect_intervals
from app.services.processing.risk import compute_risk_score
from app.services.processing.workout_matching import match_planned_to_detected, build_interval_kpis
from app.services.activity_service import fetch_and_store_streams

# Classes that warrant detailed stream processing
DEEP_PROCESSING_CLASSES = ["Tempo", "Intervals", "Long Run", "Race", "Hills"]


def _extract_planned_workout(check_in) -> dict | None:
    """
    Extract structured planned workout from check-in data.
    Returns None if no planned workout is specified.

    Future: this will come from a dedicated planned_workout field.
    For now, returns None (no planned workout capture yet).
    """
    # Planned workout capture is not yet implemented in the UI.
    # When it is, this function will parse the structured input.
    return None

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


def compute_confidence(activity, streams_dict, check_in, interval_structure=None, workout_match=None):
    """
    Determine confidence level and reasons based on available data.
    Includes interval-specific sanity checks when applicable.
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

    # Interval-specific sanity checks
    if workout_match:
        match_reasons = workout_match.get("confidence_reasons", [])
        for r in match_reasons:
            if r not in reasons:
                reasons.append(r)

        match_score = workout_match.get("match_score")
        if match_score is not None and match_score < 0.7:
            reasons.append("interval_structure_mismatch")

    if interval_structure:
        summary = interval_structure.get("summary", {})
        # Check for implausible total work time (> 45 min of hard running)
        total_work = summary.get("total_work_time_s", 0)
        if total_work > 2700:
            reasons.append("work_time_implausibly_high")

        # Check warmup/cooldown detection
        if not interval_structure.get("warmup_duration_s"):
            reasons.append("no_warmup_detected")

    # Determine level — more reasons = lower confidence
    critical = {"no_heart_rate_data", "no_stream_data", "interval_structure_mismatch",
                "work_time_implausibly_high", "high_rep_distance_variability"}
    critical_hits = critical & set(reasons)

    if len(critical_hits) >= 2:
        level = "low"
    elif len(critical_hits) >= 1 or len(reasons) >= 3:
        level = "medium"
    elif len(reasons) == 0:
        level = "high"
    else:
        level = "medium" if reasons else "high"

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
    interval_structure = detect_intervals(streams_dict, classification) if streams_dict else None
    metrics_data["interval_structure"] = interval_structure

    # 6.6 Workout matching — compare planned vs detected
    planned_workout = _extract_planned_workout(check_in)
    workout_match = match_planned_to_detected(interval_structure, planned_workout)
    metrics_data["workout_match"] = workout_match

    # 6.7 Interval-specific KPIs
    if interval_structure:
        zones_calibrated = bool(profile and profile.max_hr and profile.max_hr > 100)
        interval_kpis = build_interval_kpis(
            interval_structure,
            max_hr=max_hr,
            zones_calibrated=zones_calibrated,
            time_in_zones=metrics_data.get("time_in_zones"),
        )
        metrics_data["interval_kpis"] = interval_kpis
    else:
        metrics_data["interval_kpis"] = None

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

    # 9. Confidence (with interval sanity checks)
    confidence, confidence_reasons = compute_confidence(
        activity, streams_dict, check_in,
        interval_structure=interval_structure,
        workout_match=workout_match,
    )
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
