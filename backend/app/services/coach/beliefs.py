"""M8 belief-state write-back — pure logic.

Deterministically derive durable beliefs from a report's already-computed signals
(no free LLM text, so they stay auditable per the project's "deterministic
templated relay" guardrail), and decide how each belief is created, reinforced,
aged out, and retrieved. This module is pure: every time-dependent decision takes
`now` as a parameter. The thin DB layer (`belief_store.py`) reads/writes the
`coaching_context` rows on top of these decisions.

Two belief families are extracted in v1 (the set is extensible):
- `hr_confound`: a confirmed environmental HR confound (heat), from
  `discount_signals`. Only a measured (high-confidence) confound becomes a
  belief, so an unmeasured heat guess is never fabricated into a sensitivity.
- `adherence_pattern`: whether the runner tends to act on a kind of advice, from
  the M7 adherence outcomes. Accumulates acted/total counts so a contradiction
  (an opposing outcome) RESOLVES the belief's direction in place rather than
  stacking a second contradictory belief.

These beliefs never override the re-derived DerivedMetric ground truth; they are
prior context the coach applies, retrieved with confidence/recency tags.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# A belief is withheld from retrieval until it has been observed at least this
# many times: a single observation is a data point, not yet a durable belief.
MIN_OBSERVATIONS_FOR_RETRIEVAL = 2

# Adherence beliefs keep a ROLLING window of observations, not an all-time tally,
# so a genuine behaviour change moves the ratio instead of staying buried under
# history. When the window is full, older observations are decayed proportionally
# before the new one is counted.
_ADHERENCE_WINDOW = 8

# TTL (days) by semantic class, measured from last reinforcement. A belief that
# is not re-observed within its TTL decays out of retrieval (but is kept for
# audit); a reinforced belief refreshes its recency and persists.
_TTL_DAYS = {
    "hr_confound": 120,       # environmental/seasonal
    "adherence_pattern": 90,
    "physiology": 365,        # near-immutable (reserved; not extracted in v1)
    "transient": 14,          # e.g. "on a stimulant this week" (reserved)
}
_DEFAULT_TTL_DAYS = 90

_THEME_PHRASE = {
    "easy_discipline": "keeping easy runs easy",
    "add_quality": "adding the suggested quality sessions",
    "add_long_run": "adding the suggested long runs",
}


@dataclass(frozen=True)
class BeliefDelta:
    """A candidate belief observation extracted from one report's signals."""

    kind: str
    key: str
    statement: str
    value: Dict[str, Any]


@dataclass(frozen=True)
class BeliefState:
    """The persistable fields of a belief, as plain data (mirrors the row)."""

    kind: str
    key: str
    value: Dict[str, Any]
    confidence: str
    observation_count: int
    first_observed_at: datetime
    last_reinforced_at: datetime


def confidence_for(observation_count: int) -> str:
    """More independent observations -> more confidence the pattern is real."""
    if observation_count >= 3:
        return "high"
    if observation_count >= 2:
        return "medium"
    return "low"


def ttl_days(kind: str) -> int:
    return _TTL_DAYS.get(kind, _DEFAULT_TTL_DAYS)


def is_decayed(kind: str, last_reinforced_at: datetime, now: datetime) -> bool:
    return (now - last_reinforced_at) > timedelta(days=ttl_days(kind))


def recency_days(last_reinforced_at: datetime, now: datetime) -> int:
    return (now - last_reinforced_at).days


def is_retrievable(state: BeliefState, now: datetime) -> bool:
    """A belief reaches the next report only when it has cleared the quality
    threshold and has not decayed. (Condition-scoping of a confound belief to a
    matching run happens in the store's `build_believed_facts`.)"""
    return (
        state.observation_count >= MIN_OBSERVATIONS_FOR_RETRIEVAL
        and not is_decayed(state.kind, state.last_reinforced_at, now)
    )


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

def _outcome_field(outcome: Any, name: str) -> Any:
    """Read a field from a NextStepOutcome object or a plain dict."""
    if isinstance(outcome, dict):
        return outcome.get(name)
    return getattr(outcome, name, None)


