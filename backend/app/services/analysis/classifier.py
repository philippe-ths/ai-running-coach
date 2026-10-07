"""
Activity classification as orthogonal axes (ADR 0007).

An activity is described along independent axes rather than forced into one
mutually-exclusive label:

  - effort:          recovery | easy | moderate | tempo | hard   (intensity, from HR)
  - duration_class:  standard | long                              (length, relative)
  - structure:       continuous | intervals                       (shape)
  - is_hilly:        bool                                          (terrain modifier)
  - is_race:         bool                                          (context modifier)

`effort` is sport-agnostic (any activity with HR). `duration_class`, `structure`,
and `is_hilly` are calibrated for runs in this iteration; non-run activities get
only `effort` and `is_race`. A human-readable headline (e.g. "Long run (tempo)")
is composed from the axes at read time by `compose_headline`.
"""

import re
from dataclasses import dataclass
from statistics import median
from typing import Any, List, Optional, Sequence

from app.models import Activity

# --- Effort (intensity) ---
# Dominant HR zone, escalating to "hard" when a meaningful share sits in Z5.
_Z5_HARD_SHARE = 0.15
_ZONE_TO_EFFORT = {"Z1": "recovery", "Z2": "easy", "Z3": "moderate", "Z4": "tempo", "Z5": "hard"}

# --- Duration (length) ---
_LONG_ABS_FLOOR_S = 4500   # 75 min: long regardless of history
_LONG_REL_FACTOR = 1.4     # >= 1.4x the median recent run
_LONG_REL_MIN_S = 1800     # 30 min sanity floor for the relative path
_LONG_MIN_HISTORY = 3      # need this many prior runs to trust the median

# --- Structure (shape) ---
_INTERVAL_PACE_CV = 20.0   # overall pace variability floor for a genuine interval session

# --- Terrain ---
_HILLY_GAIN_PER_KM = 15.0  # metres of climb per km

_EFFORT_TO_RUN_NOUN = {
    "recovery": "Recovery run",
    "easy": "Easy run",
    "moderate": "Moderate run",
    "tempo": "Tempo run",
    "hard": "Hard run",
}


@dataclass
class Classification:
    """The orthogonal classification axes for one activity."""
    effort: Optional[str] = None
    duration_class: Optional[str] = None
    structure: Optional[str] = None
    is_hilly: Optional[bool] = None
    is_race: Optional[bool] = None

    @classmethod
    def from_metrics(cls, metrics) -> Optional["Classification"]:
        """Reconstruct the axes from a persisted DerivedMetric row."""
        if metrics is None:
            return None
        return cls(
            effort=metrics.effort,
            duration_class=metrics.duration_class,
            structure=metrics.structure,
            is_hilly=metrics.is_hilly,
            is_race=metrics.is_race,
        )


def _sport_type(activity: Activity) -> str:
    raw = activity.raw_summary or {}
    return raw.get("sport_type") or activity.type or "Run"


def _is_run(activity: Activity) -> bool:
    return _sport_type(activity) == "Run"


def _is_trainer(activity: Activity) -> bool:
    return bool((activity.raw_summary or {}).get("trainer", False))


def compute_effort(
    time_in_zones: Optional[dict],
    avg_hr: Optional[float] = None,
    max_hr: Optional[int] = None,
) -> Optional[str]:
    """
    Effort level from the HR-zone distribution, falling back to average %max
    when zones are unavailable. Returns None when there is no HR signal at all.
    """
    if time_in_zones:
        total = sum(time_in_zones.values())
        if total > 0:
            if time_in_zones.get("Z5", 0) / total >= _Z5_HARD_SHARE:
                return "hard"
            dominant = max(_ZONE_TO_EFFORT, key=lambda z: time_in_zones.get(z, 0))
            return _ZONE_TO_EFFORT[dominant]

    if avg_hr and max_hr and max_hr > 0:
        pct = avg_hr / max_hr
        if pct < 0.60:
            return "recovery"
        if pct < 0.70:
            return "easy"
        if pct < 0.80:
            return "moderate"
        if pct < 0.90:
            return "tempo"
        return "hard"

    return None


def _compute_duration_class(activity: Activity, run_history_durations: List[int]) -> str:
    mt = activity.moving_time_s
    if not mt:
        return "standard"
    if mt > _LONG_ABS_FLOOR_S:
        return "long"
    if len(run_history_durations) >= _LONG_MIN_HISTORY and mt >= _LONG_REL_MIN_S:
        med = median(run_history_durations)
        if med > 0 and mt >= med * _LONG_REL_FACTOR:
            return "long"
    return "standard"


# "race" or "racing" as a word: a race now zeroes the load-spike risk and is coached as
# a race, so "Embrace the hills", "Terrace loop" and a "race-pace" workout must not be.
_RACE_WORD = re.compile(r"\brac(e|es|ing)\b(?![\s-]*pace)", re.IGNORECASE)

# Strava's own race marker (`workout_type`): 1 on a run, 11 on a ride.
_STRAVA_RACE_WORKOUT_TYPES = frozenset({1, 11})

# The coarse Strava types of the run family, trail runs included (they arrive as
# type "Run" with sport_type "TrailRun"). A goal race is a run, so only these can
# be one.
_RUN_FAMILY_TYPES = frozenset({"run", "virtualrun"})

# How far a run's recorded distance may sit from the goal race's distance and still
# be that race (#1032). Courses run long, GPS reads short, and trail races are
# approximate; a warm-up or a shakeout jog on race morning falls short of it.
_GOAL_RACE_DISTANCE_RATIO = (0.9, 1.25)


