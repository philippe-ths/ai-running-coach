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
from sqlalchemy.orm import Session, undefer

from app.core.config import settings
from app.models import Activity, Block
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
from app.services.coach.budget import over_budget as budget_over, record as budget_record
from app.services.coach.memory_update import enqueue_memory_update
from app.services.coach.context import build_context_pack
from app.services.coach.coach_framing import frame_pack
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
    is_grouped_pack_prompt,
    is_metrics_coach_framed_prompt,
)
from app.services.coach.prompt_features import PromptFeature, has_feature
from app.services.coach.receipt_voice import voice_fingerprint
from app.services.coach.voice import resolve_voice
from app.services.coach.stance import resolve_stance
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

# #295: the ESCALATED budget used to RETRY a message call that truncated at
# max_tokens (or returned no prose). A multi-activity block produces a richer pack,
# so the model reasons longer under adaptive thinking and can spend the whole
# budget on thinking before emitting the prose — retrying at the SAME budget hits
# the identical wall and degrades to a canned fallback (the live #295 failure).
# Retrying with more room lets the prose land. Generous headroom over the worst
# observed thinking spend; only paid on the rare truncation retry.
_MESSAGE_MAX_TOKENS_ESCALATED = 16384

# Token budget for the A4 OPENER call. The opener is a brief reaction + a small
# tail, but adaptive-thinking tokens count against max_tokens too, so this leaves
# headroom for the private reasoning while keeping the opener materially lighter
# than the fuller turn. Tunable.
_OPENER_MAX_TOKENS = 2048

# #295: the escalated retry budget for the lean opener (kept proportionally smaller
# than the fuller's, since the opener is meant to be brief — a heavy thinking spend
# on an opener is itself suspect, but the extra room still beats a fallback).
_OPENER_MAX_TOKENS_ESCALATED = 4096

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


def _llm_pack_message(pack_dict: dict, prompt_id: Optional[str]) -> str:
    """Serialize the pack for the outgoing LLM message. Under a metrics-coach-framed
    prompt (ADR 0026 Slice 4, #680) the leaf VALUES are reframed to coach-native units
    for the LLM view; the caller still STORES the canonical `pack_dict` (framing is a
    one-way, lossy view, so the stored/re-parsed pack stays typed). Byte-identical to the
    prior `json.dumps(pack_dict, default=str)` for every non-framed prompt."""
    view = frame_pack(pack_dict) if is_metrics_coach_framed_prompt(prompt_id) else pack_dict
    return json.dumps(view, default=str)


def is_two_stage_prompt(prompt_id: str) -> bool:
    """True when the active prompt drives the A4 two-stage Exchange cadence.

    The cadence (opener -> conditional fuller) is gated here, not unconditionally,
    so flipping COACH_PROMPT_ID back to coach_message_v1 or a coach_report_v* id
    serves the prior single-shot path with zero code change (AC8 rollback).
    coach_message_v3 (P1.1 voice) is two-stage exactly like coach_message_v2."""
    return prompt_id in TWO_STAGE_PROMPT_IDS


def is_receipt_cadence(prompt_id: str) -> bool:
    """True when the active cadence is the #296 receipt cadence: an instant
    deterministic per-activity receipt plus one full LLM report ~30 min after the
    session, replacing the debounced LLM opener + 3h fuller timer.

    Gated by the COACH_RECEIPT_CADENCE flag AND a two-stage (message-family) prompt:
    the receipt itself has no prompt, but the full report is the configured prompt's
    fuller mode, which only exists for a two-stage prompt. The flag is orthogonal to
    COACH_PROMPT_ID, so the cadence and the prompt CONTENT roll back independently —
    flipping the flag off restores the prior two-stage opener/fuller cadence with zero
    code change, and it is inert under any single-shot prompt (no fuller mode to fire)."""
    return settings.COACH_RECEIPT_CADENCE and is_two_stage_prompt(prompt_id)


