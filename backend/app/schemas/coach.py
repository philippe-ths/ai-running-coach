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


class CoachReportOffer(BaseModel):
    """#944: the schedule change a report OFFERED, as stored on the report row.

    The report's judgment is the sharpest the coach ever has — it has the run
    that just happened, the week around it and the plan ahead all in view — and
    until now it was the one surface where the runner could do nothing with it
    but go and find the screen themselves. This is the offer half of the
    offer-and-confirm mechanism the thread already had.

    STORED, never executed. It carries the request only — a kind and the ids and
    numbers it names — and no token: a token minted when a background worker
    writes the report would be half an hour dead by the time the runner opens the
    Telegram message. The token is minted when the OWNER READS the report, and
    the write happens only when they confirm it (see services/coach/report_offer).

    The three kinds are the SCHEDULE changes only. `check_in`, `intent`,
    `split_block` and `merge_blocks` are the runner's own account of what they
    did, and a report proposing one of those would be the report telling the
    runner what happened to them. The whitelist is enforced server-side, in
    report_offer.REPORT_OFFER_KINDS, not by asking the model nicely.
    """

    # DELIBERATELY TOLERANT ON READ, and strict at the two gates instead. This is
    # a durable JSON shape: rows written by one build are read by the next, and by
    # the previous one after a rollback. A strict `Literal` plus `extra="forbid"`
    # here made an unrecognised `action_type` — or a field a later build added —
    # raise inside `service._to_read`, which answers the report GET with a 500 and
    # leaves the whole report permanently unreadable. Losing the card is the
    # intended degrade; losing the coaching is not, and this module's entire
    # discipline is that a bad offer costs the offer and nothing else.
    #
    # Nothing is lost by the tolerance, because neither gate trusts this model:
    # `report_offer._reject_reason` refuses an action_type off REPORT_OFFER_KINDS
    # before a row is ever stored, `report_offer.mint_report_offer` refuses it
    # again before a card is ever minted, and `ProposedActionRequest` (which IS
    # extra-forbidding) re-coerces every argument at both points. An unknown extra
    # key is ignored on the way in and dropped by that coercion on the way out.
    action_type: str = Field(max_length=32)
    # A STRING, not a UUID, because this model is dumped straight into the
    # report's JSON column — a UUID object is not JSON-serialisable there, and
    # the pack hands the coach `session_id` as a string in the first place. It is
    # parsed as a UUID where it is used as one: the thread's own
    # `ProposedActionRequest` coerces it at both the store-time gate and the mint,
    # so a value that is not a real id is dropped rather than stored.
    planned_session_id: Optional[str] = Field(default=None, max_length=64)
    target_distance_m: Optional[float] = None
    target_duration_s: Optional[int] = None


class CoachReportOfferCard(BaseModel):
    """#944: the offer as the runner is SHOWN it — minted fresh on every read.

    The same five fields the thread's SSE `proposed_action` frame carries, so the
    report panel renders the identical card the chat sheet does and both confirm
    through the one endpoint. Read-time only: never stored, never present on a
    report read by anyone but its owner.
    """

    action_type: str
    token: str
    description: str
    confirm_label: str
    dismiss_label: str = "Leave it"


