"""Semantic-judge layer for the offline coach-report eval gate (#164).

The deterministic rubric (``rubric.py``) is a fast keyword/overlap floor with
documented blind spots: it cannot see semantic paraphrase (a report that restates
the prior report in fresh words), a grounded claim phrased without the expected
keyword, or whether per-runner preference FRAMING actually improved. ADR 0021 /
#266 noted the same gap from the other side — "feels samey / less human" is a
B-vs-A judgment the deterministic eval is blind to by design.

This module adds an LLM-as-judge layer that reads a stored report's prose plus the
context pack it was grounded on and returns a small STRUCTURED verdict (per-criterion
1-5 score + a short reason) via a forced-tool structured-output call. It mirrors the
material distiller's containment-grade structured-output pattern (a forced
``tool_choice`` with no free-form channel, then strict Pydantic coercion).

IMPORTANT — this is a quality SIGNAL, never a safety gate. The deterministic policy
validator (``services/coach/validator.py``) remains the only safety floor; the
deterministic rubric remains the pass/fail gate. The judge is advisory: it is
opt-in (``--semantic-judge`` on the eval CLI), OFF by default, never runs during
``make backend-test`` or ``make eval-selftest``, never gates report generation, and
never alters the deterministic rubric's result. Its scores are aggregated into a
separate, clearly-labelled section of the scorecard.

Reproducibility: the call runs at ``temperature=0`` (the structured-output path), so
verdicts are about as reproducible as an LLM call gets — "reproducible enough to gate
on (or clearly advisory if not)" per the issue's acceptance criterion. We treat them
as advisory by default; the CLI's ``--compare`` path can flag a drop in a criterion
mean across runs, which the owner reads as a signal, not an automatic failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.coach import CoachMessageReport, CoachReportContent
from app.schemas.coach_context import CoachContextPack

ReportLike = CoachReportContent | CoachMessageReport


# The criteria are deliberately FEW and CONCRETE. Each maps to a blind spot the
# deterministic rubric cannot see (the issue's three asks + ADR 0021's "samey /
# less human"). Order is the source of truth for column order in the scorecard.
JUDGE_CRITERIA: tuple[str, ...] = (
    "human_voice",
    "coherence",
    "non_samey",
    "grounded",
    "preference_framing",
)

# Human-readable one-liners, surfaced in the tool schema and the printed scorecard.
CRITERION_DESCRIPTIONS: Dict[str, str] = {
    "human_voice": (
        "Reads like a human coach with a point of view, not a template-stamped report "
        "generator (ADR 0021). 5 = warm, specific, human; 1 = robotic / form-letter."
    ),
    "coherence": (
        "The report hangs together: a clear through-line, no internal contradiction, "
        "the numbers serve the story. 5 = coherent; 1 = disjointed or self-contradictory."
    ),
    "non_samey": (
        "Does NOT semantically restate the prior report (in `longitudinal.prior_reports`) "
        "even when reworded, and does not read as an interchangeable template. 5 = advances "
        "the relationship with a fresh, specific read; 1 = a paraphrased restatement."
    ),
    "grounded": (
        "Every trend/condition claim is supported by the context pack, regardless of exact "
        "phrasing, and nothing is overclaimed beyond what the data shows. 5 = fully grounded, "
        "no overreach; 1 = invents or overstates claims the pack does not support."
    ),
    "preference_framing": (
        "Leads with advice in themes the runner ACTS ON and reframes/soft-pedals themes they "
        "IGNORE (`preference_profile`). 5 = well framed for this runner; 1 = leads with ignored "
        "advice. Score 3 (neutral) and say so if no decisive preference profile is present."
    ),
}


class CriterionScore(BaseModel):
    """One criterion's verdict: a 1-5 integer score and a short justifying reason."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    reason: str


class JudgeVerdict(BaseModel):
    """The full structured verdict: one CriterionScore per criterion plus a short
    overall note. ``extra="forbid"`` so a rogue key fails coercion (mirrors the
    distiller's strict-coercion containment), and the 1-5 bounds are enforced here
    (the strict tool-schema subset does not reliably enforce integer ranges)."""

    model_config = ConfigDict(extra="forbid")

    human_voice: CriterionScore
    coherence: CriterionScore
    non_samey: CriterionScore
    grounded: CriterionScore
    preference_framing: CriterionScore
    overall_note: str = ""

    def scores(self) -> Dict[str, int]:
        return {name: getattr(self, name).score for name in JUDGE_CRITERIA}


# Hand-frozen strict tool schema (the RECORD_DISTILLED_MATERIAL_TOOL precedent):
# additionalProperties:false + required on every object. The 1-5 bound lives in the
# Pydantic layer (`CriterionScore`), the strict subset only fixes the SHAPE.
def _criterion_property(name: str) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["score", "reason"],
        "properties": {
            "score": {
                "type": "integer",
                "description": f"{CRITERION_DESCRIPTIONS[name]} An integer from 1 (worst) to 5 (best).",
            },
            "reason": {
                "type": "string",
                "description": "One short sentence justifying the score with a concrete observation.",
            },
        },
    }


RECORD_JUDGE_VERDICT_TOOL: Dict[str, Any] = {
    "name": "record_judge_verdict",
    "description": (
        "Record your semantic-quality verdict on this coach report as a structured "
        "record. Score each criterion from 1 (worst) to 5 (best) and give a one-sentence "
        "reason. Judge ONLY against the supplied report and its context pack; invent "
        "nothing. This is the only way to return your answer."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": list(JUDGE_CRITERIA) + ["overall_note"],
        "properties": {
            **{name: _criterion_property(name) for name in JUDGE_CRITERIA},
            "overall_note": {
                "type": "string",
                "description": "One short sentence summarising the overall quality read.",
            },
        },
    },
}


