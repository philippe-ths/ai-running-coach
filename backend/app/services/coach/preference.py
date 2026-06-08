"""M10 per-runner preference model (the capstone).

A lightweight, no-fine-tune "reward signal": derive, from the accumulated M7/M8
adherence record, which advice themes this runner demonstrably ACTS ON vs
IGNORES, and surface that so the coach reranks and frames its next_steps toward
what lands. This is preference-conditioned generation (T-POP style: retrieve the
preference, condition the framing), not a trained reward model and not a
base-model fine-tune.

The signal is already explicit-feedback-aware: it is built from the M8
adherence_pattern beliefs, and M8 never reinforces a belief from a disputed
outcome, so advice the runner explicitly pushed back on does not inflate its
adherence ratio here. It never overrides the re-derived DerivedMetric; it only
biases which forward advice is chosen and how it is framed.
"""

from __future__ import annotations

from typing import Any, List

from app.schemas.coach_context import PreferenceProfile, PreferenceTheme

# A theme needs at least this many observations before a decisive tendency is
# claimed (mirrors the project's "don't trend two runs" discipline).
MIN_TOTAL_FOR_TENDENCY = 3

_ACTS_ON_RATIO = 0.7
_IGNORES_RATIO = 0.3

# Rank order so the pack lists the most actionable preference first.
_TENDENCY_RANK = {"acts_on": 0, "mixed": 1, "ignores": 2}


def _field(belief: Any, name: str) -> Any:
    if isinstance(belief, dict):
        return belief.get(name)
    return getattr(belief, name, None)


def build_preference_profile(adherence_beliefs: List[Any]) -> PreferenceProfile:
    """Build the preference profile from the runner's adherence_pattern beliefs
    (dicts or row-like objects with `key` and `value={acted,total}`). A theme
    with too little history carries no tendency and is omitted."""
    themes: List[PreferenceTheme] = []
    for belief in adherence_beliefs or []:
        theme = _field(belief, "key")
        value = _field(belief, "value")
        if not isinstance(value, dict):
            continue  # malformed belief payload -> skip rather than crash
        acted = value.get("acted")
        total = value.get("total")
        if not theme or not isinstance(total, int) or total < MIN_TOTAL_FOR_TENDENCY:
            continue
        acted = acted if isinstance(acted, int) else 0
        ratio = acted / total
        if ratio >= _ACTS_ON_RATIO:
            tendency = "acts_on"
        elif ratio <= _IGNORES_RATIO:
            tendency = "ignores"
        else:
            tendency = "mixed"
        themes.append(PreferenceTheme(theme=theme, tendency=tendency, acted=acted, total=total))

    themes.sort(key=lambda t: (_TENDENCY_RANK.get(t.tendency, 9), t.theme))
    return PreferenceProfile(themes=themes)