class CoachMessageReport(BaseModel):
    """The A3 output shape (schema 2.0): a human prose `message` (the product)
    plus a thin structured tail carrying affordances and memory hooks only. The
    tail's `next_steps` keep the exact action/details/why/evidence shape the
    learning loop already consumes (retrieval.fetch_prior_commitments, M7/M8/M10),
    so the loop survives the cutover by construction.

    `tail_degraded` records that the model produced a real message but no usable
    tail (the inherent skip-the-tail failure mode under tool_choice=auto): the
    message is still the product, and the loop abstains on the empty next_steps.

    A4 two-stage Exchange: the opener (stage one) writes its brief prose into
    `opener_message` and leaves `message` empty; the fuller turn (stage two) fills
    `message` + the tail in place, preserving `opener_message`. An opener-only row
    is therefore `opener_message` set AND `message` empty (the in-band signal the
    eval harness and the frontend read). `schedule_fuller_turn` is the opener LLM's
    judgment of whether the run warrants a fuller turn; the deterministic safety
    override (a red-flag run) forces it true in the job regardless. Both fields
    default so legacy and fuller-only rows validate unchanged."""
    message: str
    headline: Optional[str] = None
    next_steps: List[CoachNextStep] = Field(default_factory=list, max_length=3)
    risks: List[CoachRisk] = Field(default_factory=list)
    questions: List[CoachMessageQuestion] = Field(default_factory=list, max_length=4)
    tail_degraded: bool = False
    opener_message: Optional[str] = None
    schedule_fuller_turn: bool = False
    # #822 voice rewrite: `message` is always the VOICELESS baseline — the text the
    # digest, the eval harness and the learning loop read, so those keep consuming
    # substance rather than style. `voiced_message` is that same report said again
    # in the runner's chosen voice, and is what the runner is shown when present.
    # Null means the runner is on Default, or a rewrite failed and the baseline
    # stands. Keeping both is what makes the voice auditable: any voiced report can
    # be diffed against the text it came from.
    #
    # Voiced text mirrors the baseline's own two-stage split, because a two-stage
    # exchange evolves ONE row: without a separate opener field the fuller turn
    # would overwrite the opener's voiced prose and the opener would then render in
    # the fuller's words.
    voiced_message: Optional[str] = None
    voiced_opener_message: Optional[str] = None
    # #944: the schedule change this report offered, or None. Rides the tail
    # because the tail is already "the affordances the message was shaped to
    # hold" (ADR 0009) and is already forced and strictly schema'd — the report
    # path has no agentic tool loop, and giving it one to carry an offer would
    # change the shape of every report generation. Optional and defaulted, so
    # every report stored before #944 validates unchanged.
    offer: Optional[CoachReportOffer] = None

    @model_validator(mode="before")
    @classmethod
    def _tolerate_an_unreadable_offer(cls, data: Any) -> Any:
        """Never let the offer make a STORED report unreadable (#944).

        `CoachMessageReport` is parsed straight off the `coach_reports.report`
        JSON column by `service._to_read`, which has no tolerance of its own: a
        raise here answers the report GET with a 500 and leaves that report
        permanently unopenable. The offer is a card. The report is the coaching.
        An offer this build cannot make sense of — a kind or a field a later
        build wrote, a value of the wrong type, junk — is dropped to None and the
        report renders exactly as it did before it carried one.

        Only the offer is softened, and only when reading a raw mapping: a real
        `CoachReportOffer` (the generation path, which builds one directly) is
        passed through untouched, so nothing about what may be STORED is relaxed.
        The two gates that decide what may be stored and what may be minted are
        in `services/coach/report_offer.py` and are unaffected.
        """
        if not isinstance(data, dict) or "offer" not in data:
            return data
        raw = data["offer"]
        if raw is None or isinstance(raw, CoachReportOffer):
            return data
        try:
            CoachReportOffer.model_validate(raw)
        except Exception:  # noqa: BLE001 -- an offer is never worth a 500
            data = {**data, "offer": None}
        return data


class CoachReportMeta(BaseModel):
    confidence: Literal["low", "medium", "high"]
    model_id: str
    prompt_id: str
    schema_version: str
    input_hash: str
    generated_at: datetime
    policy_violations: List[str] = Field(default_factory=list)
    # A3: True when a prose message was produced but its structured tail was
    # missing/unusable (degrade-not-withhold). False for the legacy structured
    # family and for a clean message+tail. Defaulted so legacy stored meta validates.
    tail_degraded: bool = False
    # P1.1 voice freshness — a READ-TIME flag, never stored. True when this report is
    # the active-version row under a voice-aware prompt but was generated under a
    # different voice than the runner's current one, so it should regenerate to honour
    # the new voice (the frontend auto-triggers the async regen on a stale read). Set
    # by the read endpoint; defaults False so stored meta (which never carries it) and
    # every non-voice prompt validate unchanged.
    voice_stale: bool = False
    # #646 non-destructive-regen provenance stamp — STORED, set only when this report
    # was produced by a force "Re-run" that superseded a prior report (not on a first
    # generation, where both stay None). `regenerated_at` is when the Re-run ran;
    # `memory_as_of` is the runner-memory profile's grounded-through date at that time.
    # Runner memory is intentionally current + unversioned, so the stamp is how a
    # regenerated old report's hindsight is made honest (the preserved original carries
    # the point-in-time memory). Optional/defaulted so pre-#646 stored meta validates.
    regenerated_at: Optional[datetime] = None
    memory_as_of: Optional[datetime] = None
    # #822 voice provenance. `voice_name` is the character this report speaks in, in
    # the same words the picker uses ("The Cornerman", "The Cornerman (adjusted)",
    # "Custom"); None means Default, which is the absence of a character rather than
    # a character called Default. Stamped at generation, so a report always names the
    # voice that WROTE it rather than whatever the runner has selected today.
    voice_name: Optional[str] = None
    # #824: what the rewrite did — "applied", or why the baseline stands
    # ("default_voice", "switched_off", "over_budget", "transport_error",
    # "invented_numbers:…", "policy:…"). Without it a null voiced_message cannot be
    # told apart from a rewrite that failed, which is a hole in the audit the two-pass
    # design exists to provide. Optional so pre-#822 stored meta validates.
    voice_rewrite: Optional[str] = None


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
    # #944: the offer, minted for THIS reader on THIS read. Absent unless the
    # stored report carries one, the reader owns every resource it names, and the
    # proposed-action surface is switched on. Never stored — `report.offer` is the
    # durable half and carries no token.
    offer: Optional[CoachReportOfferCard] = None

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