def _resolve_voice_for_activity(db: Session, activity: Activity):
    """Resolve the runner's declared coach voice for this activity's owner (P1.1).

    Loads the thin CoachingRelationship row (the row may not exist yet — a runner
    who never opened their profile) and resolves it to a VoiceProfile; a missing
    row or an undeclared voice resolves to the moderate default. This only affects
    voice-aware prompts (coach_message_v3) — render_voice_block is a no-op for every
    other prompt id, so the resolved voice is harmless under any other prompt."""
    # #522: COACH_RELATIONSHIP_ENABLED off => never read the relationship; resolve to
    # the moderate default voice (the runner's declared voice has no effect).
    if not settings.COACH_RELATIONSHIP_ENABLED:
        return resolve_voice(None)
    relationship = (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == activity.user_id)
        .first()
    )
    return resolve_voice(relationship)


def report_voice_stale(db: Session, row: Optional[CoachReport]) -> bool:
    """True when `row` is the ACTIVE-version report under a voice-aware prompt but was
    generated under a DIFFERENT voice than the runner's current one — so it should be
    regenerated to honour the new voice (the read endpoint flags it and the frontend
    auto-triggers the async regen on a stale view).

    False for: a non-voice-aware active prompt (voice is inert), a cross-version
    displayable fallback (#261 — left alone so a COACH_PROMPT_ID flip never storms
    regens), or a row already on the current voice. A NULL voice_key on an active
    voice-aware row reads as STALE: its voice is unknown (the row predates the
    voice_key column or a non-voice prompt), so it regenerates ONCE onto the current
    voice and is then current. This is the read-side mirror of the voice_key the
    generate path stamps; it never itself writes or regenerates."""
    if row is None:
        return False
    prompt_id = settings.COACH_PROMPT_ID
    # Only the active-version row can be voice-stale; a stale-shape displayable
    # fallback is intentionally served as-is (#261).
    if row.prompt_id != prompt_id or row.schema_version != active_schema_version(prompt_id):
        return False
    if not has_feature(prompt_id, PromptFeature.VOICE):
        return False
    activity = db.query(Activity).filter(Activity.id == row.activity_id).first()
    if activity is None:
        return False
    current = voice_fingerprint(_resolve_voice_for_activity(db, activity))
    return (row.voice_key or None) != current


def _resolve_stance_for_activity(db: Session, activity: Activity):
    """Resolve the runner's declared coaching stance for this activity's owner (P1.3).

    Loads the thin CoachingRelationship row (may not exist yet) and resolves it to a
    StanceProfile (selected school + the two emphasis axes); a missing row or an
    undeclared stance resolves to the default school (aerobic-base) + balanced
    emphasis. This only takes effect under a stance-aware prompt (coach_message_v5):
    build_context_pack threads the school into the corpus section and the emphasis
    into the stance section only under v5, so the resolved stance is harmless under
    any other prompt id (the P1.2/voice precedent)."""
    # #522: COACH_RELATIONSHIP_ENABLED off => never read the relationship; resolve to
    # the default school + balanced emphasis (the runner's declared stance has no effect).
    if not settings.COACH_RELATIONSHIP_ENABLED:
        return resolve_stance(None)
    relationship = (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == activity.user_id)
        .first()
    )
    return resolve_stance(relationship)


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


