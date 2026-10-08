"""Week frames: what the season asks of each week, as arithmetic (#1064).

The season is the coach's OPINION: phases with targets, a date for each goal, a
challenge rule. A frame is what that opinion comes to for ONE week, worked out in
code so the model that writes the week is told plainly what the week must hold
and the check that follows judges the week against exactly that. Nothing here
calls a model, reads a database or estimates a zone: it is a pure function of the
season, the goals, the runner's own recent facts and today's date.

What a frame carries
--------------------
- the phase covering most of the week, and its targets INTERPOLATED for the week;
- the dated goals that fall in it (a recommended date counts: the season has
  chosen it, so the plan holds it);
- every challenge covering it, as week n of N with its threshold;
- the runner's usual week by activity, their zone shares, the walking floor and the
  absurdity ceilings;
- for the CURRENT week, what the runner has already done, MEASURED, because a
  challenge week counts what was done plus what is planned.

Planned zone time is an ESTIMATE (duration x the runner's share for that
activity); done zone time is MEASURED (heart-rate data). The names keep the two
apart wherever they surface.

Interpolation
-------------
A phase's targets are where the phase ENDS. For each target the knots are the
runner's current level just before the first phase and each phase's end; a week
takes the value on the line between the two knots around it, so a build climbs a
step a week instead of jumping and a taper falls the same way. A phase that gives
no value for a target contributes no knot (the line runs through it), and past the
last knot the value holds.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.schemas.season import ChallengeRule, SeasonPhase, SeasonPlan
from app.services.schedule import goals as goal_text
from app.services.schedule.prompt_text import (
    APPROACH_MAX,
    FOCUS_MAX,
    SUCCESS_MAX,
    SUMMARY_MAX,
    flat,
)
from app.services.schedule.norms import (
    DisciplineNorm,
    TypicalSession,
    WeekActuals,
    longest_recent_run_m,
    running_norm_weekly_m,
    typical_sessions,
    weekly_actuals,
    weekly_hours_norm_s,
    weekly_norms_by_discipline,
    zone_shares,
)
from app.services.schedule.plan_validator import hours_ceilings, volume_ceilings
from app.services.weeks import week_start as _week_start

# Their walking is part of their life rather than load to periodise (a dog does
# not taper), so a plan keeps all of it every week. Below a few km a week it is
# not a habit the plan has to carry.
WALKING_FLOOR_MULTIPLE = 1.0

# A challenge's planned figure is an ESTIMATE from the runner's own shares, and
# the rule is judged on what the watch records. A plan aimed exactly at the
# threshold misses about half its weeks in reality, so the plan aims a little
# over it. The check still holds the rule itself.
CHALLENGE_PLANNING_MARGIN = 1.05
MATERIAL_WALKING_M = 5000.0

# A dated goal of these kinds is a day the week must hold.
DATED_KINDS = ("race", "finish", "completion")

# The zone the season's challenge shares default to when no challenge names one.
DEFAULT_MIN_ZONE = 2


def walking_floor_m(usual_walking_m: Optional[float]) -> Optional[float]:
    """The committed walking a whole week must keep, or None when walking is not
    a material part of this runner's week."""
    if not usual_walking_m or usual_walking_m < MATERIAL_WALKING_M:
        return None
    return usual_walking_m * WALKING_FLOOR_MULTIPLE


def share_of_week_left(week_start: date, today: date) -> float:
    """How much of a week is still ahead: 1.0 for a future week, 4/7 on a
    Thursday of a Monday week, today counted."""
    first = max(week_start, today)
    days = (week_start + timedelta(days=6) - first).days + 1
    return max(0, min(days, 7)) / 7


@dataclass(frozen=True)
class DatedGoalFrame:
    goal_id: Any
    name: str
    kind: str
    day: date
    distance_m: Optional[float] = None
    booked: bool = False


@dataclass(frozen=True)
class ChallengeFrame:
    goal_id: Any
    name: str
    rule: ChallengeRule
    index: int  # 1-based, "week 4 of 10"

    @property
    def weeks(self) -> int:
        return self.rule.weeks

    @property
    def threshold(self) -> float:
        return self.rule.at_least

    @property
    def plan_for(self) -> float:
        """What to plan: over the threshold for a time metric, whose planned
        figure is an estimate; the threshold itself for distance and sessions."""
        if self.rule.metric in ("zone_time_s", "time_s"):
            return self.rule.at_least * CHALLENGE_PLANNING_MARGIN
        return self.rule.at_least


