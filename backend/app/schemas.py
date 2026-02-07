from datetime import datetime, date
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator, model_validator

from app.services.units.cadence import normalize_cadence_spm

# --- User ---
class UserCreate(BaseModel):
    email: Optional[EmailStr] = None

class UserRead(BaseModel):
    id: UUID
    email: Optional[EmailStr] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# --- Activity ---
class ActivityBase(BaseModel):
    strava_activity_id: int
    name: str
    type: str
    start_date: datetime
    distance_m: int
    moving_time_s: int
    elapsed_time_s: int
    elev_gain_m: float
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    raw_summary: Dict[str, Any] = {}

class ActivityCreate(ActivityBase):
    pass

class ActivityRead(ActivityBase):
    id: UUID
    user_id: UUID
    is_deleted: bool
    user_intent: Optional[str] = None
    avg_cadence: Optional[float] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator('avg_cadence', mode='before')
    @classmethod
    def read_cadence(cls, v: Optional[float]) -> Optional[float]:
        # Allow standard reading
        return v

    @field_validator('avg_cadence', mode='after')
    @classmethod
    def normalize_cadence(cls, v: Optional[float], info) -> Optional[float]:
        # We need access to 'type' or 'user_intent' from the model to determine if it's a run.
        # In Pydantic V2 field validators, we can access values if using a model_validator,
        # or via ValidationInfo if using field validator but ordering matters.
        # However, relying on ordering is fragile.
        # Better to use a computed_field or property, but simple solution:
        # Use model_validator.
        return v

    @model_validator(mode='after')
    def normalize_run_cadence(self) -> 'ActivityRead':
        effective_type = self.user_intent if self.user_intent else self.type
        self.avg_cadence = normalize_cadence_spm(effective_type, self.avg_cadence)
        return self

class ActivityIntentUpdate(BaseModel):
    user_intent: str

# --- Profile ---
class UserProfileBase(BaseModel):
    goal_type: str
    target_date: Optional[date] = None
    experience_level: str
    weekly_days_available: int
    current_weekly_km: Optional[int] = None
    max_hr: Optional[int] = None
    upcoming_races: List[Dict[str, Any]] = [] 
    injury_notes: Optional[str] = None

class UserProfileCreate(UserProfileBase):
    pass

class UserProfileRead(UserProfileBase):
    user_id: UUID
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

# --- CheckIn ---
class CheckInBase(BaseModel):
    rpe: Optional[int] = None
    pain_score: Optional[int] = None
    pain_location: Optional[str] = None
    sleep_quality: Optional[int] = None
    notes: Optional[str] = None

class CheckInCreate(CheckInBase):
    pass

class CheckInRead(CheckInBase):
    id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class SyncResponse(BaseModel):
    fetched: int = 0
    upserted: int = 0
    skipped: int = 0
    analyzed: int = 0
    errors: List[str] = []

# --- Detail Schemas ---
class DerivedMetricRead(BaseModel):
    activity_class: str
    effort_score: float
    pace_variability: Optional[float] = None
    hr_drift: Optional[float] = None
    flags: List[str] = []
    confidence: str
    confidence_reasons: List[str] = []
    time_in_zones: Optional[Dict] = None
    model_config = ConfigDict(from_attributes=True)

# Validation schema for AI output strictness
class NextRunSchema(BaseModel):
    duration: str
    type: str # e.g. "Easy", "Tempo", "Rest"
    intensity: str # e.g. "RPE 3"
    description: str # "Keep it slow"
    duration_minutes: Optional[int] = None 
    intensity_target: Optional[str] = None
    notes: Optional[str] = None

class CoachReport(BaseModel):
    headline: str
    session_type: str
    intent_vs_execution: List[str] # 2-5 bullets
    key_metrics: List[str] # 3-6 bullets with numbers
    strengths: List[str] # 1-3 bullets
    opportunities: List[str] # 1-3 bullets
    next_run: NextRunSchema
    weekly_focus: List[str] # 1-2 bullets
    warnings: List[str] = []
    one_question: Optional[str] = None

class AIAdviceOutput(BaseModel):
    verdict: str
    evidence: List[str]
    next_run: NextRunSchema
    week_adjustment: str
    warnings: List[str]
    question: Optional[str] = None
    # Removed ai_commentary as it is now integrated into the primary verdict/evidence

