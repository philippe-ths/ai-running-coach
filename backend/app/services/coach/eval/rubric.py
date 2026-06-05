"""The coach-report eval rubric (M5) — the oracle for coach reports.

Five deterministic assertions, authored from the M5 success criteria, each
returning PASS / FAIL / NOT_APPLICABLE plus a human-readable reason and small
JSON-serialisable evidence. A report is scored from its own ``CoachReportContent``
plus its ``CoachContextPack`` only, so scoring is self-contained and repeatable.

The assertions:
  1. led_with_headline      — the report opens with a headline verdict.
  2. discounted_inflated_hr  — when a confound fired, the report discounts the HR
                               drift rather than reading it as genuine fatigue.
  3. no_medical_overreach    — reuses the production policy gate (rule 5) verbatim.
  4. advanced_not_parroted   — when a prior report exists, this one advances the
                               narrative instead of restating it.
  5. abstained_on_thin_trend — no like-for-like trend claim when the matching
                               RunnerBaseline bucket is still abstaining.

Assertions 2, 4 and 5 inspect free text with documented keyword / overlap
heuristics; they are the deterministic floor, not a semantic judge. The
NOT_APPLICABLE branch matters: a report is never failed for a dimension that
does not apply to it (no confound fired, no prior report to advance).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from app.schemas.coach import CoachReportContent
from app.schemas.coach_context import CoachContextPack
from app.services.coach.validator import _extract_all_text, validate_policy


class AssertionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class AssertionResult:
    name: str
    status: AssertionStatus
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class ReportScore:
    assertions: List[AssertionResult]
    report_id: Optional[str] = None
    activity_id: Optional[str] = None
    prompt_id: Optional[str] = None
    schema_version: Optional[str] = None

    @property
    def applicable_count(self) -> int:
        return sum(1 for a in self.assertions if a.status is not AssertionStatus.NOT_APPLICABLE)

    @property
    def passed_count(self) -> int:
        return sum(1 for a in self.assertions if a.status is AssertionStatus.PASS)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.assertions if a.status is AssertionStatus.FAIL)

    @property
    def pass_rate(self) -> float:
        """Passed over applicable. Vacuously 1.0 when nothing applies."""
        applicable = self.applicable_count
        if applicable == 0:
            return 1.0
        return self.passed_count / applicable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "activity_id": self.activity_id,
            "prompt_id": self.prompt_id,
            "schema_version": self.schema_version,
            "applicable_count": self.applicable_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "pass_rate": self.pass_rate,
            "assertions": [a.to_dict() for a in self.assertions],
        }


# --- shared text helpers ------------------------------------------------------

def _report_text(content: CoachReportContent) -> str:
    """All scoreable report text, lower-cased. Reuses the production validator's
    extractor so the eval sees exactly the surface the policy gate polices."""
    return _extract_all_text(content).lower()


def _assertive_text(content: CoachReportContent) -> str:
    """The report's ASSERTED claims only, lower-cased: headline, thesis, lead
    argument, takeaways, next steps and risks — but NOT questions. A question the
    coach asks ("how did this feel compared to your last session?") is not an
    assertion, so trend-claim detection must not fire on it."""
    parts: List[str] = []
    if content.headline:
        parts.append(content.headline)
    if content.thesis:
        parts.append(content.thesis)
    if content.lead_argument is not None:
        parts.append(content.lead_argument.text)
    for t in content.key_takeaways:
        parts.append(t.text if hasattr(t, "text") else str(t))
    for s in content.next_steps:
        parts.extend([s.action, s.details, s.why])
    for r in content.risks:
        parts.extend([r.explanation, r.mitigation])
    return " ".join(parts).lower()


_WORD_RE = re.compile(r"[a-z0-9]+")
# Common words that inflate overlap without signalling repetition of substance.
_STOPWORDS = frozenset(
    "a an the and or but to of in on for with your you it is was were be been are "
    "this that these those at as by from so then than into over after before".split()
)


def _content_words(text: str) -> set:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# --- assertion 1: led with a headline ----------------------------------------

def assert_led_with_headline(content: CoachReportContent, pack: CoachContextPack) -> AssertionResult:
    headline = (content.headline or "").strip()
    if headline:
        return AssertionResult(
            "led_with_headline", AssertionStatus.PASS,
            "Report opens with a headline verdict.",
            {"headline": headline},
        )
    return AssertionResult(
        "led_with_headline", AssertionStatus.FAIL,
        "Report has no headline; the grounded reshape (N3) requires a lead verdict.",
        {"headline": content.headline},
    )


# --- assertion 2: discounted an inflated HR when a confound fired -------------

# "warm" is excluded deliberately: it matches "warm-up", an unrelated phrase.
_CONFOUND_TERMS = {
    "heat": ["heat", "hot", "temperature", "weather", "°c", "degrees", "humid"],
    "terrain": ["hill", "terrain", "elevation", "climb", "gradient", "uphill", "ascent"],
    "stimulant": ["stimulant", "caffeine", "pre-workout", "pre workout"],
}
_DISCOUNT_ACK_TERMS = [
    "discount", "inflat", "overstate", "artefact", "artifact", "skew",
    "exaggerat", "cautiously", "with caution", "not fatigue", "not genuine fatigue",
    "rather than fatigue", "not real fatigue",
]


def assert_discounted_inflated_hr(content: CoachReportContent, pack: CoachContextPack) -> AssertionResult:
    signal = pack.metrics.discount_signals
    inflators = list(signal.get("likely_inflated_by") or []) if isinstance(signal, dict) else []
    if not inflators:
        # No concrete confound fired (no signal, or temperature-unknown caution
        # case): nothing the report is obliged to discount here.
        return AssertionResult(
            "discounted_inflated_hr", AssertionStatus.NOT_APPLICABLE,
            "No concrete discount signal fired for this activity.",
            {"likely_inflated_by": inflators},
        )

    text = _report_text(content)
    mentioned = sorted({
        inflator for inflator in inflators
        if any(term in text for term in _CONFOUND_TERMS.get(inflator, [inflator]))
    })
    acknowledged = any(term in text for term in _DISCOUNT_ACK_TERMS)
    # Require BOTH: naming a fired confound AND discount language. Naming heat
    # while still reading the drift as fatigue ("despite the heat, the drift
    # shows fatigue") is the exact failure this assertion exists to catch, so a
    # bare confound mention is not enough. (Deterministic floor: it cannot verify
    # the discount is logically correct, only that both signals co-occur.)
    if mentioned and acknowledged:
        return AssertionResult(
            "discounted_inflated_hr", AssertionStatus.PASS,
            "Report names the fired confound and uses discount language.",
            {"likely_inflated_by": inflators, "confounds_mentioned": mentioned, "discount_language": acknowledged},
        )
    missing = "discount language" if mentioned else ("the confound" if acknowledged else "both the confound and discount language")
    return AssertionResult(
        "discounted_inflated_hr", AssertionStatus.FAIL,
        f"A confound fired ({', '.join(inflators)}) but the report is missing {missing}; "
        "it risks reading the HR drift as genuine fatigue.",
        {"likely_inflated_by": inflators, "confounds_mentioned": mentioned, "discount_language": acknowledged},
    )


# --- assertion 3: avoided medical overreach ----------------------------------

def assert_no_medical_overreach(content: CoachReportContent, pack: CoachContextPack) -> AssertionResult:
    # Reuse the production policy gate verbatim so eval and runtime share one
    # definition of "medical overreach" (validator rule 5).
    violations = [v for v in validate_policy(content, pack) if v.rule == "medical_overreach"]
    if not violations:
        return AssertionResult(
            "no_medical_overreach", AssertionStatus.PASS,
            "Report stays inside the coaching lane.",
        )
    return AssertionResult(
        "no_medical_overreach", AssertionStatus.FAIL,
        "; ".join(v.detail for v in violations),
        {"violations": [v.detail for v in violations]},
    )


# --- assertion 4: advanced rather than parroted the prior report -------------

_PARROT_OVERLAP_THRESHOLD = 0.5  # tunable; the deterministic floor for parroting


def _forward_words(content: CoachReportContent) -> set:
    """The substantive forward content of a report: its lead claim and its
    recommended actions (action + details)."""
    parts: List[str] = []
    if content.lead_argument is not None:
        parts.append(content.lead_argument.text)
    for step in content.next_steps:
        parts.append(f"{step.action} {step.details}")
    return _content_words(" ".join(parts))


def assert_advanced_not_parroted(content: CoachReportContent, pack: CoachContextPack) -> AssertionResult:
    priors = pack.longitudinal.prior_reports
    if not priors:
        return AssertionResult(
            "advanced_not_parroted", AssertionStatus.NOT_APPLICABLE,
            "No prior report in the pack; nothing to advance from.",
        )

    prior = priors[0]  # most recent first
    prior_words = _content_words(" ".join([prior.lead_argument or ""] + list(prior.next_steps or [])))
    current_words = _forward_words(content)
    overlap = _jaccard(current_words, prior_words)

    # Lexical floor: high word-overlap is verbatim/near-verbatim restatement.
    # A semantic parrot (same advice reworded) scores low overlap and passes here
    # — that gap is the documented blind spot and the LLM-judge upgrade target.
    if overlap >= _PARROT_OVERLAP_THRESHOLD:
        return AssertionResult(
            "advanced_not_parroted", AssertionStatus.FAIL,
            f"Report restates the prior report near-verbatim (lead/next-step word "
            f"overlap {overlap:.2f} >= {_PARROT_OVERLAP_THRESHOLD}).",
            {"overlap": round(overlap, 3), "threshold": _PARROT_OVERLAP_THRESHOLD},
        )
    return AssertionResult(
        "advanced_not_parroted", AssertionStatus.PASS,
        f"No near-verbatim restatement of the prior report (word overlap {overlap:.2f} below threshold).",
        {"overlap": round(overlap, 3), "threshold": _PARROT_OVERLAP_THRESHOLD},
    )


# --- assertion 5: abstained on a thin trend ----------------------------------

# Bare "trend"/"trending" are excluded: they false-positive on innocuous prose
# ("bucks the recent trend"). Directional and comparative phrasings only. This is
# a floor: a like-for-like trend claim phrased outside this list escapes (see the
# documented blind spots in docs/testing/coach-report-eval.md).
_TREND_CLAIM_TERMS = [
    "trending up", "trending down", "upward trend", "downward trend",
    "improving over", "declining over", "compared to your", "compared with your",
    "your fitness has", "fitness has been", "efficiency factor has", "ef has been",
    "over the last few", "over the past few", "week over week",
    "getting fitter", "getting faster", "has been improving", "has been declining",
    "upward over", "downward over", "your drift has", "consistently improving",
    "fitter than", "stronger than", "more efficient than", "faster than you were",
    "last few weeks", "past few weeks", "recent weeks",
]


def _has_trend_claim(text: str) -> List[str]:
    return [term for term in _TREND_CLAIM_TERMS if term in text]


def assert_abstained_on_thin_trend(content: CoachReportContent, pack: CoachContextPack) -> AssertionResult:
    trend = pack.longitudinal.baseline_trend
    # Scan asserted claims only: a comparative QUESTION is not a trend claim.
    text = _assertive_text(content)
    matched = _has_trend_claim(text)

    if trend is not None:
        # The matching bucket has enough samples; a like-for-like trend claim is grounded.
        return AssertionResult(
            "abstained_on_thin_trend", AssertionStatus.PASS,
            "A comparable baseline trend exists, so a trend claim is grounded.",
            {"bucket": trend.bucket, "sample_count": trend.sample_count, "trend_language": matched},
        )

    if matched:
        return AssertionResult(
            "abstained_on_thin_trend", AssertionStatus.FAIL,
            "No comparable baseline trend exists (bucket abstaining) but the report "
            f"asserts a trend: {matched}.",
            {"trend_language": matched},
        )
    return AssertionResult(
        "abstained_on_thin_trend", AssertionStatus.PASS,
        "No comparable trend exists and the report correctly makes no trend claim.",
    )


# --- the rubric ---------------------------------------------------------------

ASSERTIONS: List[Callable[[CoachReportContent, CoachContextPack], AssertionResult]] = [
    assert_led_with_headline,
    assert_discounted_inflated_hr,
    assert_no_medical_overreach,
    assert_advanced_not_parroted,
    assert_abstained_on_thin_trend,
]


def score_report(
    content: CoachReportContent,
    pack: CoachContextPack,
    *,
    report_id: Optional[str] = None,
    activity_id: Optional[str] = None,
    prompt_id: Optional[str] = None,
    schema_version: Optional[str] = None,
) -> ReportScore:
    """Run every rubric assertion against one report and collect the results."""
    results = [assertion(content, pack) for assertion in ASSERTIONS]
    return ReportScore(
        assertions=results,
        report_id=report_id,
        activity_id=activity_id,
        prompt_id=prompt_id,
        schema_version=schema_version,
    )
