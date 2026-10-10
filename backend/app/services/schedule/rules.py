"""Spacing rules: the actual content of a flexible plan (#830).

"No quality run the day before the long run", "leave a full rest day after
Sunday" — for a runner whose sessions float, these ARE the plan. Buried in a chat
message they are prose nobody can check; here they are a closed vocabulary with a
pure predicate each, so a violation is DETECTABLE. That matters twice over: the
week can show the runner why a placement does not work, and the coach's generated
plan can be REJECTED for breaking its own rules before it is ever stored.

Why a constraint search rather than a lint
------------------------------------------
A flexible week does not have a placement to lint — most of its sessions have not
been placed yet. The honest question is therefore not "does this arrangement
break a rule" but "does a legal arrangement EXIST", which is a small constraint
satisfaction problem: each session's domain is the days in its window, and the
rules are the constraints. A week has at most seven days and a handful of
sessions, so a backtracking search with the standard smallest-domain-first
ordering settles it immediately.

Every predicate is MONOTONE — adding a session can only add violations, never
remove one — which is what makes it safe to test partial assignments during the
search and prune. That property is not incidental; a predicate that broke it
would make the pruning unsound, so keep it in mind before adding a kind.

Weekday numbers are Python's own (`date.weekday()`, 0 = Monday .. 6 = Sunday),
matching `services/weeks.py`. They are deliberately NOT relative to the runner's
chosen week start: "the long run wants a Saturday or Sunday" is a claim about
which days the runner has a free morning, not about where their week boundary
falls.
"""

import logging
from collections import Counter
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.schedule.placement import PlacedSession, candidate_days
from app.services.schedule.rule_text import describe_rule

logger = logging.getLogger(__name__)

# A predicate reports the first violation it finds as a runner-readable detail,
# or None when the rule holds for the placements it can see.
Predicate = Callable[[List[PlacedSession], Any], Optional[str]]


def _working_days(placed: Sequence[PlacedSession]) -> set:
    """Days carrying an actual session. A prescribed rest is not work, so a rest
    card on the day after the long run SATISFIES "a full rest day after"."""
    return {p.day for p in placed if p.intent != "rest"}


def _rest_day_after(placed: List[PlacedSession], rule: Any) -> Optional[str]:
    working = _working_days(placed)
    for p in placed:
        if p.intent != rule.intent:
            continue
        following = p.day + timedelta(days=1)
        if following in working:
            return (
                f"a session falls on {following.isoformat()}, the day after the "
                f"{rule.intent} session on {p.day.isoformat()}"
            )
    return None


def _no_intent_day_before(placed: List[PlacedSession], rule: Any) -> Optional[str]:
    target_days = {p.day for p in placed if p.intent == rule.target_intent}
    for p in placed:
        if p.intent != rule.before_intent:
            continue
        if p.day + timedelta(days=1) in target_days:
            return (
                f"the {rule.before_intent} session on {p.day.isoformat()} is the "
                f"day before a {rule.target_intent} session"
            )
    return None


def _min_days_between(placed: List[PlacedSession], rule: Any) -> Optional[str]:
    first = [p for p in placed if p.intent == rule.intent_a]
    second = [p for p in placed if p.intent == rule.intent_b]
    for x in first:
        for y in second:
            if x.session_id == y.session_id:
                continue
            gap = abs((x.day - y.day).days)
            if gap < rule.days:
                return (
                    f"{rule.intent_a} on {x.day.isoformat()} and {rule.intent_b} on "
                    f"{y.day.isoformat()} are {gap} day(s) apart, less than {rule.days}"
                )
    return None


def _preferred_days(placed: List[PlacedSession], rule: Any) -> Optional[str]:
    allowed = set(rule.weekdays or ())
    for p in placed:
        if p.intent == rule.intent and p.day.weekday() not in allowed:
            return (
                f"the {rule.intent} session falls on {p.day.isoformat()}, not one "
                f"of the days it needs"
            )
    return None


def _max_sessions_per_day(placed: List[PlacedSession], rule: Any) -> Optional[str]:
    counts = Counter(p.day for p in placed if p.intent != "rest")
    for day, total in counts.items():
        if total > rule.count:
            return (
                f"{total} sessions fall on {day.isoformat()}, more than the "
                f"{rule.count} allowed"
            )
    return None


PREDICATES: Dict[str, Predicate] = {
    "rest_day_after": _rest_day_after,
    "no_intent_day_before": _no_intent_day_before,
    "min_days_between": _min_days_between,
    "preferred_days": _preferred_days,
    "max_sessions_per_day": _max_sessions_per_day,
}

