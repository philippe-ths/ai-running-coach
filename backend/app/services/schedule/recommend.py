"""The recommended week: which day each session goes on, and in what order (#1082).

#1081 answers "where CAN each session go"; this answers "where SHOULD it", for a
runner who would rather follow a plan than arrange one. It is deterministic code,
never a model call: the same week, the same ticks and the same last
recommendation always give the same answer, and a tick re-plans instantly.

How a week is chosen
--------------------
1. Every recommendation is a LEGAL week: the same rule search the plan is held to
   (`rules.find_assignment` and its predicates) checks every partial placement,
   with done sessions and unplanned extras fixed on their days.
2. When the sessions left cannot all fit, the least important are dropped first,
   in the runner's own order (#1083): walks, then bike/row/other, then easy runs,
   then strength, then quality, and the long run last.
3. Among the legal weeks, the one with the lowest cost wins. The cost is the
   coach's preferences (`TrainingPlan.preferences`) plus a large charge for every
   session moved off the day it was last recommended, so re-planning moves as
   little as possible, plus a charge for stacking a day that grows with every
   activity already there, so load spreads evenly when nothing else decides,
   and a little more for the same sport twice in a day.

Every cost term only grows as sessions are added, so the cost of a partial week
is a lower bound for any week that completes it, which is what makes the
branch-and-bound pruning sound. The search carries a placement budget; past it,
the best week found so far stands, and failing that the plain legal week.

Why each day is ordered
-----------------------
Within a day, the runner builds up: easiest first, hardest last, on the same
scale as dropping (walk, other easy sport, easy run, strength, quality, long), so
the shower comes after the hardest session. A walk is an activity like any
other and takes its place in the order. Every day with more than one activity
is numbered.

Every reason the runner reads is fixed wording, here or in `preferences.py`,
tied to the preference or ordering it comes from, never free text.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.schedule.placement import PlacedSession, candidate_days
from app.services.schedule.rules import (
    PREDICATES,
    SearchBudget,
    SearchBudgetExceeded,
    find_assignment,
    violations_for,
)

HARD = ("quality", "long")

# Dropped first to last when the week cannot hold everything (#1083).
_DROP_RANK = {"walk": 0, "bike": 1, "row": 1, "other": 1}


def drop_priority(session: Any) -> int:
    """Lower is dropped sooner: walks, other easy sport, easy runs, strength,
    quality, and the long run last."""
    intent = session.intent
    if intent == "long":
        return 5
    if intent == "quality":
        return 4
    if intent == "strength":
        return 3
    if session.discipline == "run":
        return 2
    return _DROP_RANK.get(session.discipline, 1)


# Costs. Moving a session the runner was already shown outweighs any preference,
# so a recommendation only moves when it has to.
MOVE_COST = 100
PREFERENCE_COST = 10
STACK_COST = 1
# The same sport twice in a day (an easy run on the long run's day) costs more
# than plain stacking: it is rarely what a coach means, preference or not.
DOUBLE_SPORT_COST = 3

RECOMMEND_BUDGET = 40000

REASON_START_EASY = "Start easy and build up."
REASON_HARDEST_LAST = "Hardest last, then you're done."
REASON_DROPPED = "Doesn't fit in the days left this week, so it's dropped."


@dataclass
class Placement:
    session_id: Any
    day: date
    order: Optional[int] = None
    reason: Optional[str] = None


@dataclass
class Recommendation:
    placements: Dict[Any, Placement] = field(default_factory=dict)
    dropped: Dict[Any, str] = field(default_factory=dict)
    # Moves since the last recommendation, as (session_id, from_day, to_day).
    moved: List[Tuple[Any, date, date]] = field(default_factory=list)


@dataclass
class _Cand:
    session: Any
    days: List[date]
    prev: Optional[date]


def _cost(placed: Sequence[Tuple[Any, date]], fixed: Sequence[PlacedSession],
          prefs: set, prev: Dict[Any, date]) -> int:
    """The cost of a (partial) week; never decreases as sessions are added."""
    days: Dict[date, List[Any]] = {}
    for session, day in placed:
        days.setdefault(day, []).append(session)
    fixed_by_day: Dict[date, List[str]] = {}
    for p in fixed:
        fixed_by_day.setdefault(p.day, []).append(p.intent)

    cost = 0
    for session, day in placed:
        before = prev.get(session.id)
        if before is not None and before != day:
            cost += MOVE_COST
    for day, sessions in days.items():
        count = len(sessions) + len(fixed_by_day.get(day, []))
        # Each activity costs as many as were already on the day, so a busy
        # day always costs more to add to than a quiet one and the week evens out.
        cost += STACK_COST * count * (count - 1) // 2
        sports = Counter(s.discipline for s in sessions)
        cost += DOUBLE_SPORT_COST * sum(n - 1 for n in sports.values())

    def intents_on(day: date) -> List[str]:
        return [s.intent for s in days.get(day, [])] + fixed_by_day.get(day, [])

    def disciplines_on(day: date) -> List[str]:
        return [s.discipline for s in days.get(day, [])]

    if "spread_hard_days" in prefs:
        hard_days = sorted(d for d in set(days) | set(fixed_by_day)
                           for _ in range(sum(i in HARD for i in intents_on(d))))
        for a, b in zip(hard_days, hard_days[1:]):
            if (b - a).days <= 1:
                cost += PREFERENCE_COST
    if "easy_day_before_long" in prefs:
        for day in set(days) | set(fixed_by_day):
            if "long" in intents_on(day):
                eve = day - timedelta(days=1)
                cost += PREFERENCE_COST * sum(
                    i in ("quality", "strength", "long") for i in intents_on(eve)
                )
    if "strength_after_run" in prefs:
        for day, sessions in days.items():
            if any(s.intent == "strength" for s in sessions) and "run" not in disciplines_on(day):
                cost += PREFERENCE_COST
    if "spread_repeats" in prefs:
        for sessions in days.values():
            titles = Counter(s.title for s in sessions)
            cost += PREFERENCE_COST * sum(n - 1 for n in titles.values())
    return cost


def _best_week(cands: List[_Cand], rules, fixed, prefs, prev, today: date, budget: SearchBudget):
    """A legal week, then improved one move or swap at a time.

    Starts from the plain legal week the rule search finds, then repeatedly takes
    the first single move (one session to another of its days) or swap (two
    sessions trade days) that stays legal and lowers the cost, until none does or
    the budget runs out. Sessions and days are visited in a fixed order, so the
    same week always settles in the same place. A session's last recommended day
    pulls it back through the move cost, so an unchanged week stays put.
    """
    checkable = [r for r in rules if r.kind in PREDICATES]
    start = find_assignment([c.session for c in cands], rules, today, fixed=fixed, budget=budget)
    if start is None:
        return None
    by_id = {c.session.id: c for c in cands}
    day_of = {p.session_id: p.day for p in start}
    order = sorted(cands, key=lambda c: (str(c.session.title), str(c.session.id)))

    def week() -> List[Tuple[Any, date]]:
        return [(c.session, day_of[c.session.id]) for c in order]

    def legal() -> bool:
        placed = list(fixed) + [
            PlacedSession(session_id=c.session.id, intent=c.session.intent, day=day_of[c.session.id])
            for c in order
        ]
        return not violations_for(placed, checkable)

    def cost() -> int:
        return _cost(week(), fixed, prefs, prev)

    current = cost()
    try:
        improved = True
        while improved:
            improved = False
            for c in order:
                sid = c.session.id
                here = day_of[sid]
                for day in sorted(c.days, key=lambda d: (d != c.prev, d)):
                    if day == here:
                        continue
                    budget.spend()
                    day_of[sid] = day
                    if legal():
                        trial = cost()
                        if trial < current:
                            current, here, improved = trial, day, True
                            continue
                    day_of[sid] = here
            for a_index, a in enumerate(order):
                for b in order[a_index + 1:]:
                    da, db = day_of[a.session.id], day_of[b.session.id]
                    if da == db or db not in a.days or da not in b.days:
                        continue
                    budget.spend()
                    day_of[a.session.id], day_of[b.session.id] = db, da
                    if legal():
                        trial = cost()
                        if trial < current:
                            current, improved = trial, True
                            continue
                    day_of[a.session.id], day_of[b.session.id] = da, db
    except SearchBudgetExceeded:
        pass
    return [(by_id[sid].session, day) for sid, day in day_of.items()]


def _day_order(sessions: List[Any]) -> List[Tuple[Any, Optional[int], Optional[str]]]:
    """Each session of one day with its place in the order and the reason: built
    up from the easiest to the hardest, numbered when there is more than one."""
    ordered = sorted(sessions, key=lambda s: (drop_priority(s), str(s.title), str(s.id)))
    if len(ordered) < 2:
        return [(s, None, None) for s in ordered]
    last = len(ordered)
    return [
        (s, n, REASON_START_EASY if n == 1 else REASON_HARDEST_LAST if n == last else None)
        for n, s in enumerate(ordered, start=1)
    ]


def recommend_week(
    sessions: Sequence[Any],
    rules: Sequence[Any],
    preferences: Sequence[Any],
    today: date,
    *,
    fixed: Sequence[PlacedSession] = (),
    previous: Optional[Dict[Any, date]] = None,
    placements: int = RECOMMEND_BUDGET,
) -> Recommendation:
    """The recommended week for the committed sessions still to do.

    `sessions` are the open committed sessions (status upcoming); pinned ones
    have a domain of one day. `fixed` are done sessions and unplanned extras on
    their days. `previous` maps a session id to the day it was last recommended.
    """
    prefs = {getattr(p, "kind", p) for p in preferences}
    shown = {k: v for k, v in (previous or {}).items() if v is not None}
    # Only a day still to come can hold a session where it was; a day gone by
    # cannot, so a session recommended for it moves wherever it now fits.
    prev = {k: v for k, v in shown.items() if v >= today}
    cands = [
        _Cand(session=s, days=candidate_days(s, today), prev=prev.get(s.id))
        for s in sessions
    ]
    cands = [c for c in cands if c.days]
    result = Recommendation()
    budget = SearchBudget(placements)

    # 1. Drop the least important until what is left can be arranged at all.
    kept = sorted(cands, key=lambda c: (-drop_priority(c.session), str(c.session.id)))
    try:
        while kept and find_assignment(
            [c.session for c in kept], rules, today, fixed=fixed, budget=budget
        ) is None:
            gone = kept.pop()
            result.dropped[gone.session.id] = REASON_DROPPED
    except SearchBudgetExceeded:
        return result

    # 2. The cheapest legal week; the plain legal week when the budget runs out.
    week = _best_week(kept, rules, fixed, prefs, prev, today, budget)
    if week is None:
        try:
            plain = find_assignment(
                [c.session for c in kept], rules, today, fixed=fixed,
                budget=SearchBudget(placements),
            )
        except SearchBudgetExceeded:
            plain = None
        if plain is None:
            return result
        by_id = {c.session.id: c.session for c in kept}
        week = [(by_id[p.session_id], p.day) for p in plain]

    # 3. Order each day and say why.
    by_day: Dict[date, List[Any]] = {}
    for session, day in week:
        by_day.setdefault(day, []).append(session)
    for day, day_sessions in by_day.items():
        for session, order, reason in _day_order(day_sessions):
            result.placements[session.id] = Placement(
                session_id=session.id, day=day, order=order, reason=reason
            )
    # Every move since the runner was last shown the week, including a session
    # whose day passed without it being done.
    for session_id, before in sorted(shown.items(), key=lambda kv: (kv[1], str(kv[0]))):
        now = result.placements.get(session_id)
        if now is not None and now.day != before:
            result.moved.append((session_id, before, now.day))
    return result


def alternative_notes(session: Any, alternatives: Sequence[Any]) -> List[str]:
    """One fixed-wording note per alternative: what taking it instead gives or
    costs, measured against the session it stands in for."""
    notes = []
    main_s = getattr(session, "target_duration_s", None) or 0
    for alt in alternatives:
        parts = []
        if getattr(session, "discipline", None) == "run" and alt.discipline in ("bike", "row", "walk"):
            parts.append("Kinder on the legs")
        if getattr(session, "intent", None) == "strength" and alt.intent != "strength":
            parts.append("Easy cardio instead of strength")
        alt_s = alt.target_duration_s or 0
        if main_s and alt_s:
            minutes = round((alt_s - main_s) / 60)
            if minutes > 0:
                parts.append(f"+{minutes} min toward the week's time")
            elif minutes < 0:
                parts.append(f"{-minutes} min less toward the week's time")
        notes.append((", ".join(parts) + ".") if parts else "Same slot, another way to fill it.")
    return notes
