"""
Policy validator — deterministic post-LLM output checks.

Runs after Pydantic schema validation to enforce coaching rules that
the LLM sometimes ignores (zone language, confidence gating, etc.).
"""

import re
from dataclasses import dataclass
from typing import List

from app.schemas.coach import CoachReportContent
from app.schemas.coach_context import CoachContextPack


@dataclass
class PolicyViolation:
    rule: str
    detail: str
    fix_instruction: str


# Patterns that indicate the LLM is claiming specific interval execution
_INTERVAL_CLAIM_PATTERNS = [
    re.compile(r"\b\d+\s*x\s*\d+\s*m?\b", re.IGNORECASE),  # "8x400m"
    re.compile(r"\b\d+\s+reps?\b", re.IGNORECASE),  # "8 reps"
    re.compile(r"\bexecuted\s+\d+", re.IGNORECASE),  # "executed 8"
    re.compile(r"\bcompleted\s+\d+\s*(reps?|intervals?|repeats?)", re.IGNORECASE),
]


# --- Rule 5: medical-scope boundary (N2) -----------------------------------
# Oracle: plan section 8. Reject dose advice and diagnosis verbs; never let a
# single wearable number escalate into a health claim. Permit interpretive
# metric correction ("discount this drift, it was hot") and the non-diagnostic
# referral nudge ("consider seeing a clinician"). High precision is the
# priority: over-firing rejects legitimate reports and forces a fallback.

# Pharmaceutical dose units ("200mg", "200mgs", "5 mcg", "2000 IU").
# Deliberately excludes grams (sports-nutrition language) and the bare word
# "dose"/"dosage" (idiomatic coaching: "small doses of easy running"); a real
# dose instruction is caught here by its unit, or by _MED_DIRECTIVE_PATTERN when
# a change verb targets a "dose"/"dosage". Known limit: spelled-out numbers
# ("five hundred milligrams") and gram-dosed compounds are not matched.
_DOSE_PATTERN = re.compile(
    r"\b\d+\s?(?:mgs?|mcgs?|µg|milligrams?|micrograms?|i\.?u\.?)\b",
    re.IGNORECASE,
)

# Diagnosis verbs in any form.
_DIAGNOSIS_VERB_PATTERN = re.compile(r"\bdiagnos(?:e|es|ed|ing|is)\b", re.IGNORECASE)

# Directive medication advice: a change/start verb aimed at a medication noun
# (including "dose"/"dosage") within a short window. The {0,25} bound keeps the
# match linear (no catastrophic backtracking). Known limit: verb enumeration is
# inherently leaky.
_MED_DIRECTIVE_PATTERN = re.compile(
    r"\b(?:take|taking|start|starting|stop|stopping|begin|beginning|increase|"
    r"decrease|reduce|lower|raise|adjust|change|switch|go|get|put|need|needs|"
    r"consider|prescrib\w+)\b[\w\s,'\"-]{0,25}?"
    r"\b(?:medications?|meds?|beta[\s-]?blockers?|statins?|antidepressants?|"
    r"ssris?|supplements?|iron (?:pills?|tablets?|supplements?)|"
    r"doses?|dosages?)\b",
    re.IGNORECASE,
)

# Clinical conditions a non-medical coach must not assert as fact. This list is
# deliberately non-exhaustive (it can never cover every condition); it backstops
# the most common overreach. "depression" is included despite a rare
# elevation-"depression" terrain phrasing, because asserting a mood disorder is
# the higher-cost miss.
#
# The musculoskeletal/overload block was added in M9: the non-diagnostic referral
# nudge fires on pain/fatigue patterns, which is exactly the context that invites
# an LLM to name a running injury ("this is probably shin splints"). M9 extends
# the M0 medical-scope rule to backstop those names, since the referral leans on
# this gate. Bare "burnout"/"overtraining" are safe here because a claim only
# trips with an assertion verb in front (_HEALTH_CLAIM_PATTERN), so coaching like
# "avoid overtraining" does not match.
# Unambiguous clinical/injury terms (no benign running sense). Safe to match even
# after a bare copula or modal ("this is / could be a stress fracture").
_CONDITION_TERMS_SAFE = (
    r"anemi[ac]|anaemi[ac]|overtraining(?: syndrome)?|red-?s|"
    r"relative energy deficiency|hypothyroid\w*|hyperthyroid\w*|"
    r"thyroid (?:disorder|condition|disease)|arrhythmias?|"
    r"atrial fibrillation|a-?fib|myocarditis|cardiomyopathy|"
    r"heart (?:disease|condition)|hypertension|iron deficiency|low ferritin|"
    r"diabet(?:es|ic)|"
    # M9: common running injuries / overload conditions
    r"stress fracture|stress reaction|stress injur(?:y|ies)|"
    r"tendin(?:itis|opathy|osis)|tendonitis|shin splints|"
    r"plantar fasciitis|fasciitis|iliotibial band|it band syndrome|"
    r"patellofemoral|runner'?s knee|bursitis|sciatica|burnout|"
    r"compartment syndrome|labral tear|meniscus tear|"
    r"torn (?:meniscus|labrum|acl|ligament|muscle)"
)