def matching_goal_race(activity: Activity, goal_races: Sequence[Any]) -> Optional[Any]:
    """The runner's goal race this run IS, or None (#1032).

    ``goal_races`` are the runner's goal races on this activity's local day; the
    caller resolves them (analysis has no business querying the schedule). A run
    matches when its distance is within ``_GOAL_RACE_DISTANCE_RATIO`` of the race's.
    """
    if (activity.type or "").lower() not in _RUN_FAMILY_TYPES:
        return None
    distance = activity.distance_m or 0
    lo, hi = _GOAL_RACE_DISTANCE_RATIO
    for race in goal_races or ():
        race_distance = getattr(race, "distance_m", None) or 0
        if race_distance > 0 and lo <= distance / race_distance <= hi:
            return race
    return None


def race_source(activity: Activity, goal_races: Sequence[Any] = ()) -> Optional[str]:
    """Why this activity counts as a race, or None when it does not (#1032).

    Before #1032 only the word "race" in the name or stated intent counted, so a
    runner who neither renamed the activity nor tagged it had no race at all: the
    owner's 662 activities held none, their goal half marathon included. The
    runner's own schedule is the strongest witness, so it is checked first.
    """
    if matching_goal_race(activity, goal_races) is not None:
        return "goal_race"
    if _RACE_WORD.search(activity.user_intent or ""):
        return "stated_intent"
    if (activity.raw_summary or {}).get("workout_type") in _STRAVA_RACE_WORKOUT_TYPES:
        return "strava_marked"
    if _RACE_WORD.search(activity.name or ""):
        return "activity_name"
    return None


def classify_activity(
    activity: Activity,
    history: List[Activity],
    *,
    time_in_zones: Optional[dict] = None,
    pace_variability: Optional[float] = None,
    has_interval_structure: bool = False,
    avg_hr: Optional[float] = None,
    max_hr: Optional[int] = None,
    goal_races: Sequence[Any] = (),
) -> Classification:
    """
    Compute the classification axes for an activity.

    Run-specific axes (duration_class, structure, is_hilly) are only populated
    for runs; non-run activities receive effort (when HR is present) and is_race.
    """
    effort = compute_effort(
        time_in_zones,
        avg_hr if avg_hr is not None else activity.avg_hr,
        max_hr if max_hr is not None else activity.max_hr,
    )
    is_race = race_source(activity, goal_races) is not None

    if not _is_run(activity):
        # Non-run cardio/strength: intensity timeline only for this iteration.
        return Classification(effort=effort, is_race=is_race)

    run_history_durations = [
        a.moving_time_s for a in history
        if _is_run(a) and a.moving_time_s and a.moving_time_s > 0
    ]
    duration_class = _compute_duration_class(activity, run_history_durations)

    structure = (
        "intervals"
        if (has_interval_structure and pace_variability is not None and pace_variability >= _INTERVAL_PACE_CV)
        else "continuous"
    )

    is_hilly = False
    if activity.distance_m and activity.distance_m > 0:
        gain_per_km = (activity.elev_gain_m or 0) / (activity.distance_m / 1000.0)
        is_hilly = gain_per_km >= _HILLY_GAIN_PER_KM

    return Classification(
        effort=effort,
        duration_class=duration_class,
        structure=structure,
        is_hilly=is_hilly,
        is_race=is_race,
    )


# ---------------------------------------------------------------------------
# Read-time derivations from the axes: headline (UI/coach) and playbook key.
# ---------------------------------------------------------------------------

def _sport_noun(activity: Activity) -> str:
    sport = _sport_type(activity)
    trainer = _is_trainer(activity)
    if sport == "Ride":
        return "Indoor ride" if (trainer or (activity.distance_m == 0)) else "Ride"
    if sport == "Run":
        return "Treadmill run" if trainer else "Run"
    if sport == "Walk":
        return "Walk"
    if sport == "Swim":
        return "Swim"
    if sport in ("Workout", "WeightTraining"):
        return "Strength"
    if sport == "Rowing":
        return "Row"
    return sport


def compose_headline(activity: Activity, c: Optional[Classification]) -> str:
    """A single human-readable label composed from the axes at read time."""
    if c is None:
        return _sport_noun(activity)

    effort = c.effort

    # Non-run activities: sport noun, optionally qualified by effort.
    if not _is_run(activity):
        noun = _sport_noun(activity)
        if c.is_race:
            return f"{noun} (race)"
        if effort and effort not in ("easy", "recovery"):
            return f"{noun} ({effort})"
        return noun

    # Runs: primary descriptor by precedence, qualified by effort and terrain.
    if c.is_race:
        base = "Race"
    elif c.structure == "intervals":
        base = f"Intervals ({effort})" if effort in ("moderate", "tempo", "hard") else "Intervals"
    elif c.duration_class == "long":
        base = f"Long run ({effort})" if effort in ("moderate", "tempo", "hard") else "Long run"
    else:
        base = _EFFORT_TO_RUN_NOUN.get(effort, "Run")

    if c.is_hilly:
        base = "Hilly " + base[0].lower() + base[1:]
    return base


def playbook_key(activity: Activity, c: Optional[Classification]) -> Optional[str]:
    """
    Map the axes to one of the coach's activity-type playbooks
    (see ACTIVITY_PLAYBOOKS in coach/prompts.py).
    """
    if c is None or not _is_run(activity):
        return None
    if c.is_race:
        return "Race"
    if c.structure == "intervals":
        return "Intervals"
    if c.duration_class == "long":
        return "Long Run"
    if c.is_hilly:
        return "Hills"
    if c.effort in ("tempo", "hard"):
        return "Tempo"
    return "Easy Run"
