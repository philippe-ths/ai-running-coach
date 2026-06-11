"""A4 salience substrate — the deterministic SAFETY OVERRIDE.

A red-flag pattern forces a non-silent opener AND a scheduled fuller turn,
regardless of the opener LLM's judgment: the model can never decide to stay quiet
on a safety signal (ADR 0010). This mirrors the existing referral-nudge floor —
in fact it reuses the EXACT red-flag predicate the M9 referral nudge fires on
(``calibration.assess_referral``), so there is a single definition of "red flag"
across the codebase rather than a third one. Pure; abstains when no red flag is
present.

The opener job consumes ``force_fuller`` to decide whether to schedule stage two
(OR-ed with the opener LLM's own ``schedule_fuller_turn`` judgment), so a red-flag
run always earns a fuller turn even if the model judged otherwise.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.coach.calibration import assess_referral


def compute_safety_override(
    *, flags: Optional[List[str]], pain_scores: Optional[List[int]]
) -> Dict[str, Any]:
    """Return ``{"force_fuller": bool, "reasons": [pattern]}``.

    ``force_fuller`` is True iff a red-flag pattern fires — the same patterns as
    the referral nudge: the multi-signal ``illness_or_extreme_fatigue`` flag, or
    sustained notable pain across recent runs. ``reasons`` carries the matched
    pattern for audit. Abstains (force_fuller False, empty reasons) otherwise; it
    never fabricates a safety concern.
    """
    referral = assess_referral(flags=flags, pain_scores=pain_scores)
    if referral is None:
        return {"force_fuller": False, "reasons": []}
    return {"force_fuller": True, "reasons": [referral["pattern"]]}