def get_block_primary_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """Block-aware DISPLAY fallback (#482): the displayable report of this
    activity's block PRIMARY, when the activity itself owns none.

    The session report is generated once per block, keyed to the primary activity
    (the run, else the longest member). A non-primary member therefore has zero
    coach_reports rows, so its page 404s and the panel spins forever. This lets the
    read path show the session's report on any member instead.

    Read-only, no generation: the M0 versioned-cache identity used for regeneration
    (get_active_report_row / get_or_generate_coach_report) is unchanged, so the
    per-activity "Re-run" still mints a member-specific report. Returns None when the
    activity is not in a block, IS its block's primary (it would have its own report),
    or the primary has no displayable report yet.
    """
    activity_uuid = _coerce_uuid(activity_id)
    activity = (
        db.query(Activity).filter(Activity.id == activity_uuid).first()
    )
    if activity is None or activity.block_id is None:
        return None
    block = db.query(Block).filter(Block.id == activity.block_id).first()
    if block is None or block.primary_activity_id == activity_uuid:
        return None
    return get_displayable_report_row(db, block.primary_activity_id)


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
    # #273: a force regenerate does NOT delete the active row up front. The LLM call
    # runs first and _persist_report updates the existing row in place (generate-
    # then-swap), so the single content-swapping commit is atomic — a worker death
    # mid-regen leaves the prior report intact instead of zero rows. Prior schema-
    # versions are untouched (the in-place update keeps the same cache identity).

    # Load activity. undefer raw_summary (#359): the context pack reads the
    # subject's raw_summary (headline, average_temp), so load it with the row.
    activity = (
        db.query(Activity)
        .options(undefer(Activity.raw_summary))
        .filter(Activity.id == activity_uuid)
        .first()
    )
    if not activity or not activity.metrics:
        return None

    # Resolve the active prompt up front: the prompt id gates the corpus pack
    # section (P1.2), so it must be known before the pack is built.
    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)

    # Build context pack. The runner's stance (P1.3) keys the corpus school and the
    # emphasis section under a stance-aware prompt; inert otherwise.
    stance = _resolve_stance_for_activity(db, activity)
    pack = build_context_pack(db, activity, prompt_id=prompt_id, stance=stance)
    input_hash = pack.fingerprint()
    # ADR 0026: serve the grouped pack under a grouped-pack prompt id, the flat pack
    # otherwise. The stored context_pack matches what the LLM saw; loaders (chat, eval)
    # are shape-tolerant via CoachContextPack.load. The fingerprint stays flat-based, so
    # the cache identity is unchanged.
    pack_dict = (
        pack.to_grouped_dict()
        if is_grouped_pack_prompt(prompt_id)
        else pack.to_serializable_dict()
    )

    # Build prompt with activity-type playbook, selected from the axes (ADR 0007)
    classification = Classification.from_metrics(activity.metrics)
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(
        prompt_id, playbook_key(activity, classification), voice=voice, pack=pack
    )
    user_message = _llm_pack_message(pack_dict, prompt_id)

    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    # Dispatch on prompt family (ADR 0009): the A3 prose-message path vs the
    # legacy structured path. Both normalise to a _GenOutcome so storage is shared.
    if prompt_id.startswith(MESSAGE_PROMPT_PREFIX):
        outcome = await _generate_message(
            client, system_prompt, user_message, pack, user_id=activity.user_id
        )
    else:
        outcome = await _generate_structured(
            client, system_prompt, user_message, pack, user_id=activity.user_id
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
        existing=existing,  # #273: in-place swap on force; None -> insert (first gen)
        fire_learning_loop=True,
        voice=voice,
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
    activity = (
        db.query(Activity)
        .options(undefer(Activity.raw_summary))  # #359: context reads subject raw_summary
        .filter(Activity.id == activity_uuid)
        .first()
    )
    if not activity or not activity.metrics:
        return None

    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)
    stance = _resolve_stance_for_activity(db, activity)
    pack = build_context_pack(db, activity, prompt_id=prompt_id, stance=stance)

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
    # ADR 0026: serve the grouped pack under a grouped-pack prompt id, the flat pack
    # otherwise. The stored context_pack matches what the LLM saw; loaders (chat, eval)
    # are shape-tolerant via CoachContextPack.load. The fingerprint stays flat-based, so
    # the cache identity is unchanged.
    pack_dict = (
        pack.to_grouped_dict()
        if is_grouped_pack_prompt(prompt_id)
        else pack.to_serializable_dict()
    )
    # The opener is a brief reaction, not a playbook-driven analysis (mode="opener"
    # ignores the playbook), so the system prompt is the lean opener form. The voice
    # block (P1.1) rides both stages, so the opener already speaks in the declared
    # voice.
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(prompt_id, mode="opener", voice=voice, pack=pack)
    user_message = _llm_pack_message(pack_dict, prompt_id)
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY, model=settings.COACH_MODEL_ID
    )
    outcome = await _generate_message(
        client, system_prompt, user_message, pack, is_opener=True, user_id=activity.user_id
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
        voice=voice,
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
    # Capture the opener prose up front, so a force-regenerate of a complete two-line
    # row still preserves the opener half of the thread (the brief's "preserving both
    # halves" survives force too) — the in-place swap below overwrites the report dict.
    opener_prose = (existing.report or {}).get("opener_message") if existing else None
    if existing is not None and not is_opener_only(existing.report) and not force:
        # A complete fuller turn is already cached for this version.
        return _to_read(existing)
    # #273: a force regenerate does NOT delete the active row up front. The fuller
    # call runs first and _persist_report (existing=existing below) updates the SAME
    # row in place (generate-then-swap), so a worker death mid-regen leaves the prior
    # complete report intact instead of zero rows. Prior schema-versions are untouched
    # (the in-place update keeps the same cache identity).

    activity = (
        db.query(Activity)
        .options(undefer(Activity.raw_summary))  # #359: context reads subject raw_summary
        .filter(Activity.id == activity_uuid)
        .first()
    )
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
    stance = _resolve_stance_for_activity(db, activity)
    pack = build_context_pack(db, activity, continuity=continuity, prompt_id=prompt_id, stance=stance)
    input_hash = pack.fingerprint()
    # ADR 0026: serve the grouped pack under a grouped-pack prompt id, the flat pack
    # otherwise. The stored context_pack matches what the LLM saw; loaders (chat, eval)
    # are shape-tolerant via CoachContextPack.load. The fingerprint stays flat-based, so
    # the cache identity is unchanged.
    pack_dict = (
        pack.to_grouped_dict()
        if is_grouped_pack_prompt(prompt_id)
        else pack.to_serializable_dict()
    )
    classification = Classification.from_metrics(activity.metrics)
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(
        prompt_id, playbook_key(activity, classification), mode="fuller", voice=voice, pack=pack
    )
    user_message = _llm_pack_message(pack_dict, prompt_id)
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY, model=settings.COACH_MODEL_ID
    )
    outcome = await _generate_message(
        client, system_prompt, user_message, pack, user_id=activity.user_id
    )

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
        voice=voice,
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
    voice=None,
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

    # #279: never replace a prior GOOD report with a fallback. A fallback may only
    # land where there is nothing good to protect — a first generation (existing is
    # None) or a fuller fallback over an opener-only row (the #217 recovery; an
    # opener-only row is not a complete report). A force-regenerate whose generation
    # failed therefore keeps the runner's prior complete, non-fallback report instead
    # of overwriting it with "analysis unavailable" (paired with the #273 generate-
    # then-swap: the prior row is held, so it is here to preserve).
    if (
        outcome.is_fallback
        and existing is not None
        and not existing.is_fallback
        and not is_opener_only(existing.report)
    ):
        logger.warning(
            "coach_regen_fallback_preserved_prior: a regeneration yielded a fallback; "
            "kept the prior good report (activity=%s, prompt=%s)",
            activity_uuid,
            prompt_id,
        )
        return _to_read(existing)

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

    # P1.1 voice freshness: stamp the voice this report speaks in, so a later voice
    # change is detectable (report_voice_stale). Null under a non-voice-aware prompt
    # (voice is inert there). Stamped on EVERY persist — including a fallback — so a
    # regenerated row is always current and a persistent LLM failure can never loop
    # the auto-regen (a fallback row reads as not-stale; the runner can still Re-run).
    voice_key = (
        voice_fingerprint(voice)
        if voice is not None and has_feature(prompt_id, PromptFeature.VOICE)
        else None
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
        existing.voice_key = voice_key
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
            voice_key=voice_key,
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
        _fire_learning_loop(db, activity, pack, prompt_id)

    return _to_read(row)


def _fire_learning_loop(
    db: Session, activity: Activity, pack: CoachContextPack, prompt_id: str
) -> None:
    """The durable-memory write-back, fired on completion of a COMPLETE non-fallback
    report (the single-shot report, or the A4 fuller turn — never the opener, which
    is pre-input and carries none).

    The runner memory update pass (M2, ADR 0025) rewrites the user's memory profile
    from source in the background — enqueued, never awaited, so the turn never
    blocks; idempotent single-row rewrite, so no sentinel is needed. Gated on the
    active prompt being memory-aware (so it is inert under v12, the report's own
    `prompt_id` matching the file's `has_feature(prompt_id, ...)` convention) AND the
    `COACH_MEMORY_ENABLED` switch. This is now the ONLY post-report enqueue: the
    legacy belief write-back + A2c narrative consolidation were retired in M4
    (ADR 0025), replaced wholesale by the runner memory profile.
    """
    if has_feature(prompt_id, PromptFeature.MEMORY) and settings.COACH_MEMORY_ENABLED:
        enqueue_memory_update(activity.user_id)


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
    *,
    user_id=None,
) -> _GenOutcome:
    """The legacy structured path: constrained-JSON generation, policy gate, one
    corrective retry, templated fallback on failure. Behaviour preserved from the
    prior inline implementation."""
    # P2.2: at the per-user/global spend cap, degrade to the deterministic
    # fallback BEFORE spending any tokens (the cap is observed before the call).
    if user_id is not None and budget_over(user_id):
        logger.info("coach_budget_degraded_structured", extra={"user_id": str(user_id)})
        return _GenOutcome(
            report_dump=_structured_fallback_dump(), raw_response="", is_fallback=True,
        )
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
    user_id=None,
):
    result = await client.generate_coach_message(
        system=system_prompt,
        user=user_message,
        tools=[RECORD_COACH_TAIL_TOOL],
        max_tokens=max_tokens,
    )
    # P2.2: count EVERY sub-call's spend (the retry/escalation fan-out is the
    # cost lever the going-live doc flags), keyed by activity-owner user_id.
    if user_id is not None:
        budget_record(user_id, client.model, result.input_tokens, result.output_tokens)
    return result


