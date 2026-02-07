from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.db.session import get_db
from app.schemas import (
    VerdictScorecardResponse,
    StoryResponse,
    LeverResponse,
    NextStepsResponse,
    QuestionResponse,
    SummaryResponse,
    CoachVerdictV3,
    BaseRequest,
    LeverRequest,
    NextStepsRequest,
    QuestionRequest,
    SummaryRequest
)
from app.services.ai.context_builder import build_context_pack
from app.services.ai.verdict_v3.generators import (
    generate_scorecard, 
    generate_story, 
    generate_lever, 
    generate_next_steps, 
    generate_question,
    generate_executive_summary,
    generate_full_verdict_orchestrator,
    VerdictV3GenerationError
)
from app.services.ai.verdict_v3.safety import enforce_scorecard_safety, enforce_next_steps_safety

router = APIRouter()

def get_context_or_404(activity_id: UUID, db: Session):
    # ContextPack returns a Pydantic model. We need to dump it to dict for generators.
    # build_context_pack returns Optional[ContextPack]
    cp_model = build_context_pack(activity_id, db)
    if not cp_model:
        raise HTTPException(status_code=404, detail="Activity not found or not analyzed")
    return cp_model.model_dump(mode='json')

@router.post("/verdict/v3/generate", response_model=CoachVerdictV3)
def generate_full_verdict(request: BaseRequest, db: Session = Depends(get_db)):
    """
    Orchestrates the full generation pipeline using the unified orchestrator
    which handles all dependencies and debug context assembly.
    """
    try:
        context_pack = get_context_or_404(request.activity_id, db)
        return generate_full_verdict_orchestrator(context_pack)
    except VerdictV3GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/verdict/v3/scorecard", response_model=VerdictScorecardResponse)
def get_scorecard(request: BaseRequest, db: Session = Depends(get_db)):
    """
    Step 1: Generate analysis scorecard.
    """
    print(f"[DEBUG] V3 Scorecard Request received for activity: {request.activity_id}")
    context_pack = get_context_or_404(request.activity_id, db)
    print(f"[DEBUG] Context pack built. Size: {len(str(context_pack)) if context_pack else 'None'}")
    
    try:
        print("[DEBUG] Calling generate_scorecard...")
        response = generate_scorecard(context_pack)
        print("[DEBUG] Scorecard generated. Applying safety...")
        # Apply strict safety gates
        response = enforce_scorecard_safety(response, context_pack)
        print("[DEBUG] Scorecard safety applied. Returning.")
        return response
    except VerdictV3GenerationError as e:
        print(f"[ERROR] V3 Gen Error: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        print(f"[ERROR] Unexpected Error in Scorecard: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verdict/v3/story", response_model=StoryResponse)
def get_story(request: BaseRequest, db: Session = Depends(get_db)):
    """
    Step 2: Generate narrative story.
    """
    context_pack = get_context_or_404(request.activity_id, db)
    
    try:
        response = generate_story(context_pack)
        return response
    except VerdictV3GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/verdict/v3/lever", response_model=LeverResponse)
def get_lever(request: LeverRequest, db: Session = Depends(get_db)):
    """
    Step 3: Generate prescriptive lever (requires scorecard).
    """
    context_pack = get_context_or_404(request.activity_id, db)
    
    try:
        response = generate_lever(context_pack, request.scorecard)
        return response
    except VerdictV3GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/verdict/v3/next-steps", response_model=NextStepsResponse)
def get_next_steps(request: NextStepsRequest, db: Session = Depends(get_db)):
    """
    Step 4: Generate schedule (requires scorecard + lever).
    """
    context_pack = get_context_or_404(request.activity_id, db)
    
    try:
        response = generate_next_steps(context_pack, request.scorecard, request.lever)
        # Apply safety gates (rest days for red status)
        response = enforce_next_steps_safety(response, request.scorecard, context_pack)
        return response
    except VerdictV3GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/verdict/v3/question", response_model=QuestionResponse)
def get_question(request: QuestionRequest, db: Session = Depends(get_db)):
    """
    Step 5: Generate engagement question (requires scorecard).
    """
    context_pack = get_context_or_404(request.activity_id, db)
    
    try:
        response = generate_question(context_pack, request.scorecard)
        return response
    except VerdictV3GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/verdict/v3/summary", response_model=SummaryResponse)
def get_executive_summary(request: SummaryRequest, db: Session = Depends(get_db)):
    """
    Step 6: Generate Executive Summary (requires EVERYTHING).
    """
    context_pack = get_context_or_404(request.activity_id, db)
    
    try:
        response = generate_executive_summary(
            context_pack, 
            request.scorecard, 
            request.lever, 
            request.story, 
            request.next_steps
        )
        return response
    except VerdictV3GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
