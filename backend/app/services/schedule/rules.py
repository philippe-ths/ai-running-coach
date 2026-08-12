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

from collections import Counter
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.services.schedule.placement import PlacedSession, candidate_days

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
                    "detail": "this rule kind is not one the checker understands",
                }
            )
            continue
        detail = predicate(placed, rule)
        if detail is not None:
            found.append(
                {"kind": rule.kind, "label": getattr(rule, "label", rule.kind), "detail": detail}
            )
    return found


def find_assignment(
    sessions: Sequence[Any], rules: Sequence[Any], today: Any = None
) -> Optional[List[PlacedSession]]:
    """One legal day per session, or None when the rules cannot all hold.

    Smallest domain first, and each partial assignment is tested against every
    rule whose predicate can already see a violation — sound because the
    predicates are monotone.
    """
    checkable = [r for r in rules if r.kind in PREDICATES]

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

    domains.sort(key=lambda pair: len(pair[1]))
    placed: List[PlacedSession] = []

    def backtrack(index: int) -> bool:
        if index == len(domains):
            return True
        session, days = domains[index]
        for day in days:
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

    return list(placed) if backtrack(0) else None


def check_rules(
    sessions: Sequence[Any], rules: Sequence[Any], today: Any = None
) -> Tuple[bool, List[Dict[str, str]]]:
    """(satisfiable, violations) for a week's sessions against its rules.

    When no legal week exists, each rule is dropped in turn to see which one
    unblocks it — that names the rule actually in the way rather than reporting
    the whole set. When no single removal helps, the rules are jointly
    impossible and all of them are reported, which is the truthful answer.
    """
    rules = list(rules)
    sessions = list(sessions)

    unknown = [
        {
            "kind": r.kind,
            "label": getattr(r, "label", r.kind),
            "detail": "this rule kind is not one the checker understands",
        }
        for r in rules
        if r.kind not in PREDICATES
    ]

    if not sessions:
        return (not unknown), unknown

    if find_assignment(sessions, rules, today) is not None:
        return (not unknown), unknown

    implicated = []
    for index, rule in enumerate(rules):
        without = rules[:index] + rules[index + 1 :]
        if find_assignment(sessions, without, today) is not None:
            implicated.append(
                {
                    "kind": rule.kind,
                    "label": getattr(rule, "label", rule.kind),
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
                "detail": "these rules cannot all hold for this week at once",
            }
            for r in rules
        ]

    return False, unknown + implicated
