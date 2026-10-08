"""The sketched weeks, written by code from the season (#1064).

A week beyond the concrete ones is a SHAPE: a phase, the running and walking
distance, the long run, what the hard session is for, and the week's time. The
coach's opinion about all of it is already on the season (the phases and their
targets), so these weeks are arithmetic on frames and no model is called. That is
the point of the change: a shape the model wrote carried sums the model typed, and
the challenge was missed in exactly those sums.

How a week's time is built
--------------------------
The week takes the season's interpolated hours. It is split across activities in
the proportions of the runner's own usual week, except running, which follows the
phase's running distance at the runner's OWN usual pace for it. Time from a
distance uses that pace and no other: a population pace would turn a sketch into a
promise about a body that is not this one.

If a challenge covers the week and the split falls short of its threshold, hours
of the activity that closes the gap fastest for this runner (the highest share of
heart-rate time at the rule's zone) are added until it holds, so the shape states a
week that WOULD meet the challenge. If a ceiling stops that, the gap is returned as
a shortfall for the plan's run log: said, never hidden.

Planned zone time is estimated (time x the runner's share); it is not measured.

What a shape states about each activity
---------------------------------------
`discipline_mix` is the activities' shares of the week's LOAD, as the schema
documents, because that is what the horizon bar is drawn from. A challenge is
counted in TIME, so the seconds per activity are stated as well
(`duration_by_discipline_s`) and the challenge is read off those, never off a
load share taken for a time share. A shape written before the field existed reads
it empty and falls back to the old reading.
"""

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.schedule import PlannedWeekShape
from app.services.schedule.effort import LoadModel, estimate_effort
from app.services.schedule.frames import WeekFrame, phase_label
from app.services.schedule.prompt_text import FOCUS_MAX, flat
from app.services.schedule.week_check import PlannedMetrics, best_share_discipline, rule_value

_TOLERANCE_S = 60.0
_TOLERANCE_M = 10.0


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def _split(frame: WeekFrame) -> Tuple[float, Dict[str, float]]:
    """(total seconds, seconds by discipline) for the week before any challenge
    top-up."""
    hours = frame.targets.weekly_hours_s
    run_m = frame.targets.run_m
    pace = frame.usual_pace_s_per_m("run")
    run_s = run_m * pace if (run_m and pace) else None
    usual_total = frame.usual_total_s

    if hours is None:
        if run_s is not None and usual_total > 0:
            hours = run_s + (usual_total - frame.usual_time_s("run"))
        elif usual_total > 0:
            hours = usual_total
        else:
            return 0.0, {}
    if run_s is not None:
        hours = max(hours, run_s)
        others_total = frame.usual_total_s - frame.usual_time_s("run")
        rest = hours - run_s
        comp = {"run": run_s}
        if others_total > 0 and rest > 0:
            for d, norm in frame.usual.items():
                if d != "run" and norm.moving_time_s > 0:
                    comp[d] = rest * norm.moving_time_s / others_total
        else:
            hours = run_s
        return sum(comp.values()), comp
    if usual_total <= 0:
        return hours, {}
    return hours, {
        d: hours * norm.moving_time_s / usual_total
        for d, norm in frame.usual.items()
        if norm.moving_time_s > 0
    }


def _distances(frame: WeekFrame, comp: Dict[str, float]) -> Dict[str, float]:
    """The distances a shape states: running and walking, the only two it holds.

    Running is the phase's distance (under the sketch ceiling) or, when the season
    gives none, what the week's running time covers at the runner's own pace;
    walking is the runner's usual, which the plan keeps whole.
    """
    out: Dict[str, float] = {}
    run_m = frame.targets.run_m
    if run_m and frame.sketch_run_ceiling_m:
        run_m = min(run_m, frame.sketch_run_ceiling_m)
    if "run" in comp:
        pace = frame.usual_pace_s_per_m("run")
        run_m = run_m or (comp["run"] / pace if pace else None)
        if run_m:
            out["run"] = run_m
    if "walk" in comp:
        out["walk"] = frame.usual_distance_m("walk")
    return out


def _metrics(
    frame: WeekFrame, comp: Dict[str, float], dist: Dict[str, float]
) -> PlannedMetrics:
    """A split of the week's time, as the numbers `rule_value` reads."""
    metrics = PlannedMetrics(week_start=frame.week_start)
    metrics.time_s = dict(comp)
    metrics.distance_m = {d: m for d, m in dist.items() if d in comp}
    for d in comp:
        norm = frame.usual.get(d)
        metrics.sessions_by_discipline[d] = round(norm.sessions) if norm else 0
    for zone in frame.shares:
        metrics.zone_est_s[zone] = {
            d: seconds * frame.share(zone, d) for d, seconds in comp.items()
        }
    return metrics


