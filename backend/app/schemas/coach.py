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


class TappableOption(BaseModel):
    """A typed quick-reply affordance attached to a coach question (the I1
    contract, grafted from the Message-First design). `kind` tells the UI how to
    handle a tap; `payload` carries any kind-specific value (e.g. an RPE number).
    A3 renders the label only; inline-keyboard delivery is I1 scope."""
    id: str
    label: str
    kind: Literal["rpe", "pain", "reply", "dispute", "custom"]
    payload: Optional[Any] = None


class CoachMessageQuestion(BaseModel):
    """A coach question in the prose-message tail. Same question/reason shape as
    the legacy CoachQuestion (rule 1 polices it identically) plus optional typed
    tappable options."""
    question: str
    reason: str
    options: List[TappableOption] = Field(default_factory=list)


class CoachMessageReport(BaseModel):
    """The A3 output shape (schema 2.0): a human prose `message` (the product)
    plus a thin structured tail carrying affordances and memory hooks only. The
    tail's `next_steps` keep the exact action/details/why/evidence shape the
    learning loop already consumes (retrieval.fetch_prior_commitments, M7/M8/M10),
    so the loop survives the cutover by construction.

    `tail_degraded` records that the model produced a real message but no usable
    tail (the inherent skip-the-tail failure mode under tool_choice=auto): the
    message is still the product, and the loop abstains on the empty next_steps."""
    message: str
    headline: Optional[str] = None
    next_steps: List[CoachNextStep] = Field(default_factory=list, max_length=3)
    risks: List[CoachRisk] = Field(default_factory=list)
    questions: List[CoachMessageQuestion] = Field(default_factory=list, max_length=4)
    tail_degraded: bool = False


class CoachReportMeta(BaseModel):
    confidence: Literal["low", "medium", "high"]
    model_id: str
    prompt_id: str
    schema_version: str
    input_hash: str
    generated_at: datetime
    policy_violations: List[str] = Field(default_factory=list)


class CoachReportContent(BaseModel):
    # Grounded reshape (N3): a verdict-first surface layered above the existing
    # list fields. All optional and additive so legacy reports (and the
    # versioned cache from M0) without them still validate.
    headline: Optional[str] = None
    thesis: Optional[str] = None
    lead_argument: Optional[CoachTakeaway] = None
    key_takeaways: List[CoachTakeaway] = Field(..., min_length=1, max_length=6)
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
            # Coerce the single lead_argument (N3) the same way as a takeaway: a
            # bare string or string-evidence must not force a fallback report.
            lead = data.get("lead_argument")
            if isinstance(lead, str):
                lead = {"text": lead}
                data["lead_argument"] = lead
            if isinstance(lead, dict) and isinstance(lead.get("evidence"), str):
                lead["evidence"] = _parse_legacy_evidence(lead["evidence"])
        return data


class CoachReportDebug(BaseModel):
    context_pack: Dict[str, Any]
    system_prompt: str
    raw_llm_response: Optional[str] = None


class CoachReportRead(BaseModel):
    id: UUID
    activity_id: UUID
    # The stored report is one of two shapes keyed by schema-version family:
    # the legacy structured CoachReportContent (1.x) or the A3 prose
    # CoachMessageReport (2.x). service._to_read validates against the right one.
    report: Union[CoachMessageReport, CoachReportContent]
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
