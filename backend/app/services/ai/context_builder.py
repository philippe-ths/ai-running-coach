"""Service to assemble a unified ContextPack from DB models."""

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Activity, DerivedMetric, CheckIn, UserProfile, Advice
from app.services.ai.context_pack import (
    ContextPack, CPActivity, CPAthlete, CPDerivedMetric, 
    CPFlag, CPCheckIn, CPLast7Days
)
from app.services.ai.signals import infer_signals


def build_context_pack(activity_id: UUID, db: Session) -> Optional[ContextPack]:
    """Assemble all context required for AI analysis of a specific activity.
    
    Args:
        activity_id: The UUID of the activity to package.
        db: Active database session.
        
    Returns:
        Fully populated ContextPack or None if activity not found.
    """
    # 1. Fetch main activity with relationships
    # Note: We rely on lazy loading or assumed joined loading for relationships 
    # defined in models.py (metrics, advice, check_in, profile via user)
    stmt = select(Activity).where(Activity.id == activity_id)
    activity = db.execute(stmt).scalar_one_or_none()
    
    if not activity:
        return None

    # 2. Build Activity Component
    # Prefer user_intent if set (manual override), otherwise default Strava type
    effective_type = activity.user_intent if activity.user_intent else activity.type

    cp_activity = CPActivity(
        id=str(activity.id),
        start_time=activity.start_date.isoformat(),
        type=effective_type,
        name=activity.name,
        distance_m=float(activity.distance_m),
        moving_time_s=activity.moving_time_s,
        elapsed_time_s=activity.elapsed_time_s,
        elevation_gain_m=activity.elev_gain_m,
        avg_hr=activity.avg_hr,
        max_hr=activity.max_hr,
        avg_cadence=activity.avg_cadence,
        # Calculated fields if not present
        avg_pace_s_per_km=(
            (activity.moving_time_s / (activity.distance_m / 1000.0))
            if activity.distance_m > 0 else None
        )
    )

    # 3. Build Athlete Component
    cp_athlete = CPAthlete()
    if activity.user and activity.user.profile:
        p = activity.user.profile
        cp_athlete = CPAthlete(
            goal=p.goal_type,
            experience_level=p.experience_level,
            injury_notes=p.injury_notes,
            # age/sex not currently in UserProfile model, defaulting to None
        )

    # 4. Build Metrics & Flags
    cp_metrics = []
    cp_flags = []
    
    if activity.metrics:
        m = activity.metrics
        
        # Effort Score
        cp_metrics.append(CPDerivedMetric(
            key="effort_score",
            value=m.effort_score,
            unit="TRIMP",
            confidence=0.8, # Placeholder or could drive from m.confidence enum (high/med/low)
            evidence=f"Calculated from HR/Pace data (Confidence: {m.confidence})"
        ))
        
        # Pace Variability
        if m.pace_variability is not None:
             cp_metrics.append(CPDerivedMetric(
                key="pace_variability",
                value=m.pace_variability,
                unit="unitless",
                confidence=1.0,
                evidence="Standard deviation of split paces"
            ))
            
        # HR Drift
        if m.hr_drift is not None:
             cp_metrics.append(CPDerivedMetric(
                key="hr_drift",
                value=m.hr_drift,
                unit="%",
                confidence=0.8,
                evidence="HR decoupling over duration"
            ))

        # Flags (stored as list of strings in DB, mapping manually here)
        # Assuming DB stores simple strings like "high_hr_drift".
        # We simulate structured flags since DB model is simplifed (mostly list[str]).
        if m.flags:
            for f in m.flags:
                # Basic mapping for MVP - in real app, might parse or use structured storage
                cp_flags.append(CPFlag(
                    code=f,
                    severity="warn" if "high" in f or "risk" in f else "info",
                    message=f"Flagged: {f}",
                    evidence="Derived analysis"
                ))

    # 5. Build Check-In
    cp_check_in = CPCheckIn()
    if activity.check_in:
        c = activity.check_in
        cp_check_in = CPCheckIn(
            rpe_0_10=c.rpe,
            pain_0_10=c.pain_score,
            sleep_0_10=c.sleep_quality,
            notes=c.notes
        )

    # 6. Signal Availability
    # We pass the activity object directly since our infer_signals uses getattr/dict access
    # We would need streams loaded to be perfect, but summary is good for MVP
    available, missing = infer_signals(activity, streams=activity.streams)

    # 7. Last 7 Days (Placeholder per requirements)
    cp_last_7 = CPLast7Days(
        intensity_summary="Weekly aggregation not yet implemented",
        load_trend="Stable"
    )
    
    # 8. Assemble ContextPack
    return ContextPack(
        activity=cp_activity,
        athlete=cp_athlete,
        derived_metrics=cp_metrics,
        flags=cp_flags,
        last_7_days=cp_last_7,
        check_in=cp_check_in,
        available_signals=available,
        missing_signals=missing
    )
