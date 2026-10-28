"""
Coach service — orchestrates context pack → LLM → validate → policy check → store.

The read side lives in `report_reads`, the LLM call paths in `report_generation`,
and voice/stance resolution plus the re-voice of a stored report in `report_voice`
(#1159). Their names are imported here so `service.<name>` keeps resolving for
every caller, and so a patch of `service._generate_message` /
`service._generate_structured` still reaches the entry points below, which look
them up in this namespace.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer

from app.core.config import settings
from app.models import Activity
from app.models.coach_report import CoachReport
from app.schemas.coach import (
    CoachMessageReport,
    CoachReportMeta,
    CoachReportRead,
)
from app.schemas.coach_context import CoachContextPack, ContinuityContext
from app.services.coach.turn import TurnKind, build_client
from app.services.coach.memory_store import get_memory
from app.services.coach.memory_update import enqueue_memory_update
from app.services.coach.context import build_context_pack
from app.services.coach.coach_framing import coach_llm_view
from app.services.coach.digest import build_report_digest
from app.services.coach.output_contract import is_opener_only
from app.services.analysis.classifier import Classification, playbook_key
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    TWO_STAGE_PROMPT_IDS,
    build_system_prompt,
    is_grouped_pack_prompt,
)
from app.services.coach.prompt_features import PromptFeature, has_feature
from app.services.coach.receipt_voice import voice_fingerprint
from app.services.coach.voice import display_name as voice_display_name
from app.services.coach.retrieval import fetch_latest_user_reply
from app.services.coach.report_reads import (  # noqa: F401 — re-exported
    SCHEMA_VERSION,
    SCHEMA_VERSION_BY_FAMILY,
    _coerce_uuid,
    _to_read,
    active_schema_version,
    cached_row_is_servable,
    get_active_report_row,
    get_block_primary_report_row,
    get_displayable_report_leads,
    get_displayable_report_row,
)
from app.services.coach.report_generation import (  # noqa: F401 — re-exported
    MEDICAL_OVERREACH_RULE,
    _GenOutcome,
    _MsgAttempt,
    _apply_voice,
    _generate_message,
    _generate_structured,
    _strip_code_fences,
)
from app.services.coach.report_voice import (  # noqa: F401 — re-exported
    RevoiceOutcome,
    _resolve_stance_for_activity,
    _resolve_voice_for_activity,
    report_voice_stale,
    revoice_stored_report,
)


def _llm_pack_message(pack_dict: dict, prompt_id: Optional[str], *, mode: str = "fuller") -> str:
    """Serialize the pack for the outgoing LLM message. Two one-way VIEW transforms ride
    here, both gated on the prompt and both leaving the caller's STORED canonical `pack_dict`
    untouched (so the stored/re-parsed pack stays typed and every non-flagged prompt is
    byte-identical to the prior `json.dumps(pack_dict, default=str)`):

    - metrics-coach-framing (ADR 0026 Slice 4, #680): leaf VALUES reframed to coach-native
      units (km / min:sec/km / MM:SS / % of max).
    - salience settlement on the FULLER view (`mode != "opener"`), one of two mutually
      exclusive flags. SALIENCE_DROPPED (ADR 0026 Slice 5, #682) removes the section
      outright, on the reasoning that salience steers only the opener. SALIENCE_DEPTH
      (#655) keeps it, TRIMMED to `novelty`, because under the receipt cadence there IS
      no opener, which left the only LLM call prod makes with no read of whether the
      session gave the coach anything to say. Either way the deterministic safety force
      reads the canonical object, and the opener view is untouched by both."""
    return json.dumps(coach_llm_view(pack_dict, prompt_id, mode=mode), default=str)


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


def _load_subject(db: Session, activity_uuid) -> Optional[Activity]:
    """The subject activity a generation runs on, or None when there is nothing
    to coach (no row, or no analysis yet).

    undefer raw_summary (#359): the context pack reads the subject's raw_summary
    (headline, average_temp), so it is loaded with the row rather than lazily on
    first touch. All three generation entry points used to spell this out
    identically; a fourth would have had to remember the undefer.
    """
    activity = (
        db.query(Activity)
        .options(undefer(Activity.raw_summary))
        .filter(Activity.id == activity_uuid)
        .first()
    )
    if not activity or not activity.metrics:
        return None
    return activity


@dataclass
class _GenerationRequest:
    """Everything one LLM generation call for an activity needs, assembled once.

    Produced by `_assemble_generation_request` and consumed by the three
    generation entry points (get_or_generate / generate_opener / generate_fuller).
    `input_hash` is the pack fingerprint (the cache identity); `pack_dict` is the
    LLM-facing serialization (flat vs grouped per ADR 0026).
    """

    prompt_id: str
    schema_version: str
    pack: CoachContextPack
    pack_dict: dict
    input_hash: str
    system_prompt: str
    user_message: str
    voice: object


def _assemble_generation_request(
    db: Session,
    activity: Activity,
    *,
    mode: str = "fuller",
    continuity: Optional[ContinuityContext] = None,
) -> _GenerationRequest:
    """Assemble the shared generation request for one activity: the context pack,
    its fingerprint (the cache identity), the LLM-facing serialized pack (flat vs
    grouped per ADR 0026), the mode-appropriate system prompt, and the outgoing
    user message. Shared by all three generation entry points so the
    pack-build -> serialize -> prompt-render sequence — and the flat/grouped
    branch — lives in exactly one place.

    `mode` selects the two-stage form: "fuller" (the default, deep-coaching prompt
    + playbook + fuller view) or "opener" (the lean reaction prompt; the playbook
    is ignored and the opener view keeps salience). `continuity` (the opener prose
    + reply) rides the pack on the fuller turn only. Pure assembly (reads only):
    the caller owns cache-checking and persistence.
    """
    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)
    # The runner's stance (P1.3) keys the corpus school and the emphasis section
    # under a stance-aware prompt; inert otherwise.
    stance = _resolve_stance_for_activity(db, activity)
    pack = build_context_pack(
        db, activity, continuity=continuity, prompt_id=prompt_id, stance=stance
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
    # The activity-type playbook (ADR 0007) rides the fuller/single-shot system
    # prompt; build_system_prompt ignores it under mode="opener". The voice block
    # (P1.1) rides both stages via render_voice_block (a no-op for non-voice prompts).
    classification = Classification.from_metrics(activity.metrics)
    # #822: the report is generated VOICELESS. The runner's voice is resolved here
    # but deliberately not handed to the prompt — it rides the request so the
    # rewrite pass can apply it to the finished text, where it can shape delivery
    # without any route to the facts.
    voice = _resolve_voice_for_activity(db, activity)
    system_prompt = build_system_prompt(
        prompt_id,
        playbook_key(activity, classification),
        mode=mode,
        pack=pack,
    )
    user_message = _llm_pack_message(pack_dict, prompt_id, mode=mode)
    return _GenerationRequest(
        prompt_id=prompt_id,
        schema_version=schema_version,
        pack=pack,
        pack_dict=pack_dict,
        input_hash=input_hash,
        system_prompt=system_prompt,
        user_message=user_message,
        voice=voice,
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
    if cached_row_is_servable(existing, kind=TurnKind.REPORT, force=force):
        return _to_read(existing)
    # #273: a force regenerate does NOT delete the active row up front. The LLM call
    # runs first and _persist_report updates the existing row in place (generate-
    # then-swap), so the single content-swapping commit is atomic — a worker death
    # mid-regen leaves the prior report intact instead of zero rows. Prior schema-
    # versions are untouched (the in-place update keeps the same cache identity).

    activity = _load_subject(db, activity_uuid)
    if activity is None:
        return None

    # Assemble the generation request (pack, fingerprint, serialized pack, system
    # prompt, user message). Single-shot uses the default fuller mode + playbook.
    req = _assemble_generation_request(db, activity)

    client = build_client(TurnKind.REPORT, activity.user_id)

    # Dispatch on prompt family (ADR 0009): the A3 prose-message path vs the
    # legacy structured path. Both normalise to a _GenOutcome so storage is shared.
    if req.prompt_id.startswith(MESSAGE_PROMPT_PREFIX):
        outcome = await _generate_message(
            client, req.system_prompt, req.user_message, req.pack,
            user_id=activity.user_id, voice=req.voice,
        )
    else:
        outcome = await _generate_structured(
            client, req.system_prompt, req.user_message, req.pack, user_id=activity.user_id
        )

    return _persist_report(
        db,
        activity=activity,
        req=req,
        outcome=outcome,
        existing=existing,  # #273: in-place swap on force; None -> insert (first gen)
        fire_learning_loop=True,
    )


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
    activity = _load_subject(db, activity_uuid)
    if activity is None:
        return None

    # Assemble the opener request first (mode="opener": lean prompt, salience-
    # bearing view kept). The idempotent cache check below reads req.pack.salience,
    # so the pack must exist before it — this entry point (unlike the fuller/single-
    # shot paths) builds the pack ahead of the cache check by design.
    req = _assemble_generation_request(db, activity, mode="opener")

    existing = get_active_report_row(db, activity_uuid)
    if cached_row_is_servable(existing, kind=TurnKind.OPENER, force=False):
        # Idempotent re-entry: the opener (or a later fuller) is already written.
        # Do not re-LLM; recompute the schedule decision from the stored bit + the
        # deterministic safety override, and recover with a fuller turn if the
        # stored opener was a fallback.
        schedule = existing.is_fallback or \
            bool((existing.report or {}).get("schedule_fuller_turn")) or \
            req.pack.salience.safety_override.force_fuller
        return OpenerResult(
            report=_to_read(existing),
            schedule_fuller_turn=schedule,
            is_fallback=existing.is_fallback,
        )

    client = build_client(TurnKind.OPENER, activity.user_id)
    outcome = await _generate_message(
        client, req.system_prompt, req.user_message, req.pack,
        is_opener=True, user_id=activity.user_id, voice=req.voice,
    )

    read = _persist_report(
        db,
        activity=activity,
        req=req,
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
        req.pack.salience.safety_override.force_fuller
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
    if cached_row_is_servable(existing, kind=TurnKind.FULLER, force=force):
        # A complete fuller turn is already cached for this version.
        return _to_read(existing)
    # #273: a force regenerate does NOT delete the active row up front. The fuller
    # call runs first and _persist_report (existing=existing below) updates the SAME
    # row in place (generate-then-swap), so a worker death mid-regen leaves the prior
    # complete report intact instead of zero rows. Prior schema-versions are untouched
    # (the in-place update keeps the same cache identity).

    activity = _load_subject(db, activity_uuid)
    if activity is None:
        return None

    # Continuity: the opener prose this exchange already sent (preserved above,
    # so it survives a force-delete) + any chat reply since (the check-in already
    # rides the pack).
    continuity = ContinuityContext(
        opener_message=opener_prose,
        reply=fetch_latest_user_reply(db, activity_uuid),
    )

    # Assemble the fuller request (mode="fuller": deep prompt + playbook + fuller
    # view). Continuity (the opener prose + reply) rides the pack on this turn only.
    req = _assemble_generation_request(db, activity, mode="fuller", continuity=continuity)
    client = build_client(TurnKind.FULLER, activity.user_id)
    outcome = await _generate_message(
        client, req.system_prompt, req.user_message, req.pack,
        user_id=activity.user_id, voice=req.voice,
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
        req=req,
        outcome=outcome,
        existing=existing,  # in-place update of the opener row, or None -> insert
        fire_learning_loop=True,
    )


def _memory_as_of(db: Session, user_id) -> Optional[datetime]:
    """The runner-memory profile's as-of date, for the #646 regeneration stamp.

    Reads the (current, unversioned) runner memory row's `grounded_through` — the
    date of the most recent source the profile was rewritten from — falling back to
    when the row was last written. None when memory is disabled or no profile exists
    yet. This is provenance only (never grounds a fact); it makes a regenerated old
    report honest about the vantage point its memory speaks from.
    """
    if not settings.COACH_MEMORY_ENABLED:
        return None
    row = get_memory(db, user_id)
    if row is None:
        return None
    return row.grounded_through or row.updated_at


def _persist_report(
    db: Session,
    *,
    activity: Activity,
    req: "_GenerationRequest",
    outcome: "_GenOutcome",
    existing: Optional[CoachReport],
    fire_learning_loop: bool,
) -> Optional[CoachReportRead]:
    """Build the meta + digest, persist the report, and optionally fire the learning
    loop. Shared by the single-shot path, the opener, and the fuller turn so storage
    is identical.

    Takes the assembled `_GenerationRequest` rather than its six fields spread
    out (#801): all three call sites re-typed the same prompt_id / schema_version
    / pack / pack_dict / input_hash / voice list, which is six chances to pass one
    generation's pack with another's fingerprint. They come from one object, so
    they travel as one.

    Three persistence shapes:
    - INSERT (existing is None): a first generation.
    - in-place UPDATE (existing is an opener-only row, or a fallback replacing a
      fallback): the opener->fuller fill evolves ONE logical report on the same row,
      so it is not a supersede.
    - #646 SUPERSEDE (existing is a COMPLETE report and the new outcome is good): a
      force "Re-run" archives the prior current row (superseded_at = now, an immutable
      audit copy of what the coach saw) and INSERTS the regenerated report as the new
      current row, in ONE transaction (flush the archive first so the partial unique
      index has room). The original report + its point-in-time context_pack snapshot
      survive; the new row carries the regenerated-on + memory-as-of stamp.

    The digest is stored only for a COMPLETE non-fallback report — an opener-only
    row (is_opener_only) and any fallback carry no digest, so they never feed the
    M4/M7 prior-exchange reads. The learning loop fires only on a clean store of a
    non-fallback report and only when the caller asked for it (the opener never
    does). On an INSERT race the row that landed first wins and the loop is skipped.
    """
    activity_uuid = activity.id
    prompt_id = req.prompt_id
    schema_version = req.schema_version
    pack = req.pack
    pack_dict = req.pack_dict
    input_hash = req.input_hash
    voice = req.voice

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

    # #646: a force "Re-run" over a COMPLETE existing report is a non-destructive
    # supersede (archive + insert), not an in-place overwrite. An opener->fuller fill
    # (existing is opener-only) and a fallback outcome stay in-place: they evolve the
    # one logical report, not a regeneration. The #279 guard above already returned
    # for a fallback-over-good, so a supersede is always a good report replacing a
    # complete prior one.
    supersede = (
        existing is not None
        and not is_opener_only(existing.report)
        and not outcome.is_fallback
    )

    # The provenance stamp rides only a supersede (a true Re-run); a first generation
    # or an opener->fuller fill leaves both None, so the normal path's meta is
    # unchanged bar the two None-defaulted keys.
    regenerated_at = datetime.now(timezone.utc) if supersede else None
    memory_as_of = _memory_as_of(db, activity.user_id) if supersede else None

    meta = CoachReportMeta(
        confidence=pack.metrics.confidence,
        model_id=settings.COACH_MODEL_ID,
        prompt_id=prompt_id,
        schema_version=schema_version,
        input_hash=input_hash,
        generated_at=datetime.now(timezone.utc),
        policy_violations=outcome.policy_violations,
        tail_degraded=outcome.tail_degraded,
        regenerated_at=regenerated_at,
        memory_as_of=memory_as_of,
        # #822: name the character this report speaks in, stamped at generation so a
        # later voice change never relabels an existing report.
        voice_name=voice_display_name(voice) if voice is not None else None,
        voice_rewrite=outcome.voice_rewrite,
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

    if existing is not None and not supersede:
        # A4 in-place UPDATE: the fuller turn fills the opener's evolving row (or a
        # fallback replaces a fallback). The cache identity (activity_id, prompt_id,
        # schema_version) is unchanged — it is the same physical row — so the M0
        # versioned cache is preserved and no audit copy is minted (there is no prior
        # complete report to preserve; the opener is one half of this same report).
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
        if supersede:
            # #646: archive the prior current row as an immutable audit copy, then
            # insert the regenerated report as the new current row — one transaction,
            # so a crash leaves the prior report intact (#273) and the partial unique
            # index never sees two current rows for the key. Flush the archive first
            # so the UPDATE lands before the INSERT under that index.
            existing.superseded_at = regenerated_at
            db.add(existing)
            db.flush()
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
            # A concurrent request generated the active-version row first (and, on a
            # supersede, archived the same prior row). Roll back — which also reverts
            # our archive — and yield to the row that landed (do NOT fire the learning
            # loop; the winner's generation owns it).
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
