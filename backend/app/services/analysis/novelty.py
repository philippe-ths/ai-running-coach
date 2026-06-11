"""A4 salience substrate — the deterministic NOVELTY signal.

"First-of-its-kind in the runner's history": which notable classification axes of
THIS run the runner has not done before. Pure — it takes the current run's
classification axes plus the prior analysed runs' axes and returns the list of
first-of-kind tags and whether the runner has enough history for novelty to be
meaningful. The DB query that gathers the prior axes lives in the coach context
assembly (context.py), mirroring how calibration's pure module is gathered there.

Salience is NOT intensity or load (CONTEXT.md): a planned-and-nailed hard session
can be low-salience, a light first-ever activity high-salience. So novelty keys on
axis-novelty ONLY — never on effort, effort_score, or any load magnitude.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# A cold-start runner's first runs are trivially "first of everything", which is
# noise, not salience. Novelty abstains until the runner has this many prior
# analysed runs to compare against (mirrors the classifier's <3-prior-runs
# duration_class abstention, so the deterministic layer stays coherent).
MIN_HISTORY_FOR_NOVELTY = 3


@dataclass(frozen=True)
class AxisSnapshot:
    """The novelty-relevant classification axes of one analysed activity (ADR 0007)."""

    structure: Optional[str] = None
    duration_class: Optional[str] = None
    is_race: Optional[bool] = None
    is_hilly: Optional[bool] = None


def compute_novelty(current: AxisSnapshot, history: List[AxisSnapshot]) -> Dict[str, Any]:
    """Return ``{"first_of_kind": [...], "has_history": bool}``.

    ``history`` is the runner's PRIOR analysed activities (this run excluded), in
    any order. Abstains (``has_history`` False, empty ``first_of_kind``) until the
    runner has at least ``MIN_HISTORY_FOR_NOVELTY`` prior analysed runs, so a
    brand-new runner's first sessions are not all flagged maximally novel.

    The predicate set is deliberately small and documented (the M5 "documented
    set" discipline): first interval session, first long run, first race, first
    hilly run. Each fires only when the current run has that axis value AND no
    prior run did.
    """
    if len(history) < MIN_HISTORY_FOR_NOVELTY:
        return {"first_of_kind": [], "has_history": False}

    first: List[str] = []
    if current.structure == "intervals" and not any(
        h.structure == "intervals" for h in history
    ):
        first.append("first_interval_session")
    if current.duration_class == "long" and not any(
        h.duration_class == "long" for h in history
    ):
        first.append("first_long_run")
    if current.is_race and not any(h.is_race for h in history):
        first.append("first_race")
    if current.is_hilly and not any(h.is_hilly for h in history):
        first.append("first_hilly_run")

    return {"first_of_kind": first, "has_history": True}