@dataclass(frozen=True)
class Targets:
    """The coach's targets for one week, interpolated. Seconds and metres."""

    weekly_hours_s: Optional[float] = None
    run_m: Optional[float] = None
    long_run_m: Optional[float] = None


@dataclass
class WeekFrame:
    week_start: date
    today: date
    is_current: bool
    share_left: float
    phase: Optional[SeasonPhase] = None
    phase_goal: Optional[str] = None
    targets: Targets = field(default_factory=Targets)
    dated: List[DatedGoalFrame] = field(default_factory=list)
    challenges: List[ChallengeFrame] = field(default_factory=list)
    usual: Dict[str, DisciplineNorm] = field(default_factory=dict)
    # min_zone -> discipline -> share of heart-rate time at or above that zone.
    shares: Dict[int, Dict[str, float]] = field(default_factory=dict)
    typical: Dict[str, TypicalSession] = field(default_factory=dict)
    walking_floor_m: Optional[float] = None
    # The absurdity ceilings the plan validator holds: a concrete week's running
    # and time, and the looser pair for a week that is only a shape.
    run_ceiling_m: Optional[float] = None
    hours_ceiling_s: Optional[float] = None
    sketch_run_ceiling_m: Optional[float] = None
    sketch_hours_ceiling_s: Optional[float] = None
    # Measured, current week only: what was done on the days before today.
    done: Optional[WeekActuals] = None
    # Every dated goal's day and the day before it, in ANY week: a race on the
    # next Monday makes this week's Sunday its eve, and a repair of this week must
    # not put a session there although the race is in another frame.
    protected_days: frozenset = frozenset()

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)

    @property
    def usual_total_s(self) -> float:
        return sum(n.moving_time_s for n in self.usual.values())

    def usual_time_s(self, discipline: str) -> float:
        norm = self.usual.get(discipline)
        return norm.moving_time_s if norm is not None else 0.0

    def usual_distance_m(self, discipline: str) -> float:
        norm = self.usual.get(discipline)
        return norm.distance_m if norm is not None else 0.0

    def share(self, min_zone: int, discipline: str) -> float:
        return self.shares.get(min_zone, {}).get(discipline, 0.0)

    def usual_pace_s_per_m(self, discipline: str) -> Optional[float]:
        """The runner's OWN usual pace for a discipline (seconds per metre), or
        None when they do too little of it to say. Never a population figure: a
        time derived from a distance with anyone else's pace is a guess drawn as
        a plan."""
        norm = self.usual.get(discipline)
        if norm is None or norm.distance_m < 500 or norm.moving_time_s <= 0:
            return None
        return norm.moving_time_s / norm.distance_m


# --- interpolation -----------------------------------------------------------


def _interpolate(knots: List[Tuple[date, float]], day: date) -> Optional[float]:
    """The value on the line through `knots` (sorted by date) at `day`; held flat
    before the first knot and after the last."""
    if not knots:
        return None
    if day <= knots[0][0]:
        return knots[0][1]
    for (d0, v0), (d1, v1) in zip(knots, knots[1:]):
        if day <= d1:
            span = (d1 - d0).days
            if span <= 0:
                return v1
            return v0 + (v1 - v0) * ((day - d0).days / span)
    return knots[-1][1]


def _knots(
    phases: Sequence[SeasonPhase], attr: str, current: Optional[float], scale: float
) -> List[Tuple[date, float]]:
    ends = [
        (phase.end, getattr(phase, attr) * scale)
        for phase in phases
        if getattr(phase, attr) is not None
    ]
    if not ends:
        # No phase states this target: it is unknown, not the runner's current
        # level held flat, which would be a target nobody set.
        return []
    knots: List[Tuple[date, float]] = []
    if current is not None:
        knots.append((phases[0].start - timedelta(days=1), current))
    return knots + ends


