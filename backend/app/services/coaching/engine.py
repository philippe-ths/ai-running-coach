from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import UUID

from app.models import Activity, DerivedMetric, CheckIn, UserProfile, Advice
from app.services.coaching.advisor import generate_advice_structure, construct_full_text
# Import AI hooks
from app.services.ai.client import ai_client
from app.services.ai.prompts import build_commentary_prompt, build_structured_advice_prompt
from app.services.ai.context_builder import build_context_pack
# from app.services.ai.verdict_v3.generators import generate_full_verdict_orchestrator
from app.core.config import settings
from app.schemas import CoachReport, CoachVerdictV3

def generate_and_save_advice(db: Session, activity_id: str) -> Advice:
    """
    Orchestrates data loading, advice generation, AI enrichment, and persistence.
    Attempts AI generation first. Falls back to Rule-Based if AI fails or is disabled.
    """
    # 1. Load Data
    # Ensure activity_id is UUID for querying
    activity_uuid = UUID(activity_id) if isinstance(activity_id, str) else activity_id
    
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if not activity:
        return None
        
    metrics = db.query(DerivedMetric).filter(DerivedMetric.activity_id == activity_uuid).first()
    if not metrics:
        # If metrics missing, we could trigger analysis here, but assuming it's done.
        return None

    check_in = db.query(CheckIn).filter(CheckIn.activity_id == activity_uuid).first()
    profile = db.query(UserProfile).filter(UserProfile.user_id == activity.user_id).first()
    
    # History (mock/simple for now)
    history = db.query(Activity).filter(
        Activity.user_id == activity.user_id,
        Activity.start_date < activity.start_date
    ).order_by(Activity.start_date.desc()).limit(10).all()

    # 2. Mock AI Generation (Deferred to Frontend)
    # We purposefully do NOT generate V3 advice synchronously here anymore.
    # The frontend orchestrates the split-API calls ("Scorecard", "Story", etc.)
    # to provide better UX (progress steps) and reliability.
    # We clear any existing structured report to signal "Needs Generation".
    
    structured_report_data = None
    advice_data = None
    failure_reason = "Analysis Pending (Client-side generation)"

    # Context Pack is needed to verify we have enough data
    context_pack = build_context_pack(activity_uuid, db)

    if settings.AI_ENABLED and context_pack:
        # Save a placeholder so the UI verifies Advice exists, 
        # but structured_report is None -> Triggers V3Fetcher.
        advice_data = {
            "verdict": "Analyzing...",
            "evidence": [],
            "next_run": {},
            "week_adjustment": "Pending analysis...",
            "warnings": [],
            "question": None,
            "full_text": "**Status**: Ready for V3 Analysis."
        }
    elif not settings.AI_ENABLED:
        failure_reason = "AI_ENABLED is False"
    
    # 3. Handle Failure / Disabled State
    if not advice_data:
        advice_data = {
            "verdict": "Offline",
            "evidence": [],
            "next_run": {},
            "week_adjustment": "None",
            "warnings": [],
            "question": None,
            "full_text": f"Manual Analysis Only. ({failure_reason})"
        }
        structured_report_data = None

    # 4. Upsert Advice 
    # (Rest of function remains same)
    existing_advice = db.query(Advice).filter(Advice.activity_id == activity_uuid).first()
    
    # Prepare params
    upsert_data = advice_data.copy()
    
    # Explicitly set structured_report (either data or None) to ensure DB reflects current state
    upsert_data["structured_report"] = structured_report_data
        
    if existing_advice:
        for k, v in upsert_data.items():
            setattr(existing_advice, k, v)
        result = existing_advice
    else:
        result = Advice(activity_id=activity_uuid, **upsert_data)
        db.add(result)
    
    db.commit()
    db.refresh(result)
    return result

