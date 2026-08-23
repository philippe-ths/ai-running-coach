"""The coach writes the period report (#946): a considered review of a
runner-chosen stretch of training over the disciplines the runner chose.

Generation shape follows `services/schedule/draft.py` deliberately — one forced
tool call (`generate_structured`, containment over free-form output), a bounded
rewrite loop with the failures fed back, and a separate bounded transport-retry
budget so a network blip never consumes the one chance to fix a genuinely bad
answer. What differs from a plan: this is prose, not a coercible structure the
coach cannot get wrong by construction, so the safety floor is enforced the same
way a conversational reply's is — `validator.validate_conversational_policy`
over the assembled text, not a new validator. A medical-overreach answer is
never shipped: it consumes a rewrite like any other rejection, and if the last
attempt still carries one the report FAILS rather than storing something
half-safe (ADR 0009's "the prose renders verbatim" reasoning applied here too).

An EMPTY period (#946 requirement: "must say so rather than generating a
report about nothing") never reaches the model at all: `PeriodReportPack.is_empty`
is checked first and answered with a deterministic message, so a runner who
picks a week they didn't train in costs nothing and cannot trip the safety
floor over nothing to say.

Rule 4 (`check_ungated_interval_claim`) and the pack's own shape (#946 review):
`PeriodReportPack.sessions` carries no per-rep interval detail at all — no
`interval_structure`, no `detection_confidence`, no `match_score` (see
`period_report_pack.py`: no stream data, no per-activity focus payload). That is
not an oversight to route around with an empty `sessions_in_play` list — an
empty list makes rule 4 a silent no-op, since `check_ungated_interval_claim`
returns immediately when handed nothing. It means the opposite: EVERY specific
interval-execution claim ("you hit all 8 x 400m") in a period report is
ungrounded by construction, so `_NO_INTERVAL_DETAIL_SENTINEL` below hands the
rule one forced-low-confidence marker rather than nothing, and any such claim in
the generated text is caught exactly the way it would be for a single low-
confidence session. The system prompt also tells the model not to make these
claims; this is the enforcement the North Star's second question exists for —
an instruction is not a guarantee.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.period_report import PeriodReport
from app.models.user import User
from app.schemas.period_report import PeriodReportContent
from app.services.coach import period_report_store as store
from app.services.coach import turn
from app.services.coach.period_report_pack import (
    PeriodReportPack,
    build_period_report_pack,
)
from app.services.coach.validator import validate_conversational_policy

logger = logging.getLogger(__name__)

# This surface's own identity, independent of `COACH_PROMPT_ID`: a period report
# is a different generative surface (like the schedule draft's own system
# prompt), not a mode of the per-activity report, so it does not ride that
# prompt's version lineage.
PROMPT_ID = "period_report_v1"
SCHEMA_VERSION = "1.0"

MAX_MESSAGE_CHARS = 6000
MAX_NEXT_STEPS = 6

# See the module docstring's "Rule 4" note. Not a real session's data — a
# constant, forced-low-confidence marker fed to `validate_conversational_policy`
# so rule 4 binds against the pack's own shape (no per-session interval detail
# exists here at all) instead of being silently skipped by an empty list.
_NO_INTERVAL_DETAIL_SENTINEL = [{"detection_confidence": "low", "match_score": 0.0}]

_SYSTEM_PROMPT = """You are a running coach writing a review of this runner's own chosen stretch of training — not a single session, a BLOCK. They picked the period and, optionally, which disciplines to look at; you are answering the question they actually asked: "how is this going".

# WHAT THIS IS NOT

This is not a longer write-up of one run. Do not walk through the sessions chronologically, day by day — that is a log, and the runner already has one. Write a REVIEW: what the stretch adds up to, what changed across it, what stands out, and what it means for what comes next. Reference specific sessions only when a specific one is the point (a session that broke a pattern, a standout effort, a session the numbers below flag).

# COACH THIS RUNNER, NOT THE MEDIAN

Their own history is the reference for what is normal for them, never a population default. What is a good week for one runner injures another. Read the totals and the per-session data below against what THIS runner has told you and what THIS runner's own baseline looks like, and say so when something is genuinely different from their norm — not different from a generic one.

# WHAT NOT TO DO

- Do not diagnose, name or assert a clinical condition, or give medication or dose advice. You may suggest seeing a clinician as a non-diagnostic nudge if something in the record looks concerning.
- Do not reference specific HR zones (Z1-Z5) unless the data below says this runner's zones are calibrated. Use effort language instead (easy, moderate, hard).
- Do not claim a precise interval-execution count ("you hit all 8 x 800m") for any session — you do not have per-rep detail here, only totals and summaries.
- Do not invent a race, an injury, or anything this runner has not told you.
- Do not narrate every session. Synthesize.

Write in prose, as you would say it to the runner. Keep `next_steps` short and few — the two or three things actually worth carrying forward, not a checklist.