def extract_belief_deltas(
    *,
    discount_signals: Optional[Dict[str, Any]],
    adherence_outcomes: List[Any],
) -> List[BeliefDelta]:
    """Deterministically derive candidate belief deltas from one report's signals.

    `discount_signals` is the DerivedMetric.discount_signals dict (or None);
    `adherence_outcomes` is the M7 adherence outcomes (NextStepOutcome objects or
    dicts). Returns an empty list when nothing qualifies."""
    deltas: List[BeliefDelta] = []

    # HR confound: only a MEASURED (high-confidence) heat confound becomes a
    # belief, never an unmeasured guess.
    if discount_signals:
        inflators = discount_signals.get("likely_inflated_by") or []
        if "heat" in inflators and discount_signals.get("confidence") == "high":
            deltas.append(
                BeliefDelta(
                    kind="hr_confound",
                    key="heat",
                    statement=(
                        "On warm days (roughly 25C and above), this runner's heart rate "
                        "tends to read inflated, so HR drift overstates fatigue THEN. "
                        "This applies only in the heat, not on cool days."
                    ),
                    value={},
                )
            )

    # Adherence pattern: one observation per non-disputed outcome.
    for outcome in adherence_outcomes or []:
        label = _outcome_field(outcome, "label")
        theme = _outcome_field(outcome, "theme")
        overridden = _outcome_field(outcome, "overridden")
        if overridden or label == "disputed" or not theme:
            continue  # the runner disputed it -> not an adherence data point
        if label not in ("acted_on", "ignored", "contradicted"):
            continue
        deltas.append(
            BeliefDelta(
                kind="adherence_pattern",
                key=theme,
                statement="",  # recomputed from accumulated counts on apply
                value={"outcome": label},
            )
        )

    return deltas


# --------------------------------------------------------------------------- #
# create / reinforce (with contradiction resolution)
# --------------------------------------------------------------------------- #

def _adherence_statement(theme: str, acted: int, total: int) -> str:
    phrase = _THEME_PHRASE.get(theme, theme.replace("_", " "))
    ratio = acted / total if total else 0.0
    if ratio >= 0.7:
        return f"This runner tends to follow guidance about {phrase} (acted on {acted} of {total})."
    if ratio <= 0.3:
        return (
            f"This runner tends NOT to act on guidance about {phrase} "
            f"(acted on {acted} of {total}); consider framing it differently."
        )
    return f"This runner has a mixed response to guidance about {phrase} (acted on {acted} of {total})."


def apply_delta(existing: Optional[BeliefState], delta: BeliefDelta, now: datetime) -> BeliefState:
    """Pure create-or-reinforce. Creating when `existing` is None; otherwise
    reinforcing the SAME belief (non-redundant: never a duplicate row). For
    adherence, an opposing observation resolves the belief's direction in place
    by recomputing the acted/total counts and statement."""
    if existing is None:
        if delta.kind == "adherence_pattern":
            acted = 1 if delta.value.get("outcome") == "acted_on" else 0
            value = {
                "acted": acted,
                "total": 1,
                "statement": _adherence_statement(delta.key, acted, 1),
            }
        else:
            value = {"statement": delta.statement, **delta.value}
        return BeliefState(
            kind=delta.kind,
            key=delta.key,
            value=value,
            confidence=confidence_for(1),
            observation_count=1,
            first_observed_at=now,
            last_reinforced_at=now,
        )

    count = existing.observation_count + 1
    if delta.kind == "adherence_pattern":
        acted = existing.value.get("acted", 0)
        total = existing.value.get("total", 0)
        # Rolling window: decay older observations before counting the new one, so
        # a recent behaviour change can actually move the ratio.
        if total >= _ADHERENCE_WINDOW:
            keep = _ADHERENCE_WINDOW - 1
            acted = round(acted * keep / total)
            total = keep
        acted += 1 if delta.value.get("outcome") == "acted_on" else 0
        total += 1
        value = {"acted": acted, "total": total, "statement": _adherence_statement(delta.key, acted, total)}
    else:
        value = dict(existing.value)
        value["statement"] = delta.statement  # keep latest phrasing

    return replace(
        existing,
        value=value,
        observation_count=count,
        confidence=confidence_for(count),
        last_reinforced_at=now,
    )
