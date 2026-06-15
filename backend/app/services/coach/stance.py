"""
P1.3 Coaching stance — the runner-declared, runner-sovereign coach stance
(ADR 0015, mirroring voice.py one layer down).

This module owns the stance DOMAIN DATA and its RESOLUTION, with no LLM and no I/O:

- The two operable emphasis axes and their pole labels (`EMPHASIS_AXES`): Data ↔
  Sentiment (lead with numbers vs how it felt) and Process ↔ Outcome (foreground
  habits/execution vs results/PRs). They are content-focus, orthogonal to the
  four Voice dials (a blunt voice can foreground encouragement; a warm voice can
  be accountability-heavy).
- The balanced `DEFAULT_EMPHASIS` (3/3) the coach sits at when no emphasis is set.
- `resolve_stance`, which turns a `CoachingRelationship` row into a `StanceProfile`:
  the selected `school_id` (the corpus school the coach reasons from) and the two
  effective emphasis values.

How a `StanceProfile` becomes prompt text / a pack section lives elsewhere
(`prompts.py` renders rule 26's per-runner emphasis; `context.py` builds the
`stance` pack section and threads `school_id` into the `corpus` section). This
module never renders and never reads the DB. Stance reweights emphasis and
method-framing only — nothing here touches facts or the safety floor (ADR 0015).

The selected school is NOT validated here: `resolve_stance` passes the stored
school through, and `retrieval.fetch_corpus` owns the single unknown->house-core-
only degradation. An UNDECLARED stance resolves to the P1.2 default school
(`DEFAULT_SCHOOL_ID`, aerobic-base) so the default experience is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.coach.corpus import DEFAULT_SCHOOL_ID


# ---------------------------------------------------------------------------
# The two operable emphasis axes (ADR 0015, design doc §6). Low pole = lead with
# the harder/objective focus, high pole = lead with the softer/goal focus; the
# pole labels double as graph axis labels and as the words the prompt uses. `attr`
# is the column on the CoachingRelationship row.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmphasisAxis:
    key: str            # short stable key (data_sentiment / process_outcome)
    attr: str           # CoachingRelationship column name
    low_pole: str       # label at value 1
    high_pole: str      # label at value 5


EMPHASIS_AXES: tuple[EmphasisAxis, ...] = (
    EmphasisAxis("data_sentiment", "stance_data_sentiment", "Data", "Sentiment"),
    EmphasisAxis("process_outcome", "stance_process_outcome", "Process", "Outcome"),
)

EMPHASIS_MIN = 1
EMPHASIS_MAX = 5


@dataclass(frozen=True)
class Emphasis:
    """The two operable emphasis values, each 1-5 (low pole to high pole)."""

    data_sentiment: int  # 1 = Data (lead with numbers), 5 = Sentiment (how it felt)
    process_outcome: int  # 1 = Process (habits/execution), 5 = Outcome (results/PRs)

    def as_ordered(self) -> tuple[tuple[EmphasisAxis, int], ...]:
        """Pair each axis with its value, in canonical EMPHASIS_AXES order."""
        return tuple((axis, getattr(self, axis.key)) for axis in EMPHASIS_AXES)


# The balanced centre the coach sits at when no emphasis is declared. Deliberately
# the midpoint of both axes: neither data- nor sentiment-led, neither process- nor
# outcome-led, until the runner tilts it.
DEFAULT_EMPHASIS = Emphasis(data_sentiment=3, process_outcome=3)


@dataclass(frozen=True)
class StanceProfile:
    """The resolved, effective stance for one generation.

    `school_id` is the corpus school the coach reasons from (an undeclared stance
    resolves to `DEFAULT_SCHOOL_ID`; a stored value passes through for the
    `fetch_corpus` seam to resolve or degrade). `emphasis` is the effective
    Data↔Sentiment / Process↔Outcome tilt. `is_default` is True when nothing was
    declared, which keeps an undeclared runner byte-stable against the P1.2 wired
    default.
    """

    school_id: str
    emphasis: Emphasis
    is_default: bool


def _clamp(value: Optional[int], fallback: int) -> int:
    """Coerce a stored emphasis to the valid 1-5 band, falling back when null/garbage."""
    if value is None:
        return fallback
    return max(EMPHASIS_MIN, min(EMPHASIS_MAX, int(value)))


def resolve_stance(relationship) -> StanceProfile:
    """Resolve a CoachingRelationship row (or None) into the effective StanceProfile.

    Precedence: a stored school wins, else the moderate default school
    (`DEFAULT_SCHOOL_ID`); each emphasis axis's stored value wins, else the
    balanced default. A null/absent stance resolves wholly to the default school +
    balanced emphasis with is_default=True, which keeps pre-stance behaviour
    byte-identical to the P1.2-wired default. The school is NOT validated here;
    `fetch_corpus` owns the unknown->house-core-only degradation.
    """
    if relationship is None:
        return StanceProfile(
            school_id=DEFAULT_SCHOOL_ID, emphasis=DEFAULT_EMPHASIS, is_default=True
        )

    school = getattr(relationship, "stance_school", None)
    raw = {
        "data_sentiment": getattr(relationship, "stance_data_sentiment", None),
        "process_outcome": getattr(relationship, "stance_process_outcome", None),
    }

    declared = school is not None or any(v is not None for v in raw.values())
    if not declared:
        return StanceProfile(
            school_id=DEFAULT_SCHOOL_ID, emphasis=DEFAULT_EMPHASIS, is_default=True
        )

    emphasis = Emphasis(
        data_sentiment=_clamp(raw["data_sentiment"], DEFAULT_EMPHASIS.data_sentiment),
        process_outcome=_clamp(raw["process_outcome"], DEFAULT_EMPHASIS.process_outcome),
    )
    return StanceProfile(
        school_id=school or DEFAULT_SCHOOL_ID, emphasis=emphasis, is_default=False
    )
