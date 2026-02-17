from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoachTakeaway(BaseModel):
    text: str
    evidence: Optional[str] = None  # e.g. "hr_drift=7.2%, effort_score=4.2"


class CoachNextStep(BaseModel):
    action: str
    details: str
    why: str
    evidence: Optional[str] = None


class CoachRisk(BaseModel):
    flag: str
    explanation: str
    mitigation: str


class CoachQuestion(BaseModel):
    question: str
    reason: str


class CoachReportMeta(BaseModel):
    confidence: Literal["low", "medium", "high"]
    model_id: str
    prompt_id: str
    schema_version: str
    input_hash: str
    generated_at: datetime
    policy_violations: List[str] = Field(default_factory=list)


class CoachReportContent(BaseModel):
    key_takeaways: List[CoachTakeaway] = Field(..., min_length=2, max_length=4)
    next_steps: List[CoachNextStep] = Field(..., min_length=1, max_length=3)
    risks: List[CoachRisk] = Field(default_factory=list)
    questions: List[CoachQuestion] = Field(default_factory=list, max_length=4)

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_string_takeaways(cls, data: Any) -> Any:
        """Backward compat: convert bare strings in key_takeaways to structured format."""
        if isinstance(data, dict) and "key_takeaways" in data:
            data["key_takeaways"] = [
                {"text": item} if isinstance(item, str) else item
                for item in data["key_takeaways"]
            ]
        return data


class CoachReportDebug(BaseModel):
    context_pack: Dict[str, Any]
    system_prompt: str
    raw_llm_response: Optional[str] = None


class CoachReportRead(BaseModel):
    id: UUID
    activity_id: UUID
    report: CoachReportContent
    meta: CoachReportMeta
    debug: CoachReportDebug
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