def _distance_discipline(frame: WeekFrame, rule: Any) -> Optional[str]:
    """The activity a distance challenge is topped up with: run or walk, whichever
    the rule counts and the runner does the most of."""
    allowed = [d for d in ("run", "walk") if not rule.disciplines or d in rule.disciplines]
    allowed = [d for d in allowed if frame.usual_distance_m(d) > 0]
    return max(allowed, key=frame.usual_distance_m, default=None)


def _top_up(
    frame: WeekFrame,
    comp: Dict[str, float],
    dist: Dict[str, float],
    ceiling_s: Optional[float],
) -> List[str]:
    """Add hours (or distance) of the best activity until each challenge holds,
    within the ceilings. Returns a sentence for each that cannot."""
    shortfalls: List[str] = []
    for c in frame.challenges:
        rule = c.rule
        if rule.metric == "sessions":
            # A sketch holds no sessions: this is checked when the week is written,
            # and the figure the screen shows for the week is "not yet".
            continue
        value = rule_value(rule, _metrics(frame, comp, dist))
        gap = c.plan_for - value
        tolerance = _TOLERANCE_S if rule.metric in ("zone_time_s", "time_s") else _TOLERANCE_M
        if gap <= tolerance:
            continue
        label = f'the week of {_day(frame.week_start)}'
        if rule.metric == "zone_time_s":
            best = best_share_discipline(frame, c)
            if best is None:
                shortfalls.append(
                    f"The challenge \"{c.name}\" cannot be counted for {label}: there is "
                    f"no heart-rate share to estimate zone {rule.min_zone}+ time from."
                )
                continue
            extra = gap / frame.share(rule.min_zone or 1, best)
            room = (ceiling_s - sum(comp.values())) if ceiling_s else extra
            add = max(0.0, min(extra, room))
            comp[best] = comp.get(best, 0.0) + add
            value = rule_value(rule, _metrics(frame, comp, dist))
            if rule.at_least - value > tolerance:
                shortfalls.append(
                    f'The challenge "{c.name}" is shaped at {value / 3600:.1f} h of '
                    f"zone {rule.min_zone}+ (estimated) for {label}, under the "
                    f"{rule.at_least / 3600:g} h it needs: the hours ceiling stops more."
                )
        elif rule.metric == "time_s":
            target = max(rule.disciplines, key=lambda d: frame.usual_time_s(d), default=None) or (
                max(comp, key=comp.get, default=None)
            )
            if target is None:
                continue
            room = (ceiling_s - sum(comp.values())) if ceiling_s else gap
            comp[target] = comp.get(target, 0.0) + max(0.0, min(gap, room))
            value = rule_value(rule, _metrics(frame, comp, dist))
            if rule.at_least - value > tolerance:
                shortfalls.append(
                    f'The challenge "{c.name}" is shaped at {value / 3600:.1f} h for '
                    f"{label}, under the {rule.at_least / 3600:g} h it needs: the "
                    f"hours ceiling stops more."
                )
        else:
            # Distance: more of the run or walk distance the shape states, with the
            # time it takes at the runner's own pace for it, so the shape is one
            # week and not a distance floating free of its hours.
            best = _distance_discipline(frame, rule)
            pace = frame.usual_pace_s_per_m(best) if best else None
            if best is None or not pace:
                shortfalls.append(
                    f'The challenge "{c.name}" cannot be shaped for {label}: a sketched '
                    "week holds running and walking distance, and this runner has no "
                    "usual pace to size it with."
                )
                continue
            room_m = float("inf")
            if ceiling_s:
                room_m = max(0.0, (ceiling_s - sum(comp.values())) / pace)
            if best == "run" and frame.sketch_run_ceiling_m:
                room_m = min(room_m, max(0.0, frame.sketch_run_ceiling_m - dist.get("run", 0.0)))
            add_m = max(0.0, min(gap, room_m))
            dist[best] = dist.get(best, 0.0) + add_m
            comp[best] = comp.get(best, 0.0) + add_m * pace
            value = rule_value(rule, _metrics(frame, comp, dist))
            if rule.at_least - value > tolerance:
                shortfalls.append(
                    f'The challenge "{c.name}" is shaped at {value / 1000:.1f} km for '
                    f"{label}, under the {rule.at_least / 1000:g} km it needs: a "
                    "ceiling stops more."
                )
    return shortfalls


