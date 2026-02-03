from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Activity, DerivedMetric, CheckIn, ActivityStream, StravaAccount, UserProfile
from app.services.analysis.metrics import compute_derived_metrics_data
from app.services.analysis.classifier import classify_activity
from app.services.analysis.flags import generate_flags
from app.services.activity_service import fetch_and_store_streams

# Classes that warrant detailed stream analysis
DEEP_ANALYSIS_CLASSES = ["Tempo", "Intervals", "Long Run", "Race", "Hills"]

async def run_deep_analysis(db: Session, activity_id: str) -> Optional[DerivedMetric]:
    """
    Explicitly fetches streams and re-runs analysis.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity: return None
    
    # Fetch streams
    account = db.query(StravaAccount).filter(StravaAccount.user_id == activity.user_id).first()
    if account:
        await fetch_and_store_streams(db, account, activity)
    
    # Run normal analysis (which now picks up streams)
    return run_analysis(db, activity_id)

def generate_flags_with_drift(metrics_data, check_in):
    flags = []
    # Drift Check
    drift = metrics_data.get("hr_drift")
    if drift and drift > 5.0:
        flags.append("cardiac_drift_high")
    
    # Pace Var Check
    pace_var = metrics_data.get("pace_variability")
    if pace_var and pace_var > 15.0 and metrics_data.get("activity_class") == "Tempo":
        flags.append("pace_unstable")

    # Add back existing user feedback flags
    if check_in:
        if check_in.pain_score and check_in.pain_score >= 4:
            flags.append("pain_reported")
            
    # Merge with base flags if needed or replace entirely
    # For now, simplistic
    return flags

def run_analysis(db: Session, activity_id: str) -> Optional[DerivedMetric]:
    """
    Main entry point. 
    Loads activity, history, computes all metrics, saves DerivedMetric.
    """
    # 1. Load Activity
    stmt = select(Activity).where(Activity.id == activity_id)
    activity = db.execute(stmt).scalars().first()
    if not activity:
        return None

    # 2. Load History (Last 30 days)
    # Simple query for now
    history = db.query(Activity).filter(
        Activity.user_id == activity.user_id,
        Activity.start_date < activity.start_date
    ).order_by(Activity.start_date.desc()).limit(20).all()

    # *NEW* 2.5: Load Streams if available
    streams = db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).all()
    streams_dict = {s.stream_type: s.data for s in streams}

    # 3. Load CheckIn (if exists)
    check_in = db.query(CheckIn).filter(CheckIn.activity_id == activity.id).first()

    # *NEW* Fetch Profile for Max HR
    profile = db.query(UserProfile).filter(UserProfile.user_id == activity.user_id).first()
    max_hr = 190
    if profile and profile.max_hr and profile.max_hr > 100:
        max_hr = profile.max_hr

    # 4. Compute
    metrics_data = compute_derived_metrics_data(activity, streams_dict, max_hr=max_hr)
    
    # 5. Classify
    classification = classify_activity(activity, history)
    metrics_data["activity_class"] = classification

    # 6. Flags
    base_flags = generate_flags(activity, metrics_data, history, check_in)
    drift_flags = generate_flags_with_drift(metrics_data, check_in)
    
    # Dedup
    all_flags = list(set(base_flags + drift_flags))
    metrics_data["flags"] = all_flags
    
    # 7. Confidence
    # MVP logic: High if HR exists, Med otherwise
    metrics_data["confidence"] = "high" if activity.avg_hr else "medium"
    metrics_data["confidence_reasons"] = [] 

    # 8. Upsert DerivedMetric
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