Answer only by calling record_period_report."""

RECORD_PERIOD_REPORT_TOOL = {
    "name": "record_period_report",
    "description": (
        "Record your review of this stretch of training. This is the only way "
        "to return your answer."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["message"],
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "The review itself, in prose, written as you would say it to "
                    "the runner. Markdown is fine. This is the report."
                ),
            },
            "headline": {
                "type": "string",
                "description": "One short sentence capturing the stretch, for a list view.",
            },
            "next_steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "At most a few short, concrete things worth carrying forward.",
            },
        },
    },
}


def normalise(raw: dict) -> dict:
    """Trim what is cosmetic before strict coercion — the `draft_contract.normalise`
    idiom. Only truncation; nothing structural is repaired."""
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    message = out.get("message")
    if isinstance(message, str) and len(message) > MAX_MESSAGE_CHARS:
        out["message"] = message[:MAX_MESSAGE_CHARS].rstrip()
    steps = out.get("next_steps")
    if isinstance(steps, list) and len(steps) > MAX_NEXT_STEPS:
        out["next_steps"] = steps[:MAX_NEXT_STEPS]
    return out


@dataclass
class PeriodReportOutcome:
    ok: bool
    failures: Optional[List[str]] = None
    failure_kind: str = store.FAILURE_UNKNOWN
    # True when a real generation finished but `store.mark_ready`/`mark_failed`
    # found the row already settled by another writer (the stale-retry race,
    # #946 review) and discarded the result rather than storing it. `ok` stays
    # False here too: this generation attempt's own result was never saved,
    # whatever the row it was racing against ended up as.
    discarded: bool = False


def _empty_period_content(pack: PeriodReportPack) -> PeriodReportContent:
    disciplines = ", ".join(pack.disciplines) if pack.disciplines else "any activity"
    return PeriodReportContent(
        message=(
            f"Nothing logged between {pack.period_start.isoformat()} and "
            f"{pack.period_end.isoformat()} for {disciplines}. There is no "
            f"training in this stretch to review."
        ),
        headline="Nothing logged this period",
        next_steps=[],
    )


def _profile_lines(profile: dict) -> List[str]:
    if not profile:
        return ["Nothing stated yet."]
    lines = []
    for label, key in (
        ("Goal", "goal_type"),
        ("Experience", "experience_level"),
        ("Days available each week", "weekly_days_available"),
        ("Current weekly km (stated)", "current_weekly_km"),
        ("Max HR", "max_hr"),
        ("Injury notes", "injury_notes"),
    ):
        value = profile.get(key)
        if value not in (None, "", []):
            lines.append(f"- {label}: {value}")
    body = profile.get("body") or {}
    if body.get("weight_kg") is not None:
        lines.append(f"- Weight (kg): {body['weight_kg']}")
    if body.get("height_cm") is not None:
        lines.append(f"- Height (cm): {body['height_cm']}")
    return lines or ["Nothing stated yet."]


def build_prompt_context(pack: PeriodReportPack) -> str:
    """What the coach reads to write this review. Text, not the raw pack JSON —
    the same "coach-framed prose beats a raw dump" choice `draft.build_draft_context`
    makes, for the same reason: the model reasons better over units it already
    speaks in (km, not metres; a labelled total, not a bare column name)."""
    import json

    parts: List[str] = []
    disciplines_label = ", ".join(pack.disciplines) if pack.disciplines else "every discipline"
    parts.append(
        f"PERIOD: {pack.period_start.isoformat()} to {pack.period_end.isoformat()} "
        f"(inclusive), disciplines: {disciplines_label}."
    )

    parts.append("\n## THE RUNNER")
    parts.extend(_profile_lines(pack.profile))

    if pack.goal_race:
        race = pack.goal_race
        parts.append("\n## THEIR RACE")
        parts.append(
            f"- {race['name']}: {race['race_date']} "
            f"({race['distance_m'] / 1000:.1f} km, priority {race['priority']})"
        )

    if pack.memory:
        parts.append("\nMEMORY — what this runner has told you (their memory profile):")
        parts.append(json.dumps(pack.memory, default=str))

    if pack.readiness:
        parts.append("\nCURRENT CONDITION (deterministic readiness read, as of today):")
        parts.append(json.dumps(pack.readiness, default=str))

    if pack.running_norm:
        parts.append("\nTHEIR TYPICAL RUNNING WEEK (their own recent weeks, for scale):")
        parts.append(json.dumps(pack.running_norm, default=str))

    if pack.schedule:
        parts.append("\nTHEIR ACTIVE PLAN (their Schedule screen, for context on what was intended):")
        parts.append(json.dumps(pack.schedule, default=str))

    parts.append("\n## TOTALS FOR THIS PERIOD")
    parts.append(json.dumps(pack.totals, default=str))
    if pack.by_type:
        parts.append("\n## BY DISCIPLINE")
        parts.append(json.dumps(pack.by_type, default=str))

    parts.append(
        f"\n## SESSIONS IN THIS PERIOD ({len(pack.sessions)} shown, chronological)"
    )
    parts.append(
        json.dumps([s.model_dump(mode="json") for s in pack.sessions], default=str)
    )

    return "\n".join(parts)


def _mark_ready_outcome(db, report, **kwargs) -> PeriodReportOutcome:
    """`store.mark_ready` wrapped to translate a discarded (compare-and-set
    lost) result into the outcome the job layer logs distinctly from an actual
    generation failure — see `PeriodReportOutcome.discarded`."""
    settled = store.mark_ready(db, report, **kwargs)
    if settled is None:
        return PeriodReportOutcome(ok=False, discarded=True)
    return PeriodReportOutcome(ok=True)


def _mark_failed_outcome(db, report, reason: str, *, kind: str) -> PeriodReportOutcome:
    settled = store.mark_failed(db, report, reason, kind=kind)
    if settled is None:
        return PeriodReportOutcome(ok=False, failure_kind=kind, discarded=True)
    return PeriodReportOutcome(ok=False, failure_kind=kind)


async def generate_period_report(
    db: Session, user: User, report: PeriodReport
) -> PeriodReportOutcome:
    """Generate, validate and store one period report. Mirrors
    `services/schedule/draft.draft_plan`'s shape; see module docstring for the
    one deliberate difference (a policy violation fails rather than degrading)."""
    pack = build_period_report_pack(
        db,
        user,
        period_start=report.period_start,
        period_end=report.period_end,
        disciplines=report.disciplines or [],
    )

    if pack.is_empty:
        content = _empty_period_content(pack)
        return _mark_ready_outcome(
            db,
            report,
            content=content.model_dump(mode="json"),
            context_pack=pack.model_dump(mode="json"),
            model_id=None,
            meta={"empty_period": True},
        )

    if turn.over_budget(user.id):
        return _mark_failed_outcome(
            db, report, "over the spend cap for this period", kind=store.FAILURE_OVER_BUDGET
        )

    client = turn.build_client(turn.TurnKind.PERIOD, user.id)
    context = build_prompt_context(pack)

    failures: List[str] = []
    failure_kind = store.FAILURE_UNKNOWN
    rewrites_left = 2
    transport_retries_left = 1

    while rewrites_left > 0:
        user_message = context
        if failures:
            user_message = (
                f"{context}\n\n## YOUR PREVIOUS ATTEMPT WAS REJECTED\n"
                + "\n".join(f"- {failure}" for failure in failures)
                + "\n\nWrite it again, fixing every one of these."
            )

        try:
            raw = await client.generate_structured(
                system=_SYSTEM_PROMPT,
                user=user_message,
                tool=RECORD_PERIOD_REPORT_TOOL,
                max_tokens=4096,
            )
        except Exception as exc:  # noqa: BLE001 — transport, timeout, refusal
            logger.warning("period report: generation call failed: %s", exc)
            if transport_retries_left > 0:
                transport_retries_left -= 1
                continue
            return _mark_failed_outcome(
                db, report, "the coach could not be reached", kind=store.FAILURE_UNREACHABLE
            )

        rewrites_left -= 1

        try:
            content = PeriodReportContent.model_validate(normalise(raw))
        except Exception as exc:
            logger.warning("period report: off-contract answer: %s", exc)
            failures = [f"the answer was not the shape the tool requires: {exc}"]
            failure_kind = store.FAILURE_UNKNOWN
            continue

        full_text = "\n".join(
            [content.message, content.headline or "", *content.next_steps]
        )
        # `_NO_INTERVAL_DETAIL_SENTINEL`, not `[]` — see module docstring's
        # "Rule 4" note (#946 review). An empty list would silently skip rule 4
        # for every period report; this forces it to bind against the pack's
        # own shape instead.
        violations = validate_conversational_policy(
            full_text,
            zones_calibrated=pack.zones_calibrated,
            sessions_in_play=_NO_INTERVAL_DETAIL_SENTINEL,
        )
        if violations:
            logger.info(
                "period report: policy violation(s): %s",
                [v.rule for v in violations],
            )
            failures = [v.fix_instruction for v in violations]
            failure_kind = store.FAILURE_POLICY
            continue

        return _mark_ready_outcome(
            db,
            report,
            content=content.model_dump(mode="json"),
            context_pack=pack.model_dump(mode="json"),
            model_id=client.model,
            meta={"empty_period": False},
        )

    # Exhausted rewrites. A policy violation is never shipped, however close the
    # rest of the answer was — the same "half-safe is not a real option" rule
    # ADR 0009 states for the per-activity report's forced fallback.
    outcome = _mark_failed_outcome(
        db, report, "; ".join(failures) or "unknown failure", kind=failure_kind
    )
    return PeriodReportOutcome(
        ok=False, failures=failures, failure_kind=failure_kind, discarded=outcome.discarded
    )