def _majority_phase(
    phases: Sequence[SeasonPhase], start: date
) -> Optional[SeasonPhase]:
    """The phase covering most of the week starting `start`; the earlier on a tie."""
    best, best_days = None, 0
    for phase in phases:
        overlap = (
            min(phase.end, start + timedelta(days=6)) - max(phase.start, start)
        ).days + 1
        if overlap > best_days:
            best, best_days = phase, overlap
    return best


# --- building ----------------------------------------------------------------


def _goal_by_id(goals: Sequence[Any]) -> Dict[Any, Any]:
    return {g.id: g for g in goals}


def build_frames(
    *,
    season: Optional[SeasonPlan],
    goals: Sequence[Any],
    facts: Sequence[Any],
    starts_on: int,
    today: date,
    horizon_weeks: int,
) -> List[WeekFrame]:
    """One frame per horizon week, the current week first.

    `season` may be None (no season yet): frames then carry only what needs no
    opinion, the usual week, the zone shares, the walking floor and the ceilings.
    """
    facts = list(facts)
    this_week = _week_start(today, starts_on)
    by_id = _goal_by_id(goals)
    phases = sorted(season.phases, key=lambda p: (p.start, p.end)) if season else []

    usual_list = weekly_norms_by_discipline(facts, today)
    usual = {n.discipline: n for n in usual_list}
    norm_s = weekly_hours_norm_s(facts, today)
    norm_run_m = running_norm_weekly_m(facts, today)
    zones = {DEFAULT_MIN_ZONE}
    if season is not None:
        zones.update(
            rule.min_zone for _, rule in season.challenges() if rule.min_zone is not None
        )
    shares = {zone: zone_shares(facts, today, zone) for zone in zones}
    typical = typical_sessions(facts, today)
    walking_m = usual["walk"].distance_m if "walk" in usual else None
    run_ceilings = volume_ceilings(norm_run_m)
    hours = hours_ceilings(norm_s)

    hour_knots = _knots(phases, "weekly_hours", norm_s, 3600.0)
    run_knots = _knots(phases, "run_km", norm_run_m, 1000.0)
    long_knots = _knots(phases, "long_run_km", longest_recent_run_m(facts, today), 1000.0)

    challenges = season.challenges() if season else []
    dated_views = (
        [v for v in season.goals if v.kind in DATED_KINDS and v.date is not None]
        if season
        else []
    )

    protected_days = frozenset(
        day
        for view in dated_views
        for day in (view.date, view.date - timedelta(days=1))
    )

    frames: List[WeekFrame] = []
    for index in range(horizon_weeks):
        start = this_week + timedelta(weeks=index)
        end = start + timedelta(days=6)
        phase = _majority_phase(phases, start)
        targets = Targets()
        if phase is not None:
            at = min(end, phase.end)
            targets = Targets(
                weekly_hours_s=_interpolate(hour_knots, at),
                run_m=_interpolate(run_knots, at),
                long_run_m=_interpolate(long_knots, at),
            )
        phase_goal = None
        if phase is not None and phase.goal_id is not None and phase.goal_id in by_id:
            phase_goal = by_id[phase.goal_id].name

        dated = []
        for view in dated_views:
            if start <= view.date <= end:
                goal = by_id.get(view.goal_id)
                dated.append(
                    DatedGoalFrame(
                        goal_id=view.goal_id,
                        name=goal.name if goal is not None else "Goal",
                        kind=view.kind,
                        day=view.date,
                        distance_m=getattr(goal, "distance_m", None),
                        booked=bool(getattr(goal, "booked", False)),
                    )
                )
        dated.sort(key=lambda d: d.day)

        covering = [
            ChallengeFrame(
                goal_id=goal_id,
                name=by_id[goal_id].name if goal_id in by_id else "Challenge",
                rule=rule,
                index=(start - rule.start).days // 7 + 1,
            )
            for goal_id, rule in challenges
            if rule.covers(start)
        ]

        is_current = index == 0
        done = None
        if is_current:
            done = weekly_actuals(
                facts, start, starts_on, through=today - timedelta(days=1)
            )
        frames.append(
            WeekFrame(
                week_start=start,
                today=today,
                is_current=is_current,
                share_left=share_of_week_left(start, today),
                phase=phase,
                phase_goal=phase_goal,
                targets=targets,
                dated=dated,
                challenges=covering,
                usual=usual,
                shares=shares,
                typical=typical,
                walking_floor_m=walking_floor_m(walking_m),
                run_ceiling_m=run_ceilings[0] if run_ceilings else None,
                hours_ceiling_s=hours[0] if hours else None,
                sketch_run_ceiling_m=run_ceilings[1] if run_ceilings else None,
                sketch_hours_ceiling_s=hours[1] if hours else None,
                done=done,
                protected_days=protected_days,
            )
        )
    return frames