RULE_KINDS = tuple(PREDICATES)


def violations_for(
    placed: List[PlacedSession], rules: Sequence[Any]
) -> List[Dict[str, str]]:
    """Every rule the given concrete placement breaks."""
    found = []
    for rule in rules:
        predicate = PREDICATES.get(rule.kind)
        if predicate is None:
            # An unknown kind cannot be checked, so it cannot be enforced. It is
            # reported rather than ignored: silently passing a rule nobody can
            # evaluate is how a plan comes to claim a discipline it never had.
            found.append(
                {
                    "kind": rule.kind,
                    "label": getattr(rule, "label", rule.kind),
                    "statement": describe_rule(rule),
                    "detail": "this rule kind is not one the checker understands",
                }
            )
            continue
        detail = predicate(placed, rule)
        if detail is not None:
            found.append(
                {
                    "kind": rule.kind,
                    "label": getattr(rule, "label", rule.kind),
                    "statement": describe_rule(rule),
                    "detail": detail,
                }
            )
    return found


class SearchBudgetExceeded(Exception):
    """The search ran past its node budget without settling the question."""


class SearchBudget:
    """Placements a search may still try, shared across several searches."""

    def __init__(self, placements: int):
        self.left = placements

    def spend(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise SearchBudgetExceeded()


def find_assignment(
    sessions: Sequence[Any],
    rules: Sequence[Any],
    today: Any = None,
    *,
    fixed: Sequence[PlacedSession] = (),
    budget: Optional[SearchBudget] = None,
) -> Optional[List[PlacedSession]]:
    """One legal day per session, or None when the rules cannot all hold.

    Smallest domain first, and each partial assignment is tested against every
    rule whose predicate can already see a violation — sound because the
    predicates are monotone.

    `fixed` are sessions already on a day that cannot move — a session done on
    the day it was done (#1081). They take part in every rule but are never
    searched over, and they are not in the returned list. `budget` caps the
    number of placements tried; past it `SearchBudgetExceeded` is raised rather
    than a wrong answer returned.
    """
    checkable = [r for r in rules if r.kind in PREDICATES]
    fixed = list(fixed)

    domains: List[Tuple[Any, List]] = []
    for session in sessions:
        days = candidate_days(session, today)
        if not days:
            # No day left for this session: its window has lapsed, so it is a
            # MISSED session rather than a rule failure. It is skipped rather
            # than failing the search, because a session that cannot be moved
            # cannot be the reason the others do not fit — reporting it as one
            # would blame a rule for a week that has simply gone past.
            continue
        domains.append((session, days))

    # Smallest domain first, with interchangeable sessions side by side.
    # Every predicate reads only a placement's intent and day, never which
    # session it is, so two sessions with the same intent and the same days are
    # interchangeable: any legal week with them swapped is the same week. Trying
    # them in one order only (each on a day no earlier than its twin's) removes
    # that symmetry. Without it, a week that cannot fit (seven identical walks
    # against a tight daily limit) tried every ordering of the walks before
    # giving up, which ran for minutes (#1081).
    def twin_key(pair: Tuple[Any, List]) -> Tuple:
        return (pair[0].intent, tuple(pair[1]))

    domains.sort(key=lambda pair: (len(pair[1]), twin_key(pair)))
    placed: List[PlacedSession] = list(fixed)

    # The tightest "at most N a day", if any. Used to stop a branch as soon as
    # the sessions still to place cannot fit in the room left on their days,
    # rather than discovering it by trying every arrangement of them: a week
    # with more activities than the runner's limit allows fails at once.
    caps = [r.count for r in checkable if r.kind == "max_sessions_per_day" and r.count]
    cap = min(caps) if caps else None

    def room_for_the_rest(index: int) -> bool:
        if cap is None:
            return True
        rest = [(s, d) for s, d in domains[index:] if s.intent != "rest"]
        if not rest:
            return True
        used = Counter(p.day for p in placed if p.intent != "rest")
        days = set().union(*(set(d) for _, d in rest))
        return len(rest) <= sum(max(0, cap - used[day]) for day in days)

    def backtrack(index: int) -> bool:
        if index == len(domains):
            return True
        if not room_for_the_rest(index):
            return False
        session, days = domains[index]
        if index > 0 and twin_key(domains[index - 1]) == twin_key(domains[index]):
            floor = placed[-1].day
            days = [day for day in days if day >= floor]
        for day in days:
            if budget is not None:
                budget.spend()
            placed.append(
                PlacedSession(
                    session_id=getattr(session, "id", id(session)),
                    intent=session.intent,
                    day=day,
                )
            )
            if not violations_for(placed, checkable):
                if backtrack(index + 1):
                    return True
            placed.pop()
        return False

    return list(placed[len(fixed):]) if backtrack(0) else None


def check_rules(
    sessions: Sequence[Any],
    rules: Sequence[Any],
    today: Any = None,
    *,
    fixed: Sequence[PlacedSession] = (),
) -> Tuple[bool, List[Dict[str, str]]]:
    """(satisfiable, violations) for a week's sessions against its rules.

    When no legal week exists, each rule is dropped in turn to see which one
    unblocks it — that names the rule actually in the way rather than reporting
    the whole set. When no single removal helps, the rules are jointly
    impossible and all of them are reported, which is the truthful answer.
    """
    try:
        return _check_rules(sessions, rules, today, fixed, SearchBudget(CHECK_RULES_BUDGET))
    except SearchBudgetExceeded:
        # A week too tangled to settle in budget reports no violations rather
        # than hanging the request that asked. Logged, because it means the
        # search needs another pruning rule, not that the week is fine.
        logger.warning("schedule: rule check ran out of budget; reporting no violations")
        return True, []


# How many placements one rule check may try across all its searches.
CHECK_RULES_BUDGET = 60000


def _check_rules(sessions, rules, today, fixed, budget):
    rules = list(rules)
    sessions = list(sessions)

    unknown = [
        {
            "kind": r.kind,
            "label": getattr(r, "label", r.kind),
            "statement": describe_rule(r),
            "detail": "this rule kind is not one the checker understands",
        }
        for r in rules
        if r.kind not in PREDICATES
    ]

    if not sessions:
        return (not unknown), unknown

    if find_assignment(sessions, rules, today, fixed=fixed, budget=budget) is not None:
        return (not unknown), unknown

    implicated = []
    for index, rule in enumerate(rules):
        without = rules[:index] + rules[index + 1 :]
        if find_assignment(sessions, without, today, fixed=fixed, budget=budget) is not None:
            implicated.append(
                {
                    "kind": rule.kind,
                    "label": getattr(rule, "label", rule.kind),
                    "statement": describe_rule(rule),
                    "detail": (
                        "no arrangement of this week satisfies this rule alongside "
                        "the others"
                    ),
                }
            )

    if not implicated:
        implicated = [
            {
                "kind": r.kind,
                "label": getattr(r, "label", r.kind),
                "statement": describe_rule(r),
                "detail": "these rules cannot all hold for this week at once",
            }
            for r in rules
        ]

    return False, unknown + implicated


# How many placements one week's open-days question may try in total. A real
# week settles in a few hundred; the cap exists for the pathological one, where
# many identical sessions against a tight daily limit make a failing search
# explore every symmetric arrangement.
OPEN_DAYS_BUDGET = 20000


class _OnDay:
    """A session held to one candidate day, for asking "can it go here?"."""

    def __init__(self, session: Any, day: Any):
        self.id = getattr(session, "id", id(session))
        self.intent = session.intent
        self.window_start = day
        self.window_end = day


def open_days(
    sessions: Sequence[Any],
    rules: Sequence[Any],
    today: Any,
    *,
    fixed: Sequence[PlacedSession] = (),
    placements: int = OPEN_DAYS_BUDGET,
) -> Optional[Dict[Any, List]]:
    """For each session, the days it can still go on with the rest of the week
    still legal, or None when the question could not be settled in budget.

    A day is open for a session when a full legal arrangement exists with that
    session on it: every other session still finds a day, `fixed` sessions keep
    theirs, and every rule holds. This is the same search the plan is held to,
    asked once per candidate day, so the options shown can never be ones the
    checker would reject (#1081).

    A week that cannot be arranged at all leaves each session its own candidate
    days: the violation is reported separately, and showing nothing open would
    hide the sessions rather than explain the clash.
    """
    movable = [s for s in sessions if candidate_days(s, today)]
    budget = SearchBudget(placements)
    try:
        if find_assignment(movable, rules, today, fixed=fixed, budget=budget) is None:
            return {getattr(s, "id", id(s)): candidate_days(s, today) for s in movable}
        result: Dict[Any, List] = {}
        for session in movable:
            days = []
            for day in candidate_days(session, today):
                trial = [_OnDay(s, day) if s is session else s for s in movable]
                if find_assignment(trial, rules, today, fixed=fixed, budget=budget) is not None:
                    days.append(day)
            result[getattr(session, "id", id(session))] = days
        return result
    except SearchBudgetExceeded:
        return None
