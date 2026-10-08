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
"""

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.schedule import PlannedWeekShape
from app.services.schedule.effort import LoadModel, estimate_effort
from app.services.schedule.frames import WeekFrame, phase_label
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


def _metrics(frame: WeekFrame, comp: Dict[str, float]) -> PlannedMetrics:
    """A split of the week's time, as the numbers `rule_value` reads."""
    metrics = PlannedMetrics(week_start=frame.week_start)
    metrics.time_s = dict(comp)
    run_m = frame.targets.run_m
    for d, seconds in comp.items():
        if d == "run" and run_m:
            metrics.distance_m[d] = run_m
        elif d == "walk":
            metrics.distance_m[d] = frame.usual_distance_m("walk")
        else:
            pace = frame.usual_pace_s_per_m(d)
            metrics.distance_m[d] = seconds / pace if pace else 0.0
        norm = frame.usual.get(d)
        metrics.sessions_by_discipline[d] = round(norm.sessions) if norm else 0
    for zone in frame.shares:
        metrics.zone_est_s[zone] = {
            d: seconds * frame.share(zone, d) for d, seconds in comp.items()
        }
    return metrics


def _top_up(
    frame: WeekFrame, comp: Dict[str, float], ceiling_s: Optional[float]
) -> List[str]:
    """Add hours of the best activity until each challenge holds, within the
    ceiling. Returns a sentence for each that cannot."""
    shortfalls: List[str] = []
    for c in frame.challenges:
        rule = c.rule
        value = rule_value(rule, _metrics(frame, comp))
        gap = rule.at_least - value
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
            value = rule_value(rule, _metrics(frame, comp))
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
            value = rule_value(rule, _metrics(frame, comp))
            if rule.at_least - value > tolerance:
                shortfalls.append(
                    f'The challenge "{c.name}" is shaped at {value / 3600:.1f} h for '
                    f"{label}, under the {rule.at_least / 3600:g} h it needs: the "
                    f"hours ceiling stops more."
                )
        else:
            # Distance and session-count challenges are met by sessions, which a
            # shape does not hold; they are checked when the week is written.
            continue
    return shortfalls


def shape_for(
    frame: WeekFrame, load_model: LoadModel
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The stored `PlannedWeekShape` for a frame, and any shortfalls, or
    (None, []) for a week the season's timeline does not cover."""
    if frame.phase is None:
        return None, []
    total, comp = _split(frame)
    ceiling = frame.sketch_hours_ceiling_s
    if ceiling and total > ceiling:
        scale = ceiling / total
        comp = {d: s * scale for d, s in comp.items()}
    shortfalls = _top_up(frame, comp, ceiling)
    total = sum(comp.values())

    run_m = frame.targets.run_m
    if run_m and frame.sketch_run_ceiling_m:
        run_m = min(run_m, frame.sketch_run_ceiling_m)
    long_m = frame.targets.long_run_m
    if long_m and run_m:
        long_m = min(long_m, run_m)

    mix = {d: round(s / total, 4) for d, s in comp.items() if s > 0} if total > 0 else {}
    effort = 0.0
    priced = False
    for d, seconds in comp.items():
        value = estimate_effort(load_model, d, duration_s=int(seconds), distance_m=None)
        if value is not None:
            effort += value
            priced = True
    focus = (frame.phase.focus or "").strip()
    shape = PlannedWeekShape(
        week_start=frame.week_start,
        phase=phase_label(frame.phase),
        target_running_distance_m=run_m,
        target_effort_score=round(effort, 1) if priced and effort > 0 else None,
        long_run_distance_m=long_m,
        quality_focus=focus[:80].rstrip() or None,
        target_duration_s=round(total) if total > 0 else None,
        target_walking_distance_m=frame.usual_distance_m("walk"),
        discipline_mix=mix,
        intent_mix={},
    )
    return shape.model_dump(mode="json"), shortfalls


def write_shapes(
    frames: List[WeekFrame], load_model: LoadModel, skip: set
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Shapes for every frame not in `skip` (the concrete weeks), and the
    shortfalls the shaping met."""
    shapes: List[Dict[str, Any]] = []
    shortfalls: List[str] = []
    for frame in frames:
        if frame.week_start in skip:
            continue
        shape, found = shape_for(frame, load_model)
        if shape is not None:
            shapes.append(shape)
        shortfalls.extend(found)
    return shapes, shortfalls


def shape_rule_value(shape: PlannedWeekShape, frame: WeekFrame, rule: Any) -> Optional[float]:
    """A challenge's value for a SKETCHED week, from its stored shape: the week's
    time split by the shape's own mix, estimated at the runner's zone shares.
    None when the shape does not state enough to say (no time, or a session-count
    rule a shape does not hold)."""
    if rule.metric == "sessions" or not shape.target_duration_s:
        return None
    comp = {d: shape.target_duration_s * share for d, share in shape.discipline_mix.items()}
    if not comp:
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
