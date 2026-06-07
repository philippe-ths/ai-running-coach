"""M8 belief store — thin DB layer over the pure logic in `beliefs.py`.

Two entry points:
- `write_back_beliefs(db, activity, pack)`: after a successful report, create or
  reinforce the user's beliefs from this report's signals. Best-effort (a belief
  failure never breaks report generation, mirroring `recompute_runner_baseline`)
  and AT-MOST-ONCE per activity: the `Activity.beliefs_written_at` sentinel makes
  a re-read or force-regeneration of the report safe (no re-counting one run).
- `build_believed_facts(db, activity)`: at context-build time, retrieve the
  user's active, non-decayed, quality-cleared beliefs into the pack section,
  each tagged with confidence + recency. A condition-scoped belief (an HR
  confound) is only surfaced on a run that shares its triggering condition, so a
  heat belief can never be applied to discount a cool-day run.

All reads/writes are user-scoped (ADR-0005 multi-user readiness). Reinforcement
updates the single (user, kind, key) row in place; the UNIQUE constraint on that
identity makes a concurrent duplicate insert fail rather than stack a second
contradictory belief.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Activity
from app.models.coaching_context import CoachingContext
from app.schemas.coach_context import BelievedFact, BelievedFactsContext
from app.services.coach.beliefs import (
    BeliefState,
    apply_delta,
    extract_belief_deltas,
    is_retrievable,
    recency_days,
)

logger = logging.getLogger(__name__)

# Token bound on how many beliefs the pack carries.
_MAX_RETRIEVED_BELIEFS = 8

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

# Beliefs that only apply under a specific run condition (vs. a general tendency).
# A confound belief keyed "heat" is surfaced only when THIS run also shows heat.
_CONDITION_SCOPED_KINDS = {"hr_confound"}


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a naive datetime (SQLite drops tzinfo) to UTC-aware so the pure
    decay/recency comparisons never mix naive and aware. Postgres is always
    aware, so this is a no-op there."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_state(row: CoachingContext) -> BeliefState:
    return BeliefState(
        kind=row.kind,
        key=row.key,
        value=row.value or {},
        confidence=row.confidence,
        observation_count=row.observation_count,
        first_observed_at=_aware(row.first_observed_at),
        last_reinforced_at=_aware(row.last_reinforced_at),
    )


def write_back_beliefs(db: Session, activity: Activity, pack) -> None:
    """Extract belief deltas from this report's signals and create/reinforce the
    user's beliefs, exactly once per activity. Best-effort; never raises into the
    caller."""
    if activity.beliefs_written_at is not None:
        return  # already contributed this activity's observations (force/re-read safe)
    try:
        now = datetime.now(timezone.utc)
        deltas = extract_belief_deltas(
            discount_signals=pack.metrics.discount_signals,
            adherence_outcomes=pack.adherence.outcomes,
        )
        for delta in deltas:
            row = (
                db.query(CoachingContext)
                .filter(
                    CoachingContext.user_id == activity.user_id,
                    CoachingContext.kind == delta.kind,
                    CoachingContext.key == delta.key,
                )
                .first()
            )
            existing = _row_to_state(row) if row else None
            state = apply_delta(existing, delta, now)
            if row is None:
                row = CoachingContext(
                    user_id=activity.user_id,
                    kind=state.kind,
                    key=state.key,
                    first_observed_at=state.first_observed_at,
                )
                db.add(row)
            row.value = state.value
            row.confidence = state.confidence
            row.observation_count = state.observation_count
            row.last_reinforced_at = state.last_reinforced_at

        # Sentinel: mark this activity's observations as contributed, in the same
        # commit as the belief rows, so a failure rolls back both and retries.
        activity.beliefs_written_at = now
        db.add(activity)
        db.commit()
    except IntegrityError:
        # A concurrent write created the same (user, kind, key) first. The unique
        # constraint did its job (no duplicate belief); drop this activity's
        # contribution rather than risk a partial double-count.
        logger.warning("belief write-back lost a unique-identity race for user %s", activity.user_id)
        db.rollback()
    except Exception:
        logger.exception(
            "belief write-back failed for user %s", getattr(activity, "user_id", None)
        )
        db.rollback()


def retrieve_beliefs(
    db: Session, user_id, now: Optional[datetime] = None
) -> List[CoachingContext]:
    """Non-decayed, quality-cleared beliefs for the user, most confident and most
    recently reinforced first, bounded. (Condition-scoping happens in
    `build_believed_facts`, which knows the current run.)"""
    now = now or datetime.now(timezone.utc)
    rows = db.query(CoachingContext).filter(CoachingContext.user_id == user_id).all()
    retrievable = [r for r in rows if is_retrievable(_row_to_state(r), now)]
    retrievable.sort(
        key=lambda r: (
            _CONFIDENCE_RANK.get(r.confidence, 0),
            _aware(r.last_reinforced_at),
        ),
        reverse=True,
    )
    return retrievable[:_MAX_RETRIEVED_BELIEFS]


def build_believed_facts(db: Session, activity: Activity) -> BelievedFactsContext:
    """Assemble the M8 pack section from the user's retrievable beliefs, surfacing
    a condition-scoped belief only when THIS run shares its triggering condition
    (so a heat confound never discounts a cool-day run). Degrades to an empty list
    when the runner has no applicable beliefs yet."""
    now = datetime.now(timezone.utc)
    rows = retrieve_beliefs(db, activity.user_id, now)

    metrics = getattr(activity, "metrics", None)
    discount_signals = metrics.discount_signals if metrics else None
    active_conditions = set((discount_signals or {}).get("likely_inflated_by") or [])

    facts = []
    for row in rows:
        if row.kind in _CONDITION_SCOPED_KINDS and row.key not in active_conditions:
            continue  # belief's condition is not present on this run -> do not apply it
        facts.append(
            BelievedFact(
                kind=row.kind,
                statement=(row.value or {}).get("statement", ""),
                confidence=row.confidence,
                observed_count=row.observation_count,
                last_seen_days_ago=recency_days(_aware(row.last_reinforced_at), now),
            )
        )
    return BelievedFactsContext(facts=facts)