async def _reattempt_if_truncated(
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    result,
    *,
    escalated: int,
    context: str,
    user_id=None,
):
    """If a prose-message call stopped on the token ceiling, re-attempt it ONCE at
    a larger budget and return whichever result is better (#295/#282).

    The same single re-attempt the first generation phase already does, factored out
    so the policy-fix RETRY can reuse it identically (#282): the corrective retry
    re-sends the full original context PLUS the fix instructions, so it is a longer
    prompt than the first call and truncates more readily — without this, a complete
    first attempt is preferred over a truncated retry (#274) and the fix never lands.

    Truncation is detected uniformly by `stop_reason == "max_tokens"`. The extra
    call happens ONLY on a truncated result (one re-attempt, at most). The retried
    result replaces the original unless it refuses (a refusal is worse than a
    truncated turn — the caller still has prose to fall back on). A turn that is
    STILL truncated after the escalation is surfaced loudly rather than silently
    shipped half-written. `context` tags the log line (first / policy retry)."""
    if result.stop_reason != "max_tokens":
        return result
    logger.info(
        "coach message truncated (max_tokens) on %s; retrying at a larger budget (#295/#282)",
        context,
    )
    retried = await _call_message(
        client, system_prompt, user_message, max_tokens=escalated, user_id=user_id
    )
    if retried.stop_reason != "refusal":
        result = retried
    if result.stop_reason == "max_tokens":
        logger.warning(
            "coach message STILL truncated after budget escalation on %s (#295/#282); "
            "the turn may degrade — investigate the pack size / thinking spend",
            context,
        )
    return result


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