def shape_for(
    frame: WeekFrame, load_model: LoadModel, *, usual_week: bool = False
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The stored `PlannedWeekShape` for a frame, and any shortfalls, or
    (None, []) for a week the season's timeline does not cover.

    `usual_week` is for a runner with NO season (no goal to plan around): a week
    the season would not cover is then sketched from the usual week, phase "Base",
    with no challenge, so the horizon is a view of their normal training and not
    empty beyond the concrete weeks.
    """
    if frame.phase is None and not usual_week:
        # A week the season's timeline does not reach is not sketched. When a
        # challenge covers it that is a hole in the rule, not a quiet week, so it
        # is said (the season check refuses such a timeline; this is for a stored
        # one).
        return None, [
            f'The challenge "{c.name}" covers the week of {_day(frame.week_start)} but '
            "the season has no phase there, so the week is not planned."
            for c in frame.challenges
        ]
    total, comp = _split(frame)
    ceiling = frame.sketch_hours_ceiling_s
    if ceiling and total > ceiling:
        scale = ceiling / total
        comp = {d: s * scale for d, s in comp.items()}
    dist = _distances(frame, comp)
    shortfalls = _top_up(frame, comp, dist, ceiling)
    total = sum(comp.values())
    if frame.phase is None and total <= 0:
        return None, shortfalls

    run_m = dist.get("run")
    long_m = frame.targets.long_run_m
    if long_m and run_m:
        long_m = min(long_m, run_m)

    effort = 0.0
    priced = False
    load: Dict[str, float] = {}
    for d, seconds in comp.items():
        value = estimate_effort(load_model, d, duration_s=int(seconds), distance_m=None)
        if value is not None:
            effort += value
            priced = True
            if value > 0:
                load[d] = value
    # Shares of the week's LOAD, as the schema documents: the horizon draws its
    # bar from load. The seconds per activity ride beside it for the challenge.
    mix = {d: round(v / effort, 4) for d, v in load.items()} if effort > 0 else {}
    focus = flat(frame.phase.focus, FOCUS_MAX) if frame.phase is not None else ""
    shape = PlannedWeekShape(
        week_start=frame.week_start,
        phase=phase_label(frame.phase) if frame.phase is not None else "Base",
        target_running_distance_m=run_m,
        target_effort_score=round(effort, 1) if priced and effort > 0 else None,
        long_run_distance_m=long_m,
        quality_focus=focus[:80].rstrip() or None,
        target_duration_s=round(total) if total > 0 else None,
        target_walking_distance_m=dist.get("walk", frame.usual_distance_m("walk")),
        discipline_mix=mix,
        duration_by_discipline_s={d: round(s) for d, s in comp.items() if s > 0},
        intent_mix={},
    )
    return shape.model_dump(mode="json"), shortfalls


def write_shapes(
    frames: List[WeekFrame], load_model: LoadModel, skip: set, *, usual_week: bool = False
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Shapes for every frame not in `skip` (the concrete weeks), and the
    shortfalls the shaping met. `usual_week` sketches weeks no season covers from
    the runner's usual week (see `shape_for`)."""
    shapes: List[Dict[str, Any]] = []
    shortfalls: List[str] = []
    for frame in frames:
        if frame.week_start in skip:
            continue
        shape, found = shape_for(frame, load_model, usual_week=usual_week)
        if shape is not None:
            shapes.append(shape)
        shortfalls.extend(found)
    return shapes, shortfalls


def shape_rule_value(shape: PlannedWeekShape, frame: WeekFrame, rule: Any) -> Optional[float]:
    """A challenge's value for a SKETCHED week, from its stored shape: the week's
    time per activity, estimated at the runner's zone shares. None when the shape
    does not state enough to say: no time, a session-count rule (a sketch holds no
    sessions, so it is checked when the week is written), or a distance rule over
    an activity whose distance a shape does not hold."""
    if rule.metric == "sessions":
        return None
    if shape.duration_by_discipline_s:
        comp = dict(shape.duration_by_discipline_s)
    elif shape.target_duration_s:
        # A shape written before the seconds were stated: the old reading, the
        # week's time split by the mix.
        comp = {d: shape.target_duration_s * share for d, share in shape.discipline_mix.items()}
    else:
        return None
    if not comp:
        return None
    if rule.metric == "distance_m" and not (set(rule.disciplines) or set(comp)) <= {"run", "walk"}:
        return None
    metrics = PlannedMetrics(week_start=frame.week_start)
    metrics.time_s = comp
    if shape.target_running_distance_m is not None and "run" in comp:
        metrics.distance_m["run"] = shape.target_running_distance_m
    if shape.target_walking_distance_m is not None and "walk" in comp:
        metrics.distance_m["walk"] = shape.target_walking_distance_m
    for zone in frame.shares:
        metrics.zone_est_s[zone] = {
            d: seconds * frame.share(zone, d) for d, seconds in comp.items()
        }
    return rule_value(rule, metrics)