# --- saying a frame in words -------------------------------------------------


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def _day(d: date) -> str:
    return f"{d:%a} {d.isoformat()}"


def phase_label(phase: Optional[SeasonPhase]) -> Optional[str]:
    return phase.kind.capitalize() if phase is not None else None


def zone_name(min_zone: Optional[int]) -> str:
    return f"zone {min_zone} or above"


def describe_challenge(c: ChallengeFrame) -> str:
    """The rule in the runner's words ("10.0 h of zone 2 or above")."""
    rule = c.rule
    if rule.metric == "zone_time_s":
        amount = f"{_hours(rule.at_least)} in {zone_name(rule.min_zone)}"
    elif rule.metric == "time_s":
        amount = f"{_hours(rule.at_least)} of moving time"
    elif rule.metric == "distance_m":
        amount = f"{rule.at_least / 1000:.1f} km"
    else:
        amount = f"{rule.at_least:.0f} sessions"
    only = f", {' and '.join(rule.disciplines)} only" if rule.disciplines else ", any activity"
    return f"{amount}{only}"


def describe_frame(frame: WeekFrame) -> List[str]:
    """One concrete week stated plainly, for the drafting prompt."""
    lines: List[str] = []
    header = f"### Week of {_day(frame.week_start)} to {_day(frame.week_end)}"
    if frame.is_current:
        header += (
            f" (this week: {frame.share_left * 7:.0f} of 7 days left, plan only "
            f"{frame.today.isoformat()} onward)"
        )
    lines.append(header)
    if frame.phase is not None:
        label = f"- Phase: {frame.phase.kind}"
        if frame.phase.focus:
            label += f" ({flat(frame.phase.focus, FOCUS_MAX)})"
        if frame.phase_goal:
            label += f", towards {frame.phase_goal}"
        lines.append(label)
    t = frame.targets
    bits = []
    if t.weekly_hours_s:
        bits.append(f"about {_hours(t.weekly_hours_s)} in all")
    if t.run_m:
        bits.append(f"{t.run_m / 1000:.0f} km of running")
    if t.long_run_m:
        bits.append(f"a long run of about {t.long_run_m / 1000:.0f} km")
    if bits:
        lines.append(
            "- The season's targets for this week: " + ", ".join(bits)
            + ". The targets are your own, where the phases put this week."
        )
    for goal in frame.dated:
        distance = f", {goal.distance_m / 1000:g} km" if goal.distance_m else ""
        status = "booked" if goal.booked else "the date the season recommends"
        need = "Pin a committed run or walk to that day"
        if goal.distance_m:
            need += f" covering at least {goal.distance_m * 0.9 / 1000:.1f} km"
        if goal.kind == "race":
            need += ' (intent "quality" or "long", with "race" in the title)'
        lines.append(
            f"- {goal.name} is on {_day(goal.day)}{distance} ({status}). {need}, and "
            "plan the days around it: nothing hard the day before, an easy day after."
        )
    for c in frame.challenges:
        spec_note = ""
        if c.rule.metric == "zone_time_s":
            zone = c.rule.min_zone or DEFAULT_MIN_ZONE
            shares = frame.shares.get(zone, {})
            parts = [
                f"{d} {shares[d]:.0%}"
                for d in sorted(shares, key=lambda d: -shares[d])
                if not c.rule.disciplines or d in c.rule.disciplines
            ]
            spec_note = (
                " Planned time is counted at this runner's own share of each "
                f"activity spent in {zone_name(zone)} (" + ", ".join(parts)
                + "), and an activity not listed counts as none."
                if parts
                else " This runner has no heart-rate shares, so planned time cannot be counted."
            )
        lines.append(
            f'- Challenge "{c.name}", week {c.index} of {c.weeks}: this week must hold '
            f"at least {describe_challenge(c)}"
            f"{', and that includes this race week' if frame.dated else ''}. A week "
            f"that falls short is rejected."
            + (
                f" Plan about {c.plan_for / 3600:.1f} h: the planned figure is an "
                "estimate and the rule counts what their watch records."
                if c.rule.metric in ("zone_time_s", "time_s") else ""
            )
            + spec_note
        )
    if frame.done is not None and (frame.done.total_time_s > 0):
        done_bits = [f"{_hours(frame.done.total_time_s)} moving"]
        for c in frame.challenges:
            if c.rule.metric == "zone_time_s":
                done_bits.append(
                    f"{_hours(frame.done.zone_time_s(c.rule.min_zone or 1, c.rule.disciplines))} "
                    f"in {zone_name(c.rule.min_zone)} (measured)"
                )
        lines.append(
            "- Already done earlier this week (counts towards the week): "
            + ", ".join(done_bits) + "."
        )
    if frame.walking_floor_m is not None:
        usual_km = frame.usual_distance_m("walk") / 1000
        # Rounded UP to a tenth: stated as "26 km" a week of 26.0 km fails a floor of
        # 26.2, and the model cannot know why.
        needed = math.ceil(frame.walking_floor_m * frame.share_left / 100) / 10
        lines.append(
            f"- Walking: this runner usually walks {usual_km:.0f} km a week. Commit at "
            f"least {needed:.1f} km of walking"
            f"{' for the days left' if frame.share_left < 1 else ''}"
            f"{', race week included' if frame.dated else ''}."
        )
    ceilings = []
    if frame.hours_ceiling_s:
        ceilings.append(f"{_hours(frame.hours_ceiling_s)} of committed time")
    if frame.run_ceiling_m:
        ceilings.append(f"{frame.run_ceiling_m / 1000:.0f} km of running")
    if ceilings:
        lines.append(
            "- A limit, not a target: more than " + " or ".join(ceilings)
            + " in this week is rejected (a race does not count towards it)."
        )
    return lines