@dataclass
class _MsgAttempt:
    """One prose-message generation attempt, carrying the signals storage needs to
    pick the best of several (#274): its report, the raw serialization that produced
    it (so raw_llm_response always matches the STORED report, never a discarded
    attempt), whether the model ran out of tokens (a truncated, half-written message),
    and its surviving policy violations."""
    report: CoachMessageReport
    raw_response: str
    truncated: bool
    violations: List[PolicyViolation]


def _choose_message_attempt(first: _MsgAttempt, retry: _MsgAttempt) -> _MsgAttempt:
    """Pick the better of two attempts to store (#274). A complete (non-truncated)
    attempt beats a truncated one — a runner is better served by a complete message
    that trips a tolerated non-medical rule than by a half-sentence. Among
    equally-(non-)truncated attempts, fewer surviving violations wins, and a tie goes
    to the corrective `retry`. The medical-overreach-forces-fallback rule is applied
    by the caller to whichever attempt this returns, so the safety floor is unaffected."""
    if first.truncated != retry.truncated:
        return retry if first.truncated else first
    return retry if len(retry.violations) <= len(first.violations) else first


async def _generate_message(
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
    *,
    is_opener: bool = False,
    user_id=None,
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
    escalated = _OPENER_MAX_TOKENS_ESCALATED if is_opener else _MESSAGE_MAX_TOKENS_ESCALATED
    merge = merge_opener if is_opener else merge_report
    fallback = _opener_fallback_outcome if is_opener else _message_fallback_outcome
    # P2.2: at the spend cap, degrade to the deterministic fallback BEFORE the
    # call (cap observed before tokens are spent; one user's cap never affects
    # another since the counter is keyed by activity-owner user_id).
    if user_id is not None and budget_over(user_id):
        logger.info("coach_budget_degraded_message", extra={"user_id": str(user_id)})
        return fallback()
    # #217: the prose call occasionally returns no text block at all (an empty-prose
    # response, observed as transient — an immediate re-run recovers it). A single
    # in-process retry turns that one hiccup into a real turn instead of a fallback,
    # which matters most for the safety-forced fuller turn whose substantive coaching
    # would otherwise be silently dropped.
    # #295: a truncation (or empty-prose) retry escalates the TOKEN BUDGET rather
    # than re-running at the same one that just truncated — a rich multi-activity
    # pack can spend the whole budget on thinking before any prose, so the same
    # budget fails identically and degrades to a canned fallback. More room lets the
    # prose land. The empty-prose retry (attempt 1) uses the escalated budget too.
    report = None
    raw_response = ""
    truncated = False
    for attempt in range(_EMPTY_PROSE_ATTEMPTS):
        budget = max_tokens if attempt == 0 else escalated
        try:
            result = await _call_message(
                client, system_prompt, user_message, max_tokens=budget, user_id=user_id
            )
            if result.stop_reason == "refusal":
                logger.warning("coach message refused; storing fallback")
                return fallback()
            # #295: a truncated first attempt re-attempts ONCE at a larger budget.
            # Shared with the policy-fix retry's truncation re-attempt (#282).
            result = await _reattempt_if_truncated(
                client, system_prompt, user_message, result,
                escalated=escalated, context="first attempt", user_id=user_id,
            )

            parsed = parse_blocks(result.content_blocks)
            # Tail-skip corrective retry: the model is allowed to skip the tool under
            # tool_choice=auto. One nudge before accepting a degraded tail.
            if parsed.tail is None and result.stop_reason == "end_turn":
                logger.info("coach message skipped the tail tool; one corrective retry")
                nudge = f"{user_message}\n\n{_TAIL_REMINDER}"
                result2 = await _call_message(
                    client, system_prompt, nudge, max_tokens=budget, user_id=user_id
                )
                parsed2 = parse_blocks(result2.content_blocks)
                if parsed2.tail is not None and parsed2.message.strip():
                    parsed, result = parsed2, result2

            raw_response = _serialize_blocks(result.content_blocks)
            truncated = result.stop_reason == "max_tokens"  # #274: a half-written msg
            report = merge(parsed)
            break
        except EmptyMessageError:
            if attempt + 1 < _EMPTY_PROSE_ATTEMPTS:
                logger.warning(
                    "coach response carried no prose message; retrying at a larger budget (#217/#295)"
                )
                continue
            logger.warning(
                "coach response carried no prose message after retry; storing fallback"
            )
            return fallback()
        except anthropic.APIError as e:
            logger.error("coach message transport error: %s", e)
            return fallback()

    # #274: track the first attempt with the raw serialization that produced it, so
    # whichever attempt we store carries ITS OWN raw_llm_response (never a discarded
    # one). The policy retry is judged against it rather than blindly replacing it.
    chosen = _MsgAttempt(
        report=report,
        raw_response=raw_response,
        truncated=truncated,
        violations=validate_message_policy(report, pack),
    )
    if chosen.violations:
        logger.info(
            "Message policy violations detected: %s — attempting retry",
            [v.rule for v in chosen.violations],
        )
        retry_attempt = await _retry_message_with_fixes(
            client, system_prompt, user_message, pack, chosen.violations,
            is_opener=is_opener, user_id=user_id,
        )
        if retry_attempt is not None:
            # #274: store the BETTER attempt — a complete attempt is not clobbered by
            # a truncated retry, and raw_response follows the report we keep.
            chosen = _choose_message_attempt(chosen, retry_attempt)

    if any(v.rule == "medical_overreach" for v in chosen.violations):
        # ADR 0009: medical overreach in the attempt we would store forces a fallback,
        # because the prose renders verbatim to the runner. Use the STAGE-CORRECT
        # fallback (the variable bound above), so an opener overreach yields an
        # opener-shaped fallback (message empty, opener_message set) that stays
        # opener-only — otherwise the safety-forced fuller turn would cache-hit a
        # non-opener-only fallback and never regenerate, silently defeating the
        # safety floor on exactly the red-flag runs that trip rule 5.
        logger.warning("medical overreach in the stored attempt; forcing fallback")
        return fallback()
    if chosen.violations:
        logger.warning(
            "Message policy violations persisted: %s",
            [v.rule for v in chosen.violations],
        )

    return _GenOutcome(
        report_dump=chosen.report.model_dump(),
        raw_response=chosen.raw_response,
        is_fallback=False,
        policy_violations=[v.rule for v in chosen.violations],
        tail_degraded=chosen.report.tail_degraded,
    )


async def _retry_message_with_fixes(
    client: AnthropicClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
    *,
    is_opener: bool = False,
    user_id=None,
) -> Optional[_MsgAttempt]:
    """Re-prompt once with fix instructions for the message-policy violations.
    Returns the retry as a _MsgAttempt (#274), or None when the retry produced
    nothing usable (refusal / empty prose / transport error) — the caller then keeps
    the original attempt. `is_opener` keeps the opener's merge + token budget on the
    retry too.

    #282: the corrective retry re-sends the full original context PLUS the fix
    instructions, so it is a longer prompt than the first call and truncates more
    readily. When this retry itself stops on the token ceiling, re-attempt it ONCE at
    the escalated budget before its result is judged — mirroring the first-generation
    phase's truncation re-attempt — so the corrective attempt is less likely to come
    back truncated and discarded by the #274 prefer-complete-over-truncated rule. The
    extra call happens ONLY on a truncated retry."""
    max_tokens = _OPENER_MAX_TOKENS if is_opener else _MESSAGE_MAX_TOKENS
    escalated = _OPENER_MAX_TOKENS_ESCALATED if is_opener else _MESSAGE_MAX_TOKENS_ESCALATED
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
            client, system_prompt, retry_message, max_tokens=max_tokens, user_id=user_id
        )
        if result.stop_reason == "refusal":
            return None
        # #282: a truncated policy-fix retry re-attempts ONCE at a larger budget
        # before being judged, exactly as the first generation phase does.
        # _reattempt_if_truncated keeps the original (truncated) result when the
        # re-attempt refuses, so the refusal check above still covers that case.
        result = await _reattempt_if_truncated(
            client, system_prompt, retry_message, result,
            escalated=escalated, context="policy retry", user_id=user_id,
        )
        report = merge(parse_blocks(result.content_blocks))
    except (EmptyMessageError, anthropic.APIError) as e:
        logger.error("Coach message retry error: %s", e)
        return None
    return _MsgAttempt(
        report=report,
        raw_response=_serialize_blocks(result.content_blocks),
        truncated=result.stop_reason == "max_tokens",
        violations=validate_message_policy(report, pack),
    )


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
