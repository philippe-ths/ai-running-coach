"""
Policy validator — deterministic post-LLM output checks.

Runs after Pydantic schema validation to enforce coaching rules that
the LLM sometimes ignores (zone language, missing questions, etc.).
"""

import re
from dataclasses import dataclass
from typing import List

from app.schemas.coach import CoachReportContent


@dataclass
class PolicyViolation:
    rule: str
    detail: str
    fix_instruction: str


def validate_policy(
    content: CoachReportContent,
    context_pack: dict,
) -> List[PolicyViolation]:
    """
    Run deterministic policy checks on LLM output.
    Returns list of violations (empty = all checks passed).
    """
    violations = []

    # Rule 1: If all check_in fields are null and questions is empty → must ask questions
    check_in = context_pack.get("check_in", {})
    all_null = all(v is None for v in check_in.values())
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
    zones_calibrated = context_pack.get("metrics", {}).get("zones_calibrated", False)
    if not zones_calibrated:
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
    valid_flags = set(context_pack.get("metrics", {}).get("flags", []))
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

    return violations


def _extract_all_text(content: CoachReportContent) -> str:
    """Concatenate all text fields for pattern matching."""
    parts = []
    for t in content.key_takeaways:
        parts.append(t.text if hasattr(t, "text") else str(t))
    for s in content.next_steps:
        parts.extend([s.action, s.details, s.why])
        if s.evidence:
            parts.append(s.evidence)
    for r in content.risks:
        parts.extend([r.explanation, r.mitigation])
    for q in content.questions:
        parts.extend([q.question, q.reason])
    return " ".join(parts)