def _fmt_day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _view_when(view: Any) -> str:
    if view.date is not None:
        return f"on {_fmt_day(view.date)}"
    if view.window_start is not None:
        return f"between {_fmt_day(view.window_start)} and {_fmt_day(view.window_end)}"
    return "no date"


def describe_season(season: SeasonPlan, goals: Sequence[Any]) -> List[str]:
    """The season as the drafting prompt states it: the coach's summary, its view
    of every goal, the challenge rules and the phase timeline. These are the coach's
    own earlier decisions, so the weeks are told to sit inside them."""
    by_id = _goal_by_id(goals)
    lines = [
        f"Your summary of it: {flat(season.summary, SUMMARY_MAX)}",
        "Your view of each goal:",
    ]
    for view in season.goals:
        goal = by_id.get(view.goal_id)
        name = goal.name if goal is not None else "Goal"
        booked = ", booked, the date is the runner's" if goal is not None and goal.booked else ""
        head = f'- "{name}" ({view.kind}{booked}): {_view_when(view)}.'
        lines.append(
            head
            + f" Success: {flat(view.success, SUCCESS_MAX)}"
            + f" Approach: {flat(view.approach, APPROACH_MAX)}"
        )
        if view.challenge is not None:
            rule = view.challenge
            frame = ChallengeFrame(goal_id=view.goal_id, name=name, rule=rule, index=1)
            lines.append(
                f"  The rule: at least {describe_challenge(frame)} in every week for "
                f"{rule.weeks} straight weeks from the week of {_fmt_day(rule.start)}."
            )
    lines.append("The phases, in order:")
    for phase in season.phases:
        bits = []
        if phase.weekly_hours is not None:
            bits.append(f"{phase.weekly_hours:g} h a week")
        if phase.run_km is not None:
            bits.append(f"{phase.run_km:g} km running")
        if phase.long_run_km is not None:
            bits.append(f"long run {phase.long_run_km:g} km")
        targets = f" (ends at {', '.join(bits)})" if bits else ""
        focus = f": {flat(phase.focus, FOCUS_MAX)}" if phase.focus else ""
        lines.append(
            f"- {phase.kind} {phase.start.isoformat()} to {phase.end.isoformat()}"
            f"{focus}{targets}"
        )
    return lines
