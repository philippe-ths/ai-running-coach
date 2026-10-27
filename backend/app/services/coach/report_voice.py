"""
The runner's declared voice and stance for an activity, and re-saying a stored
report in a changed voice without regenerating it (#1142). Split from `service.py`
(#1159); `service` re-exports every name here.
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity
from app.models.coach_report import CoachReport
from app.models.coaching_relationship import CoachingRelationship
from app.schemas.coach import CoachMessageReport, CoachReportMeta
from app.schemas.coach_context import load_stored_pack
from app.services.coach.prompt_features import PromptFeature, has_feature
from app.services.coach.receipt_voice import voice_fingerprint
from app.services.coach.report_generation import (
    MEDICAL_OVERREACH_RULE,
    _MsgAttempt,
    _apply_voice,
)
from app.services.coach.report_reads import (
    _coerce_uuid,
    active_schema_version,
    get_active_report_row,
)
from app.services.coach.stance import resolve_stance
from app.services.coach.turn import relationship_for_user
from app.services.coach.validator import validate_message_policy
from app.services.coach.voice import display_name as voice_display_name, resolve_voice

logger = logging.getLogger(__name__)

def _relationship_for_activity(
    db: Session, activity: Activity
) -> Optional[CoachingRelationship]:
    """This activity owner's thin CoachingRelationship row, or None.

    Delegates to the envelope's single gated read (#801): the
    COACH_RELATIONSHIP_ENABLED check used to live here AND be missing from the
    thread turn's own read, which is #791. Kept as a named function because it is
    the activity-shaped call the resolvers below read best with."""
    return relationship_for_user(db, activity.user_id)


def _resolve_voice_for_activity(db: Session, activity: Activity):
    """Resolve the runner's declared coach voice for this activity's owner (P1.1).

    Resolves the thin CoachingRelationship row to a VoiceProfile; a missing row, an
    undeclared voice, or a disabled relationship input resolves to the moderate
    default. This only affects voice-aware prompts (coach_message_v3) —
    render_voice_block is a no-op for every other prompt id, so the resolved voice is
    harmless under any other prompt."""
    return resolve_voice(_relationship_for_activity(db, activity))


def report_voice_stale(db: Session, row: Optional[CoachReport]) -> bool:
    """True when `row` is the ACTIVE-version report under a voice-aware prompt but was
    generated under a DIFFERENT voice than the runner's current one — so it should be
    re-voiced to honour the new voice (the read endpoint flags it and the frontend
    auto-triggers the async revoice job on a stale view, which re-says the stored
    voiceless baseline and regenerates only a row that has none, #1142).

    False for: a non-voice-aware active prompt (voice is inert), a cross-version
    displayable fallback (#261 — left alone so a COACH_PROMPT_ID flip never storms
    regens), or a row already on the current voice. A NULL voice_key on an active
    voice-aware row reads as STALE: its voice is unknown (the row predates the
    voice_key column or a non-voice prompt), so it is brought ONCE onto the current
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


def _voiceless_baseline(row: CoachReport) -> Optional[CoachMessageReport]:
    """The stored report as a voiceless baseline that can be said again in another
    voice, or None when the row holds no such thing (#1142).

    Only a row written by the voiceless-then-rewrite pipeline (ADR 0030) qualifies,
    and its `meta.voice_rewrite` is the proof: that path stamps it on every complete
    prose report, while a pre-#822 row (whose prose was STEERED by its voice at
    generation, so is not voiceless) and a fallback carry none. A fallback is also
    excluded on its own flag: there is no coaching in it to re-voice, and the runner
    is better served by the regeneration that may produce some."""
    if row.is_fallback or not (row.meta or {}).get("voice_rewrite"):
        return None
    # No structured (schema 1.x) prompt is voice-aware, so a voice-stale row is
    # always prose; a dict that is not one simply fails to validate here.
    try:
        report = CoachMessageReport.model_validate(row.report or {})
    except ValidationError:
        return None
    if not (report.message.strip() or (report.opener_message or "").strip()):
        return None
    return report


class RevoiceOutcome(str, Enum):
    """What `revoice_stored_report` did (#1142), so its caller knows whether the
    full regeneration path is needed.

    - REVOICED: the row now speaks in the runner's current voice (or, after a
      definitive rewrite rejection, serves its baseline and is stamped current).
    - NOT_STALE: nothing to do: no active row, or it is already on the current voice
      (switched back before the job ran; another device's revoice landed first).
    - DEFERRED: the rewrite could not run for a transient reason (over budget, a
      transport error) or the row changed underneath it; the row is untouched, keeps
      its old voiced text and stays stale, so a later view retries.
    - NEEDS_REGENERATE: stale, but there is no safe voiceless baseline to re-say;
      only a regeneration can bring this report onto the current voice.
    """

    REVOICED = "revoiced"
    NOT_STALE = "not_stale"
    DEFERRED = "deferred"
    NEEDS_REGENERATE = "needs_regenerate"


# Rewrite outcomes that say nothing about the report and may well succeed later:
# the budget refills and the transport recovers. Every OTHER reason is definitive:
# the operator kill switch and a Default voice mean the baseline IS the right text
# (ADR 0030 points 5-6), and empty/unparsed/dropped/invented/policy are the model's
# answer refused on its content, which a retry on every view would only re-buy.
_TRANSIENT_REWRITE_REASONS = ("over_budget", "transport_error")


def _rewrite_was_transient(reason: Optional[str]) -> bool:
    return bool(reason) and reason.split(" ", 1)[0] in _TRANSIENT_REWRITE_REASONS


async def revoice_stored_report(db: Session, activity_id: str) -> RevoiceOutcome:
    """Re-say a voice-stale report in the runner's current voice, without
    regenerating it (#1142).

    The baseline is settled substance (ADR 0030), so a voice change needs only the
    rewrite: no report generation, no memory pass, no re-analysis. Each stage the row
    carries is re-voiced through the same `_apply_voice` a fresh generation uses,
    judged against the same policy floor. A baseline that trips the medical floor
    under TODAY's rules is never re-voiced: a rewrite judged only on rules its
    baseline did not already break would be exempt from the medical check, so that
    report goes to the regeneration path, whose policy retry and fallback own it.

    A definitive rewrite rejection serves the baseline and stamps the current voice,
    exactly as generation does, so it cannot loop. A transient one (over budget,
    transport error) leaves the row exactly as it was: the runner keeps the old
    voice's text rather than losing it to a blip, and the next view retries.
    """
    activity_uuid = _coerce_uuid(activity_id)
    row = get_active_report_row(db, activity_uuid)
    if row is None or not report_voice_stale(db, row):
        return RevoiceOutcome.NOT_STALE
    report = _voiceless_baseline(row)
    if report is None:
        return RevoiceOutcome.NEEDS_REGENERATE
    try:
        pack = load_stored_pack(row.context_pack, prompt_id=row.prompt_id)
    except Exception:  # noqa: BLE001 — unreadable pack: regenerate rather than guess
        logger.warning("revoice skipped: stored pack unreadable (activity=%s)", activity_uuid)
        return RevoiceOutcome.NEEDS_REGENERATE
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if activity is None:
        return RevoiceOutcome.NOT_STALE
    voice = _resolve_voice_for_activity(db, activity)
    row_id = row.id
    stored_report = dict(row.report)

    report = report.model_copy(update={"voiced_message": None, "voiced_opener_message": None})
    baseline_violations = validate_message_policy(report, pack)
    if any(v.rule == MEDICAL_OVERREACH_RULE for v in baseline_violations):
        logger.warning(
            "revoice refused: baseline trips the medical floor under current rules "
            "(activity=%s); regenerating instead",
            activity_uuid,
        )
        return RevoiceOutcome.NEEDS_REGENERATE
    chosen = _MsgAttempt(
        report=report,
        raw_response=row.raw_llm_response or "",
        truncated=False,
        violations=baseline_violations,
    )
    voice_rewrite: Optional[str] = None
    for is_opener, text in (
        (True, report.opener_message or ""),
        (False, report.message),
    ):
        if not text.strip():
            continue
        chosen, voice_rewrite = await _apply_voice(
            chosen, pack, voice=voice, user_id=activity.user_id, is_opener=is_opener
        )
        if _rewrite_was_transient(voice_rewrite):
            logger.info(
                "revoice deferred (%s): row left on its old voice (activity=%s)",
                voice_rewrite, activity_uuid,
            )
            return RevoiceOutcome.DEFERRED

    # The rewrite took seconds; a Re-run may have superseded or refilled this row in
    # the meantime. Write only onto the row exactly as it was read.
    db.expire_all()
    row = db.query(CoachReport).filter(CoachReport.id == row_id).first()
    if row is None or row.superseded_at is not None or row.report != stored_report:
        logger.info("revoice discarded: report changed underneath (activity=%s)", activity_uuid)
        return RevoiceOutcome.DEFERRED

    meta = CoachReportMeta.model_validate(row.meta).model_copy(update={
        "voice_name": voice_display_name(voice),
        "voice_rewrite": voice_rewrite,
        "revoiced_at": datetime.now(timezone.utc),
    })
    row.report = {**stored_report, **{
        "voiced_message": chosen.report.voiced_message,
        "voiced_opener_message": chosen.report.voiced_opener_message,
    }}
    row.meta = meta.model_dump(mode="json")
    row.voice_key = voice_fingerprint(voice)
    db.add(row)
    db.commit()
    return RevoiceOutcome.REVOICED


def _resolve_stance_for_activity(db: Session, activity: Activity):
    """Resolve the runner's declared coaching stance for this activity's owner (P1.3).

    Resolves the thin CoachingRelationship row to a StanceProfile (selected school +
    the two emphasis axes); a missing row, an undeclared stance, or a disabled
    relationship input resolves to the default school (aerobic-base) + balanced
    emphasis. This only takes effect under a stance-aware prompt (coach_message_v5):
    build_context_pack threads the school into the corpus section and the emphasis
    into the stance section only under v5, so the resolved stance is harmless under
    any other prompt id (the P1.2/voice precedent)."""
    return resolve_stance(_relationship_for_activity(db, activity))