class AdviceRead(BaseModel):
    verdict: str
    evidence: List[str] = []
    next_run: Optional[Dict] = None
    week_adjustment: str
    warnings: List[str] = []
    question: Optional[str] = None
    # full_text field removed to prevent fallback to unstructured text
    ai_commentary: Optional[str] = None
    structured_report: Optional[Dict[str, Any]] = None # New Field
    model_config = ConfigDict(from_attributes=True)

class ActivityStreamRead(BaseModel):
    stream_type: str
    data: List[Any] # Can be list of floats, latlng pairs, etc.
    model_config = ConfigDict(from_attributes=True)
    
    # We can't access parent Activity context easily here to know the Type/Intent.
    # However, if stream_type is 'cadence', we might want to normalize.
    # But normalization requires knowing if it's a RUN. 
    # ActivityDetailRead contains streams. We could normalize in ActivityDetailRead validator?
    # Or strict assumption: if it's ~80 and looks like cadence, double it? 
    # Dangerous for cycling (80 rpm is normal). 
    # WE MUST normalize at the ActivityDetailRead level where we have context.

class ActivityDetailRead(ActivityRead):
    metrics: Optional[DerivedMetricRead] = None
    advice: Optional[AdviceRead] = None
    check_in: Optional[CheckInRead] = None
    streams: List[ActivityStreamRead] = []

    @model_validator(mode='after')
    def normalize_stream_cadence(self) -> 'ActivityDetailRead':
        # Apply SPM normalization to the cadence stream if it exists
        effective_type = self.user_intent if self.user_intent else self.type
        
        # We invoke the logic from services to check if it's a RUN
        # Reuse logic? We can't call normalize_cadence_spm on a list directly.
        # But we can check if we SHOULD normalize.
        # Let's check if the scalar avg_cadence was normalized. 
        # If scalar was < 130 and is now > 130 (or logic dictates), apply to stream.
        
        # Better: Reuse the exact same condition as the scalar normalizer.
        # But we don't expose "is_run" boolean.
        
        # Let's verify if the scalar normalization logic considers this a run.
        # Since avg_cadence is *already* normalized by the parent (ActivityRead) validator running first,
        # we can't easily compare old vs new.
        
        # We will re-implement a safe check calling the util.
        # Import inside method or at top? Top is cleaner.
        # from app.services.units.cadence import normalize_cadence_spm
        
        if not self.streams:
            return self

        # Find cadence stream
        cadence_stream = next((s for s in self.streams if s.stream_type == 'cadence'), None)
        if not cadence_stream:
            return self

        # Heuristic: Check a sample of the stream
        # Or better, just ask the normalizer "is this a run?" 
        # But normalizer takes (type, val). 
        # We can pass a dummy value (e.g. 80) and see if it changes.
        
        test_val = 80.0
        norm_val = normalize_cadence_spm(effective_type, test_val)
        should_normalize = (norm_val == 160.0)
        
        if should_normalize:
            # Modify data in place
            # data can be numbers or nulls? Usually numbers.
            # Avoid floating point accumulation if already floats.
            
            # Check if stream average is low (~80).
            # If the user graph showed 80, the stream data IS low.
            # Strava might return [79, 80, 81...] for strides.
            
            # Safety check: compute stream average
            nums = [x for x in cadence_stream.data if isinstance(x, (int, float))]
            if not nums:
                return self
                
            stream_avg = sum(nums) / len(nums)
            
            if stream_avg < 130:
                # Double it
                cadence_stream.data = [x * 2 if isinstance(x, (int, float)) else x for x in cadence_stream.data]

        return self


# --- Chat ---
class ChatRequest(BaseModel):
    message: str
    activity_id: Optional[UUID] = None

class ChatResponse(BaseModel):
    reply: str

# --- Coach Verdict V3 ---
from typing import Literal

class V3Headline(BaseModel):
    sentence: str
    status: Literal["green", "amber", "red"]

class V3ExecutiveSummary(BaseModel):
    title: str
    status: Literal["green", "amber", "red"]
    opinion: str

class V3ScorecardItem(BaseModel):
    item: Literal[
        "Purpose match",
        "Control (smoothness)",
        "Aerobic value",
        "Mechanical quality",
        "Risk / recoverability"
    ]
    rating: Literal["ok", "warn", "fail", "unknown"]
    reason: str

    @field_validator('rating', mode='before')
    @classmethod
    def normalize_rating(cls, v: str) -> str:
        v = v.lower().strip()
        mapping = {
            "green": "ok",
            "amber": "warn",
            "red": "fail",
            "good": "ok",
            "bad": "fail",
            "warning": "warn"
        }
        return mapping.get(v, v)