# Terms with a benign running sense ("HR is depressed", "high blood pressure
# zones", "asthma-friendly"): only flagged after an explicit assertion verb, not
# a bare copula, to keep precision.
_CONDITION_TERMS_AMBIGUOUS = r"high blood pressure|asthma|depress(?:ion|ed)"

_CONDITION_TERMS = _CONDITION_TERMS_SAFE + r"|" + _CONDITION_TERMS_AMBIGUOUS

# A condition term asserted (not merely named) about the runner: an assertion
# verb immediately followed by the condition, with an optional article/qualifier.
_HEALTH_CLAIM_PATTERN = re.compile(
    r"\b(?:have|has|having|got|you're|you are|suffering from|suffer from|"
    r"diagnosed with|indicates?|means|sign of|signs of|symptom of|symptoms of|"
    r"consistent with|points? to)\s+"
    r"(?:a |an |the |some |possible |likely |early |signs? of |chronic )?"
    r"(?:" + _CONDITION_TERMS + r")\b",
    re.IGNORECASE,
)

# Speculative naming ("this is / could be / might be / looks like a stress
# fracture") — how an LLM names a condition while "not diagnosing". Only the
# unambiguous terms, since a bare copula is common in running prose.
_SPECULATIVE_CLAIM_PATTERN = re.compile(
    r"\b(?:is|are|could be|might be|may be|looks like|sounds like)\s+"
    r"(?:a |an |the |probably |likely |possibly |a possible |an early |early )*"
    r"(?:" + _CONDITION_TERMS_SAFE + r")\b",
    re.IGNORECASE,
)

# Negations that flip an apparent claim into a reassurance ("no signs of X").
# Note: "any" is intentionally NOT here — it is a common non-negating word
# ("any fatigue?") and including it silently suppressed real claims.
_NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|n't|never|without|free of|rule out|ruled out|"
    r"denies?|negative for|nothing|unlikely)\b",
    re.IGNORECASE,
)


def _has_asserted_health_claim(text: str) -> bool:
    """True if a clinical condition is asserted about the runner and not negated."""
    for pattern in (_HEALTH_CLAIM_PATTERN, _SPECULATIVE_CLAIM_PATTERN):
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 40):match.start()]
            if _NEGATION_PATTERN.search(window):
                continue  # "no signs of overtraining syndrome" — reassurance, not a claim
            return True
    return False