_SYSTEM_PROMPT = """You are a strict, fair evaluator of an AI running coach's written \
reports. You assess QUALITIES a keyword checker cannot see: whether the writing reads \
human, whether it hangs together, whether it merely restates the prior report in fresh \
words, whether its claims are grounded in the supplied data, and whether it frames \
advice for THIS runner's known tendencies.

You will be given one coach report and the exact context pack it was generated from. \
Score each criterion from 1 (worst) to 5 (best) and give a one-sentence reason citing a \
concrete observation from the report or pack.

ABSOLUTE RULES:
- Judge ONLY what is in front of you. Do not invent facts about the runner or assume \
data the pack does not contain.
- The context pack is the ground truth for what the coach could legitimately say. A \
claim the pack supports is grounded even if phrased without the obvious keyword; a claim \
the pack does NOT support is overreach even if it sounds plausible.
- For non_samey, compare the report against `longitudinal.prior_reports`: a semantic \
restatement (same point, new words) scores low even if no phrase is repeated verbatim.
- For preference_framing, read `preference_profile`: reward leading with themes the \
runner acts on and reframing themes they ignore. If no decisive profile is present, \
score 3 and say so.
- You are NOT a safety reviewer. Do not police medical scope or refuse — a separate \
deterministic validator owns that floor. Score quality only.
- Return your answer ONLY by calling record_judge_verdict exactly once. Produce no \
other output."""


# Token headroom: five short reasons + a note. Bounded but generous.
_MAX_TOKENS = 1200


class StructuredClient(Protocol):
    """The narrow slice of AnthropicClient the judge needs — a forced-tool structured
    call. Lets tests inject a fake client with no API key (mirrors the distiller's
    ``client`` injection seam)."""

    async def generate_structured(
        self, *, system: str, user: str, tool: Dict[str, Any], max_tokens: int = ...
    ) -> Dict[str, Any]: ...


def _render_report(content: ReportLike) -> Dict[str, Any]:
    """The report as the judge should read it. ``model_dump`` is faithful and shape-
    agnostic (it carries the prose ``message`` or the structured fields, plus headline,
    next_steps, etc.), so the judge sees exactly the stored report."""
    return content.model_dump(mode="json")


def render_judge_messages(content: ReportLike, pack: CoachContextPack) -> tuple[str, str]:
    """Render the (system, user) messages for the judge call. Pure.

    The system prompt is fixed; the report and its pack ride the user message as
    clearly-fenced JSON data."""
    report_json = json.dumps(_render_report(content), indent=2, sort_keys=True, default=str)
    pack_json = json.dumps(pack.model_dump(mode="json"), indent=2, sort_keys=True, default=str)
    user = (
        "Evaluate the following coach report against the context pack it was generated "
        "from. Score every criterion and call record_judge_verdict once.\n\n"
        "----- BEGIN COACH REPORT -----\n"
        f"{report_json}\n"
        "----- END COACH REPORT -----\n\n"
        "----- BEGIN CONTEXT PACK (ground truth) -----\n"
        f"{pack_json}\n"
        "----- END CONTEXT PACK -----"
    )
    return _SYSTEM_PROMPT, user


async def judge_report(
    client: StructuredClient, content: ReportLike, pack: CoachContextPack
) -> JudgeVerdict:
    """Run one structured judge call and coerce the result through the strict
    ``JudgeVerdict`` schema. Raises ``ValidationError``/``ValueError`` on a malformed
    verdict (out-of-range score, missing/extra key, no tool block) — the caller
    (``judge_db_reports``) catches it per-report so one bad verdict never crashes the
    run, exactly as the deterministic harness treats an unparseable report."""
    system, user = render_judge_messages(content, pack)
    raw = await client.generate_structured(
        system=system, user=user, tool=RECORD_JUDGE_VERDICT_TOOL, max_tokens=_MAX_TOKENS
    )
    return JudgeVerdict.model_validate(raw)


@dataclass
class JudgeReportScore:
    """One report's judge verdict plus its identity, mirroring ``ReportScore`` so the
    judge section of the scorecard is scoped to ``(prompt_id, schema_version)`` like
    the deterministic results."""

    verdict: JudgeVerdict
    report_id: Optional[str] = None
    activity_id: Optional[str] = None
    prompt_id: Optional[str] = None
    schema_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "activity_id": self.activity_id,
            "prompt_id": self.prompt_id,
            "schema_version": self.schema_version,
            "scores": self.verdict.scores(),
            "reasons": {
                name: getattr(self.verdict, name).reason for name in JUDGE_CRITERIA
            },
            "overall_note": self.verdict.overall_note,
        }


def summarize_judge_scores(scores: List[JudgeReportScore]) -> Dict[str, Any]:
    """Aggregate per-criterion mean/min/count over the judged reports. Empty input
    yields an empty summary (the judge layer was off or judged nothing)."""
    summary: Dict[str, Any] = {}
    for name in JUDGE_CRITERIA:
        values = [getattr(s.verdict, name).score for s in scores]
        if values:
            summary[name] = {
                "count": len(values),
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
            }
        else:
            summary[name] = {"count": 0, "mean": None, "min": None, "max": None}
    return summary
