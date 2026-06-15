"""
Coach service — orchestrates context pack → LLM → validate → policy check → store.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import anthropic

logger = logging.getLogger(__name__)

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity
from app.models.coach_report import CoachReport
from app.models.coaching_relationship import CoachingRelationship
from app.schemas.coach import (
    CoachMessageReport,
    CoachReportContent,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
)
from app.schemas.coach_context import CoachContextPack, ContinuityContext
from app.services.coach.belief_store import write_back_beliefs
from app.services.coach.consolidation import enqueue_consolidation
from app.services.coach.context import build_context_pack
from app.services.coach.digest import build_report_digest
from app.services.coach.llm import AnthropicClient
from app.services.coach.output_contract import (
    RECORD_COACH_TAIL_TOOL,
    EmptyMessageError,
    is_opener_only,
    merge_opener,
    merge_report,
    parse_blocks,
)
from app.services.analysis.classifier import Classification, playbook_key
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    TWO_STAGE_PROMPT_ID,
    TWO_STAGE_PROMPT_IDS,
    build_system_prompt,
)
from app.services.coach.voice import resolve_voice
from app.services.coach.retrieval import fetch_latest_user_reply
from app.services.coach.validator import (
    PolicyViolation,
    validate_message_policy,
    validate_policy,
)

# Schema version is keyed by prompt FAMILY (ADR 0009): the legacy structured
# CoachReportContent output is schema 1.2; the A3 prose-message CoachMessageReport
# output is schema 2.0. The active version is resolved from the active prompt id's
# prefix, so a config flip of COACH_PROMPT_ID flips both the prompt and the cache
# identity together. `SCHEMA_VERSION` is retained as the legacy-family default for
# callers that import it.
SCHEMA_VERSION = "1.2"
SCHEMA_VERSION_BY_FAMILY = {
    "coach_report": "1.2",
    "coach_message": "2.0",
}

# Token budget for the A3 message call (thinking tokens count against it).
_MESSAGE_MAX_TOKENS = 8192

# Token budget for the A4 OPENER call. The opener is a brief reaction + a small
# tail, but adaptive-thinking tokens count against max_tokens too, so this leaves
# headroom for the private reasoning while keeping the opener materially lighter
# than the fuller turn. Tunable.
_OPENER_MAX_TOKENS = 2048

# #217: total attempts at the prose call before degrading to a fallback. The prose
# response is occasionally empty (no text block) but recovers on an immediate
# re-run, so one retry (2 attempts) reclaims that transient failure. Both stages
# (opener and fuller) share it.
_EMPTY_PROSE_ATTEMPTS = 2

# Token budget for the legacy structured JSON report. Was 1024, which truncated
# under claude-sonnet-4-6 (more verbose JSON than the retired model) — the report
# hit stop_reason=max_tokens mid-object and the partial/fenced text failed to
# parse, silently falling back. The structured report is bounded (≤6 takeaways,
# ≤3 next_steps, evidence arrays), so 4096 gives ample headroom without truncation.
_STRUCTURED_MAX_TOKENS = 4096

# Nudge appended when the model wrote prose but skipped the tail tool.
_TAIL_REMINDER = (
    "You wrote the message but did not call record_coach_tail. Call it now, "
    "exactly once, restating only what your message already said."
)

# Templated fallback prose when no usable message could be produced.
_FALLBACK_MESSAGE = (
    "I couldn't put together a full write-up for this run just now. Your metrics "
    "are recorded — take a look at the activity detail for the flags, splits and "
    "zones, and I'll pick this up properly on your next run."
)

# Templated fallback prose for the A4 opener (LLM/parse/transport failure): a
# brief, non-committal reaction that keeps the exchange alive (the fuller turn can
# still follow). Lands in opener_message, message stays empty.
_FALLBACK_OPENER_MESSAGE = (
    "Nice work getting that run in. I'll take a proper look and follow up shortly."
)


def is_two_stage_prompt(prompt_id: str) -> bool:
    """True when the active prompt drives the A4 two-stage Exchange cadence.

    The cadence (opener -> conditional fuller) is gated here, not unconditionally,
    so flipping COACH_PROMPT_ID back to coach_message_v1 or a coach_report_v* id
    serves the prior single-shot path with zero code change (AC8 rollback).
    coach_message_v3 (P1.1 voice) is two-stage exactly like coach_message_v2."""
    return prompt_id in TWO_STAGE_PROMPT_IDS


def _resolve_voice_for_activity(db: Session, activity: Activity):
    """Resolve the runner's declared coach voice for this activity's owner (P1.1).

    Loads the thin CoachingRelationship row (the row may not exist yet — a runner
    who never opened their profile) and resolves it to a VoiceProfile; a missing
    row or an undeclared voice resolves to the moderate default. This only affects
    voice-aware prompts (coach_message_v3) — render_voice_block is a no-op for every
    other prompt id, so the resolved voice is harmless under any other prompt."""
    relationship = (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == activity.user_id)
        .first()
    )
    return resolve_voice(relationship)


def active_schema_version(prompt_id: str) -> str:
    """The schema version for a prompt id, resolved by family prefix.

    Unknown prefixes fall back to the legacy structured family (1.2) so an
    unrecognised prompt id can never silently mint a new schema.
    """
    for family, version in SCHEMA_VERSION_BY_FAMILY.items():
        if prompt_id.startswith(family):
            return version
    return SCHEMA_VERSION


def get_active_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """Return the cached report row for the *active* version of this activity.

    The active version is the current (COACH_PROMPT_ID, SCHEMA_VERSION) pair.
    Rows from older prompt/schema versions are retained but ignored here, so a
    version bump causes a clean cache miss and regeneration rather than serving
    a stale shape.
    """
    activity_uuid = _coerce_uuid(activity_id)
    prompt_id = settings.COACH_PROMPT_ID
    return (
        db.query(CoachReport)
        .filter(
            CoachReport.activity_id == activity_uuid,
            CoachReport.prompt_id == prompt_id,
            CoachReport.schema_version == active_schema_version(prompt_id),
        )
        .first()
    )


def get_displayable_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """A report row suitable for DISPLAY: the active-version row if present, else
    the most recently created cached report of ANY (prompt_id, schema_version).

    This is the read/display fallback (#261) that keeps a COACH_PROMPT_ID flip from
    turning every historical activity into a cache-miss-that-regenerates: after a
    flip (e.g. activating P1.1 voice coach_message_v3), prior-version reports still
    render instead of 404-ing or triggering a synchronous regeneration that exceeds
    the gateway timeout (#260). It is a read concern ONLY — it never affects the M0
    versioned-cache identity used for regeneration (get_active_report_row and
    get_or_generate_coach_report are unchanged), so the background pipeline still
    generates the active version for new activities, and an explicit force=True
    regeneration still targets the active version.
    """
    active = get_active_report_row(db, activity_id)
    if active is not None:
        return active
    activity_uuid = _coerce_uuid(activity_id)
    return (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == activity_uuid)
        .order_by(CoachReport.created_at.desc())
        .first()
    )


async def get_or_generate_coach_report(
    db: Session, activity_id: str, force: bool = False
) -> Optional[CoachReportRead]:
    """
    Returns the cached report for the active version if one exists, otherwise
    generates a new one via LLM.

    force=True regenerates the active-version report (replacing only that row);
    prior-version reports are always retained, so regeneration is never
    destructive to history.
    """
    # A4: under the two-stage prompt, an on-demand request (the UI's generate /
    # force) wants the COMPLETE report now — which is the fuller turn. The opener
    # -> conditional-fuller cadence itself is driven by the pipeline jobs, not this
    # entry point. Under any single-shot prompt (coach_message_v1, coach_report_v*)
    # this delegation is skipped and the prior single-shot behaviour is preserved
    # exactly (AC8 rollback).
    if is_two_stage_prompt(settings.COACH_PROMPT_ID):
        return await generate_fuller(db, activity_id, force=force)

    activity_uuid = _coerce_uuid(activity_id)

    # Check cache (active version only)
    existing = get_active_report_row(db, activity_uuid)
    if existing and not force:
        return _to_read(existing)
    if force and existing:
        # Replace only the active-version row; prior versions are untouched.
        db.delete(existing)
        db.commit()

    # Load activity
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if not activity or not activity.metrics:
        return None

    # Resolve the active prompt up front: the prompt id gates the corpus pack
    # section (P1.2), so it must be known before the pack is built.
    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)

    # Build context pack
    pack = build_context_pack(db, activity, prompt_id=prompt_id)
    input_hash = pack.fingerprint()
    pack_dict = pack.to_serializable_dict()

    # Build prompt with activity-type playbook, selected from the axes (ADR 0007)
    classification = Classification.from_metrics(activity.metrics)
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(
        prompt_id, playbook_key(activity, classification), voice=voice
    )
    user_message = json.dumps(pack_dict, default=str)

    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    # Dispatch on prompt family (ADR 0009): the A3 prose-message path vs the
    # legacy structured path. Both normalise to a _GenOutcome so storage is shared.
    if prompt_id.startswith(MESSAGE_PROMPT_PREFIX):
        outcome = await _generate_message(client, system_prompt, user_message, pack)
    else:
        outcome = await _generate_structured(client, system_prompt, user_message, pack)

    read = _persist_report(
        db,
        activity=activity,
        prompt_id=prompt_id,
        schema_version=schema_version,
        pack=pack,
        pack_dict=pack_dict,
        input_hash=input_hash,
        outcome=outcome,
        existing=None,
        fire_learning_loop=True,
    )
    return read


@dataclass
class OpenerResult:
    """The result of generating an A4 opener (stage one).

    `report` is the stored opener-state row (read shape). `schedule_fuller_turn`
    is the exchange's depth decision the opener job consumes to decide whether to
    schedule stage two: the opener LLM's own judgment OR-ed with the deterministic
    safety override (a red-flag run always earns a fuller turn). `is_fallback` is
    True when the opener LLM/parse failed and a templated opener was stored.
    """

    report: Optional[CoachReportRead]
    schedule_fuller_turn: bool
    is_fallback: bool


async def generate_opener(db: Session, activity_id: str) -> Optional[OpenerResult]:
    """A4 stage one: a lightweight opener for a freshly-analysed activity.

    Builds the (salience-bearing) context pack, runs the opener-mode prose call,
    stores an opener-state CoachReport row (opener_message set, message empty, no
    digest, no learning-loop write-back — the opener is pre-input and carries
    none), and returns the schedule decision. Idempotent: if an active row already
    exists it is returned without a fresh LLM call. Returns None when the activity
    has no metrics (nothing to react to).
    """
    activity_uuid = _coerce_uuid(activity_id)
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if not activity or not activity.metrics:
        return None

    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)
    pack = build_context_pack(db, activity, prompt_id=prompt_id)

    existing = get_active_report_row(db, activity_uuid)
    if existing is not None:
        # Idempotent re-entry: the opener (or a later fuller) is already written.
        # Do not re-LLM; recompute the schedule decision from the stored bit + the
        # deterministic safety override, and recover with a fuller turn if the
        # stored opener was a fallback.
        schedule = existing.is_fallback or \
            bool((existing.report or {}).get("schedule_fuller_turn")) or \
            pack.salience.safety_override.force_fuller
        return OpenerResult(
            report=_to_read(existing),
            schedule_fuller_turn=schedule,
            is_fallback=existing.is_fallback,
        )

    input_hash = pack.fingerprint()
    pack_dict = pack.to_serializable_dict()
    # The opener is a brief reaction, not a playbook-driven analysis (mode="opener"
    # ignores the playbook), so the system prompt is the lean opener form. The voice
    # block (P1.1) rides both stages, so the opener already speaks in the declared
    # voice.
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(prompt_id, mode="opener", voice=voice)
    user_message = json.dumps(pack_dict, default=str)
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY, model=settings.COACH_MODEL_ID
    )
    outcome = await _generate_message(
        client, system_prompt, user_message, pack, is_opener=True
    )

    read = _persist_report(
        db,
        activity=activity,
        prompt_id=prompt_id,
        schema_version=schema_version,
        pack=pack,
        pack_dict=pack_dict,
        input_hash=input_hash,
        outcome=outcome,
        existing=None,
        fire_learning_loop=False,  # the opener writes nothing to durable memory
    )
    # Hybrid salience: the opener LLM's judgment OR the deterministic safety
    # override (the model can never stay quiet on a red-flag run). A fallback
    # opener (the LLM hiccuped) ALSO schedules a fuller turn, so the substantive
    # coaching can still recover on the retried fuller call rather than the
    # exchange silently producing nothing.
    schedule = outcome.is_fallback or \
        bool(outcome.report_dump.get("schedule_fuller_turn")) or \
        pack.salience.safety_override.force_fuller
    return OpenerResult(report=read, schedule_fuller_turn=schedule, is_fallback=outcome.is_fallback)


async def generate_fuller(
    db: Session, activity_id: str, *, force: bool = False
) -> Optional[CoachReportRead]:
    """A4 stage two (also the on-demand path under the two-stage prompt): the deep
    prose coaching turn.

    Reads the opener prose (from the same evolving row) and any chat reply as
    continuity, builds the full context pack, runs the fuller-mode prose call, and
    writes the result onto the SAME coach_reports row IN PLACE when an opener-state
    row exists (preserving the opener prose so both halves survive), else inserts a
    fresh row. Fires the learning-loop write-back + Consolidation on completion
    (non-fallback only). A complete (fuller) row is returned from cache unless
    `force`. Returns None when the activity has no metrics, or when the active
    prompt is not the two-stage one (a stale caller after a rollback must not run
    the fuller-mode prose call under a single-shot prompt — #216).
    """
    if not is_two_stage_prompt(settings.COACH_PROMPT_ID):
        logger.info(
            "generate_fuller skipped: active prompt %s is single-shot",
            settings.COACH_PROMPT_ID,
        )
        return None

    activity_uuid = _coerce_uuid(activity_id)

    existing = get_active_report_row(db, activity_uuid)
    # Capture the opener prose BEFORE any force-delete, so a force-regenerate of a
    # complete two-line row still preserves the opener half of the thread (the
    # brief's "preserving both halves" survives force too).
    opener_prose = (existing.report or {}).get("opener_message") if existing else None
    if force and existing is not None:
        # A manual regenerate produces a fresh complete report; prior versions are
        # untouched (force replaces only the active-version row).
        db.delete(existing)
        db.commit()
        existing = None
    if existing is not None and not is_opener_only(existing.report):
        # A complete fuller turn is already cached for this version.
        return _to_read(existing)

    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if not activity or not activity.metrics:
        return None

    # Continuity: the opener prose this exchange already sent (preserved above,
    # so it survives a force-delete) + any chat reply since (the check-in already
    # rides the pack).
    continuity = ContinuityContext(
        opener_message=opener_prose,
        reply=fetch_latest_user_reply(db, activity_uuid),
    )

    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)
    pack = build_context_pack(db, activity, continuity=continuity, prompt_id=prompt_id)
    input_hash = pack.fingerprint()
    pack_dict = pack.to_serializable_dict()
    classification = Classification.from_metrics(activity.metrics)
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(
        prompt_id, playbook_key(activity, classification), mode="fuller", voice=voice
    )
    user_message = json.dumps(pack_dict, default=str)
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY, model=settings.COACH_MODEL_ID
    )
    outcome = await _generate_message(client, system_prompt, user_message, pack)

    # Preserve the opener prose on the evolving row — the fuller LLM does not emit
    # opener_message, so carry it forward so the two-line thread (opener + fuller)
    # survives in storage and on the frontend.
    if outcome.is_fallback and opener_prose:
        # #217: a fuller fallback must NOT lock the exchange. The fuller-shaped
        # fallback carries a non-empty `message`, which would make the row complete
        # (not opener-only) and defeat both recovery paths: generate_fuller would
        # cache-hit it and the reply path's is_opener_only gate would reject it, so a
        # safety-forced (red-flag) turn dies on one transient LLM failure. Keep the
        # row OPENER-ONLY instead (message empty, opener prose preserved) — exactly
        # the opener-fallback pattern — so the reply/force/timer paths regenerate the
        # substantive turn. The row stays is_fallback (not notified, no learning loop).
        outcome.report_dump = CoachMessageReport(
            message="", opener_message=opener_prose, tail_degraded=True
        ).model_dump()
    elif opener_prose:
        outcome.report_dump["opener_message"] = opener_prose

    return _persist_report(
        db,
        activity=activity,
        prompt_id=prompt_id,
        schema_version=schema_version,
        pack=pack,
        pack_dict=pack_dict,
        input_hash=input_hash,
        outcome=outcome,
        existing=existing,  # in-place update of the opener row, or None -> insert
        fire_learning_loop=True,
    )


def _persist_report(
    db: Session,
    *,
    activity: Activity,
    prompt_id: str,
    schema_version: str,
    pack: CoachContextPack,
    pack_dict: dict,
    input_hash: str,
    outcome: "_GenOutcome",
    existing: Optional[CoachReport],
    fire_learning_loop: bool,
) -> Optional[CoachReportRead]:
    """Build the meta + digest, persist the report (in-place UPDATE of `existing`
    or a fresh INSERT), and optionally fire the learning loop. Shared by the
    single-shot path, the opener, and the fuller turn so storage is identical.

    The digest is stored only for a COMPLETE non-fallback report — an opener-only
    row (is_opener_only) and any fallback carry no digest, so they never feed the
    M4/M7 prior-exchange reads. The learning loop fires only on a clean store of a
    non-fallback report and only when the caller asked for it (the opener never
    does). On an INSERT race the row that landed first wins and the loop is skipped.
    """
    activity_uuid = activity.id

    if outcome.tail_degraded:
        # Monitoring (A3): one greppable WARNING per stored degraded tail.
        logger.warning(
            "coach_tail_degraded: stored a prose message with a degraded tail "
            "(activity=%s, prompt=%s); the learning loop abstains on this report",
            activity_uuid,
            prompt_id,
        )

    meta = CoachReportMeta(
        confidence=pack.metrics.confidence,
        model_id=settings.COACH_MODEL_ID,
        prompt_id=prompt_id,
        schema_version=schema_version,
        input_hash=input_hash,
        generated_at=datetime.now(timezone.utc),
        policy_violations=outcome.policy_violations,
        tail_degraded=outcome.tail_degraded,
    )

    # A2a digest: only for a complete non-fallback report (not an opener-only row,
    # not a fallback). Guarded so a digest hiccup never blocks storage.
    report_digest = None
    if not outcome.is_fallback and not is_opener_only(outcome.report_dump):
        try:
            report_digest = build_report_digest(
                outcome.report_dump, activity.start_date
            ).model_dump()
        except Exception:  # noqa: BLE001 — digest is a derived convenience
            logger.exception(
                "exchange digest projection failed for activity %s", activity_uuid
            )

    if existing is not None:
        # A4 in-place UPDATE: the fuller turn fills the opener's evolving row. The
        # cache identity (activity_id, prompt_id, schema_version) is unchanged — it
        # is the same physical row — so the M0 versioned cache is preserved.
        existing.report = outcome.report_dump
        existing.meta = meta.model_dump(mode="json")
        existing.context_pack = pack_dict
        existing.raw_llm_response = outcome.raw_response
        existing.is_fallback = outcome.is_fallback
        existing.digest = report_digest
        db.add(existing)
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        db_report = CoachReport(
            activity_id=activity_uuid,
            prompt_id=prompt_id,
            schema_version=schema_version,
            report=outcome.report_dump,
            meta=meta.model_dump(mode="json"),
            context_pack=pack_dict,
            raw_llm_response=outcome.raw_response,
            is_fallback=outcome.is_fallback,
            digest=report_digest,
        )
        db.add(db_report)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request generated the active-version row first. Yield to
            # the row that landed (and do NOT fire the learning loop — the winner's
            # generation owns it).
            db.rollback()
            winner = get_active_report_row(db, activity_uuid)
            if winner is not None:
                return _to_read(winner)
            raise
        db.refresh(db_report)
        row = db_report

    if fire_learning_loop and not outcome.is_fallback:
        _fire_learning_loop(db, activity, pack)

    return _to_read(row)


def _fire_learning_loop(db: Session, activity: Activity, pack: CoachContextPack) -> None:
    """The M4-M10 durable-memory write-back, fired on completion of a COMPLETE
    non-fallback report (the single-shot report, or the A4 fuller turn — never the
    opener, which is pre-input and carries none).

    M8 belief write-back feeds the durable belief store the next report reads
    (best-effort inside, self-guarded by `beliefs_written_at` so a re-read or
    force never double-counts). A2c consolidation re-grounds the narrative in the
    background — enqueued, never awaited, so the turn never blocks; idempotent, so
    no sentinel is needed.
    """
    write_back_beliefs(db, activity, pack)
    enqueue_consolidation(activity.user_id)


@dataclass
class _GenOutcome:
    """The normalised result of either generation path, so storage is shared.

    `report_dump` is the dict stored in CoachReport.report (a CoachReportContent
    for the structured family, a CoachMessageReport for the prose family).
    """
    report_dump: dict
    raw_response: str
    is_fallback: bool
    policy_violations: List[str] = field(default_factory=list)
    tail_degraded: bool = False


# --- structured family (legacy CoachReportContent, schema 1.2) ----------------


def _structured_fallback_dump() -> dict:
    """The templated structured fallback report (LLM/parse/transport failure)."""
    return CoachReportContent(
        key_takeaways=[
            {"text": "Analysis is temporarily unavailable for this activity."},
            {"text": "Your metrics have been recorded and can be reviewed in the detail view."},
        ],
        next_steps=[
            {
                "action": "Review your metrics manually",
                "details": "Check the activity detail page for flags and zones.",
                "why": "The AI coaching summary could not be generated for this session.",
            }
        ],
    ).model_dump()


async def _generate_structured(
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
) -> _GenOutcome:
    """The legacy structured path: constrained-JSON generation, policy gate, one
    corrective retry, templated fallback on failure. Behaviour preserved from the
    prior inline implementation."""
    raw_response = ""
    policy_violations: List[str] = []
    try:
        raw_response = await client.generate_json(
            system=system_prompt, user=user_message, max_tokens=_STRUCTURED_MAX_TOKENS,
        )
        cleaned = _strip_code_fences(raw_response)
        parsed = json.loads(cleaned)
        content = CoachReportContent.model_validate(parsed)

        violations = validate_policy(content, pack)
        if violations:
            logger.info(
                "Policy violations detected: %s — attempting retry",
                [v.rule for v in violations],
            )
            content, retry_violations = await _retry_with_fixes(
                client, system_prompt, user_message, pack, violations
            )
            if retry_violations:
                logger.warning(
                    "Policy violations persisted after retry: %s",
                    [v.rule for v in retry_violations],
                )
                policy_violations = [v.rule for v in retry_violations]
    except (json.JSONDecodeError, ValidationError, anthropic.APIError) as e:
        logger.error("Coach report parse/validation/transport error: %s", e)
        return _GenOutcome(
            report_dump=_structured_fallback_dump(),
            raw_response=raw_response, is_fallback=True,
        )

    return _GenOutcome(
        report_dump=content.model_dump(),
        raw_response=raw_response,
        is_fallback=False,
        policy_violations=policy_violations,
    )


# --- prose-message family (A3 CoachMessageReport, schema 2.0) ------------------


def _message_fallback_outcome() -> _GenOutcome:
    """A fallback that keeps the runner inside the prose family: a templated
    message and a degraded tail. A tail is never stored without its message."""
    return _GenOutcome(
        report_dump=CoachMessageReport(
            message=_FALLBACK_MESSAGE, tail_degraded=True
        ).model_dump(),
        raw_response="",
        is_fallback=True,
        tail_degraded=True,
    )


def _serialize_blocks(content_blocks: list) -> str:
    """A readable serialisation of the response blocks for the debug column: the
    prose message followed by the tail JSON. Best-effort; never raises."""
    try:
        parsed = parse_blocks(content_blocks)
        out = parsed.message
        if parsed.tail is not None:
            out += "\n\n[tail]\n" + json.dumps(parsed.tail, default=str, indent=2)
        return out
    except Exception:  # noqa: BLE001 — debug serialisation must never break storage
        return ""


async def _call_message(
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    *,
    max_tokens: int = _MESSAGE_MAX_TOKENS,
):
    return await client.generate_coach_message(
        system=system_prompt,
        user=user_message,
        tools=[RECORD_COACH_TAIL_TOOL],
        max_tokens=max_tokens,
    )


def _opener_fallback_outcome() -> "_GenOutcome":
    """A4 opener fallback: a templated brief reaction in opener_message (message
    empty), a degraded tail, no schedule judgment. The deterministic safety
    override still drives scheduling on a red-flag run."""
    return _GenOutcome(
        report_dump=CoachMessageReport(
            message="", opener_message=_FALLBACK_OPENER_MESSAGE, tail_degraded=True,
        ).model_dump(),
        raw_response="",
        is_fallback=True,
        tail_degraded=True,
    )


async def _generate_message(
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
    *,
    is_opener: bool = False,
) -> _GenOutcome:
    """The prose-message path: one adaptive-thinking call producing prose + a tool
    tail, with stop_reason-aware retries, the policy gate over message+tail, and
    degrade-not-withhold semantics (ADR 0009).

    `is_opener` selects the A4 opener variant: a shorter max_tokens, the
    opener merge (prose -> opener_message, message empty), and the opener fallback.
    The stop_reason / retry / policy / medical-overreach-forces-fallback discipline
    is identical for both stages, so the opener is policed exactly as the fuller
    turn (AC3).

    stop_reason handling: `refusal` -> templated fallback; `max_tokens` -> one
    re-attempt before degrading to whatever arrived; `end_turn` with no tail ->
    one corrective retry asking for the tool, then degrade. A surviving
    medical-overreach violation forces a fallback (the one strengthening over the
    structured path), since prose renders verbatim.
    """
    max_tokens = _OPENER_MAX_TOKENS if is_opener else _MESSAGE_MAX_TOKENS
    merge = merge_opener if is_opener else merge_report
    fallback = _opener_fallback_outcome if is_opener else _message_fallback_outcome
    # #217: the prose call occasionally returns no text block at all (an empty-prose
    # response, observed as transient — an immediate re-run recovers it). A single
    # in-process retry turns that one hiccup into a real turn instead of a fallback,
    # which matters most for the safety-forced fuller turn whose substantive coaching
    # would otherwise be silently dropped. The retry is scoped to that specific
    # failure; refusal / max_tokens / tail-skip keep their existing one-shot handling.
    report = None
    raw_response = ""
    for attempt in range(_EMPTY_PROSE_ATTEMPTS):
        try:
            result = await _call_message(client, system_prompt, user_message, max_tokens=max_tokens)
            if result.stop_reason == "refusal":
                logger.warning("coach message refused; storing fallback")
                return fallback()
            if result.stop_reason == "max_tokens":
                logger.info("coach message truncated (max_tokens); retrying once")
                retried = await _call_message(
                    client, system_prompt, user_message, max_tokens=max_tokens
                )
                if retried.stop_reason != "refusal":
                    result = retried

            parsed = parse_blocks(result.content_blocks)
            # Tail-skip corrective retry: the model is allowed to skip the tool under
            # tool_choice=auto. One nudge before accepting a degraded tail.
            if parsed.tail is None and result.stop_reason == "end_turn":
                logger.info("coach message skipped the tail tool; one corrective retry")
                nudge = f"{user_message}\n\n{_TAIL_REMINDER}"
                result2 = await _call_message(
                    client, system_prompt, nudge, max_tokens=max_tokens
                )
                parsed2 = parse_blocks(result2.content_blocks)
                if parsed2.tail is not None and parsed2.message.strip():
                    parsed, result = parsed2, result2

            raw_response = _serialize_blocks(result.content_blocks)
            report = merge(parsed)
            break
        except EmptyMessageError:
            if attempt + 1 < _EMPTY_PROSE_ATTEMPTS:
                logger.warning(
                    "coach response carried no prose message; retrying once (#217)"
                )
                continue
            logger.warning(
                "coach response carried no prose message after retry; storing fallback"
            )
            return fallback()
        except anthropic.APIError as e:
            logger.error("coach message transport error: %s", e)
            return fallback()

    violations = validate_message_policy(report, pack)
    if violations:
        logger.info(
            "Message policy violations detected: %s — attempting retry",
            [v.rule for v in violations],
        )
        report, remaining = await _retry_message_with_fixes(
            client, system_prompt, user_message, pack, violations, report,
            is_opener=is_opener,
        )
        if remaining:
            # ADR 0009: medical overreach surviving the retry forces a fallback,
            # because the prose renders verbatim to the runner. Use the
            # STAGE-CORRECT fallback (the variable bound above), so an opener
            # overreach yields an opener-shaped fallback (message empty,
            # opener_message set) that stays opener-only — otherwise the
            # safety-forced fuller turn would cache-hit a non-opener-only fallback
            # and never regenerate, silently defeating the safety floor on exactly
            # the red-flag runs that trip rule 5.
            if any(v.rule == "medical_overreach" for v in remaining):
                logger.warning("medical overreach survived retry; forcing fallback")
                return fallback()
            logger.warning(
                "Message policy violations persisted after retry: %s",
                [v.rule for v in remaining],
            )
            return _GenOutcome(
                report_dump=report.model_dump(),
                raw_response=raw_response,
                is_fallback=False,
                policy_violations=[v.rule for v in remaining],
                tail_degraded=report.tail_degraded,
            )

    return _GenOutcome(
        report_dump=report.model_dump(),
        raw_response=raw_response,
        is_fallback=False,
        tail_degraded=report.tail_degraded,
    )


async def _retry_message_with_fixes(
    client: AnthropicClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
    prior: CoachMessageReport,
    *,
    is_opener: bool = False,
) -> tuple[CoachMessageReport, List[PolicyViolation]]:
    """Re-prompt once with fix instructions for the message-policy violations.
    Returns (report, remaining_violations). If the retry produces nothing usable,
    the prior report and its violations are kept (so a surviving overreach still
    forces the fallback upstream). `is_opener` keeps the opener's merge + token
    budget on the retry too."""
    max_tokens = _OPENER_MAX_TOKENS if is_opener else _MESSAGE_MAX_TOKENS
    merge = merge_opener if is_opener else merge_report
    fix_instructions = "\n".join(
        f"- {v.rule}: {v.fix_instruction}" for v in violations
    )
    retry_message = (
        "Your previous message had policy violations. Rewrite the message and the "
        "tail to fix these issues ONLY (keep everything else the same):\n"
        f"{fix_instructions}\n\nOriginal context:\n{original_user_message}"
    )
    try:
        result = await _call_message(
            client, system_prompt, retry_message, max_tokens=max_tokens
        )
        if result.stop_reason == "refusal":
            return prior, validate_message_policy(prior, pack)
        report = merge(parse_blocks(result.content_blocks))
    except (EmptyMessageError, anthropic.APIError) as e:
        logger.error("Coach message retry error: %s", e)
        return prior, validate_message_policy(prior, pack)
    return report, validate_message_policy(report, pack)


async def _retry_with_fixes(
    client: AnthropicClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
) -> tuple[CoachReportContent, List[PolicyViolation]]:
    """
    Re-prompt the LLM once with fix instructions for policy violations.
    Returns (content, remaining_violations). Never loops more than once.
    """
    fix_instructions = "\n".join(
        f"- {v.rule}: {v.fix_instruction}" for v in violations
    )
    retry_message = (
        f"Your previous response had policy violations. Fix these issues ONLY "
        f"(keep everything else the same):\n{fix_instructions}\n\n"
        f"Original context:\n{original_user_message}"
    )

    try:
        raw = await client.generate_json(
            system=system_prompt,
            user=retry_message,
            max_tokens=_STRUCTURED_MAX_TOKENS,
        )
        cleaned = _strip_code_fences(raw)
        parsed = json.loads(cleaned)
        content = CoachReportContent.model_validate(parsed)

        # Re-validate — but don't loop again
        remaining = validate_policy(content, pack)
        return content, remaining

    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Coach report retry parse error: %s", e)
        raise


def _coerce_uuid(value) -> uuid.UUID:
    """Accept either a UUID or its string form. Postgres tolerates the latter
    via implicit cast; SQLite (used in tests) does not, so coerce up front."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) that LLMs sometimes add.

    Tolerates an UNTERMINATED opening fence: a closing-fence-less or truncated
    response (e.g. ```json\\n{...) would otherwise be handed to json.loads with a
    leading backtick and fail as "Expecting value: line 1 column 1". Stripping a
    leading fence even without its close lets the JSON body parse. (Defence in
    depth alongside `_STRUCTURED_MAX_TOKENS`, which prevents the truncation that
    drops the closing fence in the first place.)"""
    stripped = text.strip()
    # Match a fully fenced block ```json\n...\n``` or ```\n...\n```.
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Otherwise strip a leading opening fence and any trailing fence independently.
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def _to_read(db_report: CoachReport) -> CoachReportRead:
    """Convert a DB CoachReport row into the read schema.

    The stored report is validated against the model for its schema-version family
    (ADR 0009): the A3 prose CoachMessageReport for 2.x rows, the legacy structured
    CoachReportContent otherwise. Keying off the stored row's own schema_version
    (not the active config) means old rows always read back in their own shape, so
    a config flip never mis-parses history (AC7).
    """
    meta = CoachReportMeta.model_validate(db_report.meta)
    if str(db_report.schema_version or "").startswith("2"):
        report = CoachMessageReport.model_validate(db_report.report)
    else:
        report = CoachReportContent.model_validate(db_report.report)
    return CoachReportRead(
        id=db_report.id,
        activity_id=db_report.activity_id,
        report=report,
        meta=meta,
        debug=CoachReportDebug(
            context_pack=db_report.context_pack or {},
            system_prompt=PROMPT_VERSIONS.get(meta.prompt_id, "unknown"),
            raw_llm_response=db_report.raw_llm_response,
        ),
        created_at=db_report.created_at,
    )