def validate_policy(
    content: CoachReportContent,
    context_pack: CoachContextPack,
) -> List[PolicyViolation]:
    """
    Run deterministic policy checks on LLM output.
    Returns list of violations (empty = all checks passed).
    """
    violations = []

    # Rule 1: If all check_in fields are null and questions is empty → must ask questions
    check_in = context_pack.check_in
    all_null = all(
        getattr(check_in, field) is None for field in type(check_in).model_fields
    )
    if all_null and len(content.questions) == 0:
        violations.append(PolicyViolation(
            rule="missing_questions_for_null_checkin",
            detail="All check_in fields are null but no questions were generated",
            fix_instruction=(
                "Add 1-2 questions asking about RPE, sleep quality, or how the "
                "runner felt during the session. Example: 'How did you feel during "
                "the session?' with reason 'No check-in data available'."
            ),
        ))

    # Rule 2: If zones_calibrated=false and output mentions Z1/Z2/Z3/Z4/Z5
    if not context_pack.metrics.zones_calibrated:
        full_text = _extract_all_text(content)
        zone_pattern = re.compile(r"\bZ[1-5]\b")
        if zone_pattern.search(full_text):
            violations.append(PolicyViolation(
                rule="uncalibrated_zone_reference",
                detail="Output references HR zones but zones_calibrated is false",
                fix_instruction=(
                    "Replace all zone references (Z1-Z5) with effort-based "
                    "language: 'easy conversational pace' (RPE 2-3), 'moderate "
                    "effort' (RPE 4-5), 'comfortably hard' (RPE 6-7), 'hard "
                    "threshold effort' (RPE 8), 'maximum effort' (RPE 9-10)."
                ),
            ))

    # Rule 3: If risk references a flag not in the flags array
    valid_flags = set(context_pack.metrics.flags)
    for risk in content.risks:
        if risk.flag not in valid_flags:
            violations.append(PolicyViolation(
                rule="invalid_risk_flag",
                detail=f"Risk references flag '{risk.flag}' not in flags array {valid_flags}",
                fix_instruction=(
                    f"Remove the risk entry for '{risk.flag}' or only reference "
                    f"flags from: {sorted(valid_flags)}"
                ),
            ))

    # Rule 4: If detection_confidence < high and LLM claims specific interval execution
    workout_match = context_pack.metrics.workout_match
    if workout_match:
        det_conf = workout_match.get("detection_confidence", "low")
        match_score = workout_match.get("match_score")
        low_confidence = det_conf == "low" or (
            match_score is not None and match_score < 0.7
        )
        if low_confidence:
            full_text = _extract_all_text(content)
            for pattern in _INTERVAL_CLAIM_PATTERNS:
                if pattern.search(full_text):
                    violations.append(PolicyViolation(
                        rule="ungated_interval_claim",
                        detail=(
                            f"LLM claims specific interval execution but "
                            f"detection_confidence={det_conf}, match_score={match_score}"
                        ),
                        fix_instruction=(
                            "Detection confidence is low. Do NOT claim specific rep counts, "
                            "distances, or interval structure as fact. Instead say: "
                            "'Your data suggests the intervals were not consistently detected. "
                            "Consider using the lap button or running on a track for better "
                            "rep-by-rep feedback.' Treat all rep statistics as approximate."
                        ),
                    ))
                    break  # One violation is enough

    # Rule 5: medical-scope boundary — reject medical overreach (plan section 8).
    full_text = _extract_all_text(content)
    medical_reason = None
    if _DOSE_PATTERN.search(full_text):
        medical_reason = "contains dose advice (a pharmaceutical dose or dosage instruction)"
    elif _DIAGNOSIS_VERB_PATTERN.search(full_text):
        medical_reason = "uses a diagnosis verb"
    elif _MED_DIRECTIVE_PATTERN.search(full_text):
        medical_reason = "gives directive medication advice"
    elif _has_asserted_health_claim(full_text):
        medical_reason = "asserts a clinical condition about the runner"
    if medical_reason:
        violations.append(PolicyViolation(
            rule="medical_overreach",
            detail=f"Output {medical_reason}, which is outside the coaching scope",
            fix_instruction=(
                "Stay inside the general-wellness coaching lane. Do NOT give drug or "
                "supplement doses, use diagnosis verbs, name or assert a clinical "
                "condition, or escalate a single wearable number into a health claim. "
                "You MAY interpret and correct a metric (e.g. 'discount this HR drift, "
                "it was hot, so it overstates fatigue') and you MAY suggest seeing a "
                "clinician as a non-diagnostic nudge. Rephrase to remove the overreach."
            ),
        ))

    return violations


def _extract_all_text(content: CoachReportContent) -> str:
    """Concatenate all text fields for pattern matching."""
    parts = []
    # Grounded-reshape (N3) verdict layer: scan headline/thesis/lead_argument so
    # the medical-scope and zone-language rules also cover the strongest claim,
    # not just the takeaway list. Safety requirement: the lead argument is the
    # most prominent text, so it must be policed too.
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
    for q in content.questions:
        parts.extend([q.question, q.reason])
    return " ".join(parts)
