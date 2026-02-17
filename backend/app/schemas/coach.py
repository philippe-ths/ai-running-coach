from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRef(BaseModel):
    """Machine-readable evidence reference — a field path + its value."""
    field: str
    value: Any


class CoachTakeaway(BaseModel):
    text: str
    evidence: Optional[List[EvidenceRef]] = None


class CoachNextStep(BaseModel):
    action: str
    details: str
    why: str
    evidence: Optional[List[EvidenceRef]] = None


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
    def _coerce_legacy_formats(cls, data: Any) -> Any:
        """Backward compat: convert bare strings and legacy evidence formats."""
        if isinstance(data, dict):
            # Coerce bare string takeaways
            if "key_takeaways" in data:
                data["key_takeaways"] = [
                    {"text": item} if isinstance(item, str) else item
                    for item in data["key_takeaways"]
                ]
            # Coerce string evidence → structured format
            for section in ("key_takeaways", "next_steps"):
                for item in data.get(section, []):
                    if isinstance(item, dict) and isinstance(item.get("evidence"), str):
                        item["evidence"] = _parse_legacy_evidence(item["evidence"])
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


def _parse_legacy_evidence(evidence_str: str) -> list:
    """Convert legacy 'field=value, field=value' string to structured refs."""
    refs = []
    for pair in evidence_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            field, _, value = pair.partition("=")
            refs.append({"field": field.strip(), "value": value.strip()})
    return refs if refs else [{"field": "raw", "value": evidence_str}]
