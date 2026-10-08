"""Deterministic repair of a week's numeric shortfalls (#1064).

The coach gets one retry with the exact gaps. If the week is still short, the
shortfall is arithmetic: a few more minutes of the activity that closes the gap
fastest for THIS runner. Code can close that without a model, so it does, and says
exactly what it changed. What it will not do is invent: every added minute is a
lengthening of a session the coach wrote or a copy of a session the runner really
does, at the runner's own pace.

What it repairs, and what it does not
-------------------------------------
Only the NUMERIC failures, the challenge threshold and the walking floor. A
structural failure, a broken spacing rule or a missing goal day is the coach's to
fix, never code's: moving a race is not a repair.

How
---
1. Lengthen the week's committed EASY sessions of the best activity by up to half
   again each, scaling distance with time so the ratio the coach wrote (its pace)
   is kept.
2. If still short, add easy sessions of that activity, copied from the runner's own
   typical session (or the week's median walk, for walking), at most an hour and a
   half each, on the days with the fewest sessions, never on a dated goal's day or
   the day before it, and only where the plan's spacing rules still hold.
3. Stop at the absurdity ceilings. Never past them: what is left is a shortfall
   that is stored and shown rather than a repair that makes the week absurd.

Pure: no I/O, no model.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from app.schemas.season import ChallengeRule
from app.services.schedule.draft_contract import MAX_SESSIONS_PER_WEEK, DraftedSession, DraftedWeek
from app.services.schedule.frames import WeekFrame
from app.services.schedule.plan_validator import _ImplicitRule, _Placeable
from app.services.schedule.planned_distance import planned_distance_m
from app.services.schedule.rules import check_rules
from app.services.schedule.week_check import (
    NUMERIC_CODES,
    planned_metrics,
    rule_value,
)

# How far an existing easy session may be stretched: half again.
MAX_LENGTHEN = 0.5
# The longest session repair will add.
MAX_ADDED_S = 5400.0
# Shorter than this is not worth a session of its own.
MIN_ADDED_S = 1200.0
# Under this much room under a ceiling, repair stops rather than add a sliver.
MIN_ROOM_S = 900.0
MAX_ADDITIONS = 7

_NOUN = {
    "run": "run",
    "bike": "ride",
    "walk": "walk",
    "row": "row",
    "strength": "strength session",
    "other": "session",
}


@dataclass
class Repair:
    sessions: List[DraftedSession]
    notes: List[str] = field(default_factory=list)


def _weekday(session: Any) -> str:
    if session.window_start == session.window_end:
        return f"{session.window_start:%A}'s"
    return "the"


def _label(session: Any) -> str:
    return f"{_weekday(session)} {session.title.lower()}"


def _threshold_text(rule: ChallengeRule) -> str:
    if rule.metric == "zone_time_s":
        return f"{rule.at_least / 3600:g} h zone {rule.min_zone}+"
    if rule.metric == "time_s":
        return f"{rule.at_least / 3600:g} h"
    if rule.metric == "distance_m":
        return f"{rule.at_least / 1000:g} km"
    return f"{rule.at_least:g}-session"


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


class _Week:
    """The week being repaired: editable sessions beside the ones that only count."""

    def __init__(
        self,
        frame: WeekFrame,
        sessions: Sequence[DraftedSession],
        fixed: Sequence[Any],
        rules: Sequence[Any],
    ) -> None:
        self.frame = frame
        self.sessions: List[DraftedSession] = list(sessions)
        self.fixed = list(fixed)
        self.rules = list(rules)
        self.notes: List[str] = []
        # Goal days and the day before each: nothing is lengthened or added there.
        # The frame knows every dated goal, including one on the NEXT week's first
        # day, whose eve is this week's last.
        self.protected = set(frame.protected_days)
        for goal in frame.dated:
            self.protected.add(goal.day)
            self.protected.add(goal.day - timedelta(days=1))

    def everything(self) -> List[Any]:
        return self.sessions + self.fixed

    def committed_time_s(self) -> float:
        return float(
            sum(
                s.target_duration_s or 0
                for s in self.everything()
                if getattr(s, "commitment", "committed") == "committed"
            )
        )

    def committed_run_m(self) -> float:
        return sum(
            planned_distance_m(s)
            for s in self.everything()
            if s.discipline == "run" and getattr(s, "commitment", "committed") == "committed"
        )

    def time_room_s(self) -> float:
        ceiling = self.frame.hours_ceiling_s
        return float("inf") if ceiling is None else ceiling - self.committed_time_s()

    def run_room_m(self) -> float:
        ceiling = self.frame.run_ceiling_m
        return float("inf") if ceiling is None else ceiling - self.committed_run_m()

    def metrics(self):
        return planned_metrics(self.everything(), self.frame)

    def editable(self, discipline: str) -> List[int]:
        """Indexes of this week's easy committed sessions of `discipline` that may
        be stretched."""
        out = []
        for i, s in enumerate(self.sessions):
            if (
                s.discipline == discipline
                and s.intent == "easy"
                and s.commitment == "committed"
                and s.window_end >= self.frame.today
                and not (s.window_start == s.window_end and s.window_start in self.protected)
                and (s.target_duration_s or 0) > 0
            ):
                out.append(i)
        return out

    # --- stretching ----------------------------------------------------

    def stretch(self, i: int, ratio: float) -> float:
        """Grow session `i` by `ratio` of its size (time and distance together),
        within the ceilings. Returns the seconds actually added."""
        s = self.sessions[i]
        seconds = float(s.target_duration_s or 0)
        ratio = min(ratio, MAX_LENGTHEN)
        add_s = min(seconds * ratio, self.time_room_s())
        if s.discipline == "run" and s.target_distance_m:
            run_room = self.run_room_m()
            if s.target_distance_m * (add_s / seconds) > run_room:
                add_s = max(0.0, run_room / s.target_distance_m * seconds)
        if add_s < 60:
            return 0.0
        factor = (seconds + add_s) / seconds
        update: Dict[str, Any] = {"target_duration_s": int(round(seconds + add_s))}
        if s.target_distance_m:
            update["target_distance_m"] = round(s.target_distance_m * factor, 1)
        self.sessions[i] = s.model_copy(update=update)
        return add_s

    # --- adding --------------------------------------------------------

    def _occupancy(self, day: date) -> float:
        """How many sessions the day carries, a floating one counted fractionally."""
        total = 0.0
        for s in self.everything():
            if getattr(s, "commitment", "committed") != "committed" or s.intent == "rest":
                continue
            width = (s.window_end - s.window_start).days + 1
            if s.window_start <= day <= s.window_end:
                total += 1.0 / width
        return total

    def _rules_hold_with(self, extra: DraftedSession) -> bool:
        placeable = [
            _Placeable(
                id=f"k{n}", intent=s.intent,
                window_start=s.window_start, window_end=s.window_end,
            )
            for n, s in enumerate(self.everything())
            if getattr(s, "commitment", "committed") == "committed"
        ] + [
            _Placeable(
                id="new", intent=extra.intent,
                window_start=extra.window_start, window_end=extra.window_end,
            )
        ]
        ok, _ = check_rules(placeable, self.rules + [_ImplicitRule()], None)
        return ok

    def add(
        self,
        discipline: str,
        seconds: float,
        *,
        distance_m: Optional[float],
        reason: str,
    ) -> Optional[float]:
        """Add one easy session. Returns the seconds added, or None when no legal
        day or no room is left."""
        if len(self.everything()) >= MAX_SESSIONS_PER_WEEK:
            return None
        seconds = min(seconds, self.time_room_s())
        if distance_m and discipline == "run":
            run_room = self.run_room_m()
            if distance_m > run_room:
                seconds *= max(0.0, run_room / distance_m)
                distance_m = max(0.0, run_room)
        if seconds < MIN_ROOM_S:
            return None
        days = [
            self.frame.week_start + timedelta(days=n)
            for n in range(7)
            if self.frame.week_start + timedelta(days=n) >= self.frame.today
            and self.frame.week_start + timedelta(days=n) not in self.protected
        ]
        if not days:
            return None
        noun = _NOUN.get(discipline, "session")
        title = "Strength" if discipline == "strength" else f"Easy {noun}"
        if discipline == "walk":
            title = "Easy walk"
        for day in sorted(days, key=lambda d: (self._occupancy(d), d)):
            candidate = DraftedSession(
                window_start=day,
                window_end=day,
                intent="strength" if discipline == "strength" else "easy",
                discipline=discipline,
                commitment="committed",
                title=title,
                detail=f"Added to hold the week's target: {reason}.",
                target_distance_m=round(distance_m, 1) if distance_m else None,
                target_duration_s=int(round(seconds)),
            )
            if self._rules_hold_with(candidate):
                self.sessions.append(candidate)
                self.notes.append(
                    f"Added a {_minutes(seconds)} min {title.lower()} on "
                    f"{day:%A} to hold {reason}."
                )
                return seconds
        return None


def _typical_for(week: _Week, discipline: str) -> Optional[tuple]:
    """(duration_s, distance_m or None) of the runner's own typical easy session."""
    t = week.frame.typical.get(discipline)
    if t is None:
        return None
    return t.duration_s, t.distance_m


