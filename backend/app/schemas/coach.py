from datetime import datetime
from typing import List, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CoachNextStep(BaseModel):
    action: str
    details: str
    why: str


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


class CoachReportContent(BaseModel):
    key_takeaways: List[str] = Field(..., min_length=2, max_length=4)
    next_steps: List[CoachNextStep] = Field(..., min_length=1, max_length=3)
    risks: List[CoachRisk] = Field(default_factory=list)
    questions: List[CoachQuestion] = Field(default_factory=list, max_length=4)


class CoachReportRead(BaseModel):
    id: UUID
    activity_id: UUID
    report: CoachReportContent
    meta: CoachReportMeta
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
