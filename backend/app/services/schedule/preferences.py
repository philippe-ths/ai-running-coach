"""The coach's preferences for arranging a flexible week (#1082).

A preference never forbids anything (that is what a spacing rule is for); it
ranks the legal arrangements so the app can recommend one. Its wording lives
here, in one place, so the coach is told and the runner is shown exactly what
the preference says and nothing more.
"""

from typing import Any, List

from app.schemas.schedule import PlanPreference

# What each preference asks for, as the coach reads it and the runner sees it.
PREFERENCE_TEXT = {
    "spread_hard_days": "Keep quality and long sessions on days apart.",
    "easy_day_before_long": "The day before the long run holds only easy sessions or rest.",
    "strength_after_run": "Put strength on a day with a run, after the run.",
    "spread_repeats": "Put repeats of the same session on different days.",
}


def plan_preferences(plan: Any) -> List[PlanPreference]:
    """The plan's preferences, strict-coerced; anything off-shape is dropped."""
    coerced: List[PlanPreference] = []
    for raw in getattr(plan, "preferences", None) or []:
        try:
            pref = PlanPreference.model_validate(raw)
        except Exception:
            continue
        if pref.kind not in {p.kind for p in coerced}:
            coerced.append(pref)
    return coerced


def describe_preference(pref: Any) -> str:
    return PREFERENCE_TEXT.get(getattr(pref, "kind", None), "An unrecognised preference.")