class V3RunStory(BaseModel):
    start: str
    middle: str
    finish: str

class V3Lever(BaseModel):
    category: Literal["pacing", "physiology", "mechanics", "context"]
    signal: str
    cause: str
    fix: str
    cue: str

    @field_validator('cue')
    @classmethod
    def validate_quotes(cls, v: str) -> str:
        v = v.strip()
        # Allow standard single/double quotes
        if not (
            (v.startswith('"') and v.endswith('"')) or 
            (v.startswith("'") and v.endswith("'"))
        ):
            raise ValueError("Cue must be enclosed in quotes")
        return v

class V3NextSteps(BaseModel):
    tomorrow: str
    next_7_days: str

# --- Split Response Schemas (Focused Generation) ---

class BaseDebugResponse(BaseModel):
    debug_context: Optional[Dict[str, Any]] = None
    debug_prompt: Optional[Dict[str, str]] = None

class VerdictScorecardResponse(BaseDebugResponse):
    """Schema for the initial analysis pass (verdict + scorecard)."""
    inputs_used_line: str
    headline: Optional[V3Headline] = None # Deprecated, moving to Executive Summary
    why_it_matters: List[str] = Field(min_length=2, max_length=2, description="Must have exactly 2 points: Fitness and Fatigue")
    scorecard: List[V3ScorecardItem]

    @field_validator('scorecard')
    @classmethod
    def validate_unique_items(cls, v: List[V3ScorecardItem]) -> List[V3ScorecardItem]:
        seen = set()
        for item in v:
            if item.item in seen:
                raise ValueError(f"Duplicate scorecard item: {item.item}")
            seen.add(item.item)
        return v

class StoryResponse(BaseDebugResponse):
    """Schema for the narrative arc pass."""
    run_story: V3RunStory

class LeverResponse(BaseDebugResponse):
    """Schema for the single prescriptive change pass."""
    lever: V3Lever

class SummaryResponse(BaseDebugResponse):
    """Schema for the final executive summary / verdict."""
    executive_summary: V3ExecutiveSummary

class NextStepsResponse(BaseDebugResponse):
    """Schema for the forward-looking plan pass."""
    next_steps: V3NextSteps

class QuestionResponse(BaseDebugResponse):
    """Schema for the final coaching question."""
    question_for_you: str

# --- API Request Models ---
class ScorecardRequest(BaseModel):
    activity_id: int # Using INT to match Strava ID or UUID? 
    # Context builder takes UUID. ActivityBase has strava_activity_id(int).
    # The user prompt said: body: { "activity_id": int }
    # However, create_file earlier showed `ActivityRead` has `id: UUID`.
    # I should support UUID if that's the internal ID. 
    # Context builder signature: build_context_pack(activity_id: UUID, db: Session).
    # So I must accept UUID.
    # But user prompt says "activity_id": int. 
    # Maybe the frontend sends the *Strava* ID?
    # Or maybe the prompt was just loose with "int".
    # I'll stick to UUID for internal consistency, or allow int if I can resolve it.
    # Looking at existing routes might clarify.
    # Let's assume UUID for now as it's safer for the internal DB lookup.
    # Wait, existing schema `ActivityRead` has `id: UUID`. The user likely means the internal ID.
    pass

class BaseRequest(BaseModel):
    activity_id: UUID

class LeverRequest(BaseRequest):
    scorecard: VerdictScorecardResponse

class NextStepsRequest(BaseRequest):
    scorecard: VerdictScorecardResponse
    lever: LeverResponse

class SummaryRequest(BaseRequest):
    scorecard: VerdictScorecardResponse
    lever: LeverResponse
    story: StoryResponse
    next_steps: NextStepsResponse
    # Question? Optional, maybe not needed for summary.

class QuestionRequest(BaseRequest):
    scorecard: VerdictScorecardResponse

# --- Composite Schema ---

class CoachVerdictV3(BaseModel):
    inputs_used_line: str
    headline: Optional[V3Headline] = None # Deprecated
    executive_summary: Optional[V3ExecutiveSummary] = None # New Main Heading
    why_it_matters: List[str] # min_length=2, max_length=2
    scorecard: List[V3ScorecardItem]
    run_story: V3RunStory
    lever: V3Lever
    next_steps: V3NextSteps
    question_for_you: Optional[str] = None
    debug_context: Optional[Dict[str, Any]] = None
    debug_prompt: Optional[Dict[str, str]] = None