def _week_median_walk(week: _Week) -> Optional[tuple]:
    walks = sorted(
        (
            (float(s.target_duration_s), planned_distance_m(s))
            for s in week.everything()
            if s.discipline == "walk"
            and getattr(s, "commitment", "committed") == "committed"
            and (s.target_duration_s or 0) > 0
            and planned_distance_m(s) > 0
        ),
        key=lambda pair: pair[1],
    )
    if not walks:
        return None
    return walks[len(walks) // 2]


# --- the two numeric repairs --------------------------------------------------


def _close_walking(week: _Week) -> None:
    frame = week.frame
    if frame.walking_floor_m is None:
        return
    needed = frame.walking_floor_m * frame.share_left

    def walked() -> float:
        return week.metrics().distance_m.get("walk", 0.0)

    # 1. stretch the walks the week already holds
    for i in week.editable("walk"):
        gap = needed - walked()
        if gap <= 10:
            return
        s = week.sessions[i]
        dist = planned_distance_m(s)
        if dist <= 0:
            continue
        ratio = min(MAX_LENGTHEN, gap / dist)
        before = s.target_duration_s or 0
        added = week.stretch(i, ratio)
        if added:
            week.notes.append(
                f"Lengthened {_label(s)} by {_minutes(added)} min to keep this "
                f"runner's usual walking ({frame.usual_distance_m('walk') / 1000:.0f} km a week)."
            )
    # 2. add walks copied from the week's median walk, else the runner's typical
    for _ in range(MAX_ADDITIONS):
        gap = needed - walked()
        if gap <= 10:
            return
        base = _week_median_walk(week) or _typical_for(week, "walk")
        if base is None or not base[1]:
            return
        seconds, dist = base
        scale = min(1.0, max(gap / dist, 0.4))
        if week.add(
            "walk", seconds * scale, distance_m=dist * scale,
            reason="this runner's usual walking",
        ) is None:
            return


def _disciplines_for(week: _Week, c, metric: str) -> List[str]:
    """The activities that may close the gap, best first."""
    rule = c.rule
    frame = week.frame
    allowed = set(rule.disciplines)
    pool = [d for d in ("run", "bike", "walk", "row", "other", "strength")
            if not allowed or d in allowed]
    if metric == "zone_time_s":
        zone = rule.min_zone or 1
        usable = [d for d in pool if frame.share(zone, d) > 0]
        return sorted(
            usable, key=lambda d: (-frame.share(zone, d), -frame.usual_time_s(d))
        )
    if metric == "distance_m":
        return sorted(
            [d for d in pool if d in ("run", "walk")],
            key=lambda d: -frame.usual_distance_m(d),
        )
    # time and sessions: what they already do most
    return sorted(pool, key=lambda d: -frame.usual_time_s(d))


def _close_challenge(week: _Week, c) -> None:
    rule = c.rule
    frame = week.frame
    reason = f"the {_threshold_text(rule)} week"

    def gap() -> float:
        return rule.at_least - rule_value(rule, week.metrics())

    tolerance = {"zone_time_s": 60.0, "time_s": 60.0, "distance_m": 10.0, "sessions": 0.0}[rule.metric]
    if gap() <= tolerance:
        return

    for discipline in _disciplines_for(week, c, rule.metric):
        share = frame.share(rule.min_zone or 1, discipline) if rule.metric == "zone_time_s" else 1.0

        def need_s() -> float:
            if rule.metric == "distance_m":
                pace = frame.usual_pace_s_per_m(discipline)
                return gap() * pace if pace else 0.0
            return gap() / share if share > 0 else 0.0

        if rule.metric == "sessions":
            _add_sessions(week, c, discipline, reason)
            if gap() <= tolerance:
                return
            continue

        # 1. stretch what the coach wrote
        for i in week.editable(discipline):
            if gap() <= tolerance:
                return
            s = week.sessions[i]
            seconds = float(s.target_duration_s or 0)
            if rule.metric == "distance_m" and not s.target_distance_m:
                continue
            ratio = min(MAX_LENGTHEN, need_s() / seconds) if seconds else 0
            if ratio <= 0:
                continue
            added = week.stretch(i, ratio)
            if added:
                week.notes.append(
                    f"Lengthened {_label(s)} by {_minutes(added)} min to hold "
                    f"{reason}."
                )
        # 2. add easy sessions copied from the runner's own typical one
        for _ in range(MAX_ADDITIONS):
            if gap() <= tolerance:
                return
            base = _typical_for(week, discipline)
            if base is None:
                break
            t_seconds, t_dist = base
            want = need_s()
            if want <= 0:
                break
            seconds = max(MIN_ADDED_S, min(MAX_ADDED_S, want))
            dist = t_dist * (seconds / t_seconds) if t_dist and t_seconds else None
            if week.add(discipline, seconds, distance_m=dist, reason=reason) is None:
                break


def _add_sessions(week: _Week, c, discipline: str, reason: str) -> None:
    rule = c.rule
    base = _typical_for(week, discipline)
    if base is None:
        return
    seconds, dist = base
    for _ in range(MAX_ADDITIONS):
        if rule_value(rule, week.metrics()) >= rule.at_least:
            return
        if week.add(
            discipline, min(seconds, MAX_ADDED_S), distance_m=dist, reason=reason
        ) is None:
            return


def repair_week(
    frame: WeekFrame,
    sessions: Sequence[DraftedSession],
    *,
    fixed: Sequence[Any] = (),
    rules: Sequence[Any] = (),
) -> Repair:
    """Close what a week is numerically short of, as far as the ceilings allow.

    `sessions` are the coach's (editable); `fixed` are sessions that count towards
    the week but are not the coach's to change here (an amendment's surviving
    rows). The caller re-runs the check afterwards: whatever is still short is a
    shortfall, never silently accepted.
    """
    week = _Week(frame, sessions, fixed, rules)
    _close_walking(week)
    for c in frame.challenges:
        _close_challenge(week, c)
    return Repair(sessions=week.sessions, notes=week.notes)


def repair_weeks(
    weeks: Sequence[DraftedWeek],
    check: Any,
    frames_by_week: Dict[date, WeekFrame],
    *,
    rules: Sequence[Any] = (),
    fixed_by_week: Optional[Dict[date, Sequence[Any]]] = None,
) -> tuple:
    """Repair every week `check` found numerically short; leave the rest alone.

    Returns `(weeks, notes)`. `fixed_by_week` are the sessions a week keeps that are
    not the coach's to change (an amendment's surviving rows).
    """
    numeric_weeks = {f.week_start for f in check.week_failures if f.code in NUMERIC_CODES}
    out: List[DraftedWeek] = []
    notes: List[str] = []
    for week in weeks:
        frame = frames_by_week.get(week.week_start)
        if frame is None or week.week_start not in numeric_weeks:
            out.append(week)
            continue
        result = repair_week(
            frame, week.sessions, rules=rules,
            fixed=(fixed_by_week or {}).get(week.week_start, ()),
        )
        notes.extend(result.notes)
        out.append(week.model_copy(update={"sessions": result.sessions}))
    return out, notes
