"""Coach-native reframing of the context pack for the outgoing LLM view (ADR 0026).

Two Slice-scoped transforms live here, composed by `coach_llm_view` and applied at BOTH
LLM seams (the report pack in `service._llm_pack_message` and the chat pack in `chat.py`)
so the report and chat coaches read an IDENTICAL pack:

- `frame_pack` (Slice 4, #680) — reframe the numeric LEAVES to coach-native units and
  precision: km not metres, min:sec/km not m/s, minute-granularity session durations and
  second-resolution interval/zone times, bpm with a light % of max supplement on the two
  headline HRs, and no over-precise decimals, dropped efficiency curve, or per-rep offsets.
  Gated on `PromptFeature.METRICS_COACH_FRAMED` (grouped_v4+).

- `refine_coach_view` (Slice 5, #682) — the COMPLETED coach view: reframe the sections
  Slice 4 left raw (readiness verdict-only, recent_weeks per-session HR), and collapse the
  duplicated / misleading blocks a good coach would misread (the four overlapping interval
  representations into one `interval_read`, the plan-less `workout_match`, the twice-stated
  `hr_drift`, the training-history sentinel/dupes, and an empty `our_thread`). Gated on
  `PromptFeature.PACK_COACH_VIEW` (grouped_v5).

Both are ONE-WAY views for the outgoing LLM message only: the values are strings / reshaped
structures that cannot round-trip to typed facts, so the canonical grouped pack stays what
is STORED and what the validator, tiering, eval, and re-parse read (byte-identical to
grouped_v4). Every non-flagged prompt is byte-identical. Formatting delegates to
`coach_units` so the report pack and the chat `query_tools` render every fact identically.

Pure: no I/O, no DB. Missing/None leaves are skipped, never fabricated.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.services.coach import coach_units as u
from app.services.coach.prompts import (
    is_metrics_coach_framed_prompt,
    is_pack_coach_view_prompt,
    is_salience_dropped_prompt,
)


def frame_pack(grouped: Dict[str, Any]) -> Dict[str, Any]:
    """Return a coach-framed copy of the grouped pack dict. Input is not mutated."""
    if not isinstance(grouped, dict):
        return grouped
    pack = copy.deepcopy(grouped)

    max_hr = _runner_max_hr(pack)

    this_run = pack.get("this_run")
    if isinstance(this_run, dict):
        _frame_activity(this_run.get("activity"), max_hr)
        _frame_metrics(this_run.get("metrics"))

    the_runner = pack.get("the_runner")
    if isinstance(the_runner, dict):
        _frame_training_history(the_runner.get("training_history"))

    return pack


# --- helpers ---------------------------------------------------------------

def _runner_max_hr(pack: Dict[str, Any]) -> Optional[float]:
    the_runner = pack.get("the_runner")
    if isinstance(the_runner, dict):
        profile = the_runner.get("profile")
        if isinstance(profile, dict):
            return profile.get("max_hr")
    return None


def _rename(d: Dict[str, Any], old: str, new: str, value: Any) -> None:
    """Drop `old`, set `new=value`. Skips entirely when `value` is None (drop-when-N/A)."""
    d.pop(old, None)
    if value is not None:
        d[new] = value


def _set_if(d: Dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        d[key] = value
    else:
        d.pop(key, None)


def _frame_activity(activity: Optional[Dict[str, Any]], max_hr: Optional[float]) -> None:
    if not isinstance(activity, dict):
        return
    if "distance_m" in activity:
        _rename(activity, "distance_m", "distance_km", u.km(activity.get("distance_m")))
    if "moving_time_s" in activity:
        _rename(activity, "moving_time_s", "duration", u.duration(activity.get("moving_time_s")))
    # The two headline HRs get bpm + a % of max supplement; bpm stays primary.
    if "avg_hr" in activity:
        _set_if(activity, "avg_hr", u.hr_bpm(activity.get("avg_hr"), max_hr))
    if "max_hr" in activity:
        _set_if(activity, "max_hr", u.hr_bpm(activity.get("max_hr"), max_hr))
    if "avg_cadence" in activity:
        _set_if(activity, "avg_cadence", u.trim(_round(activity.get("avg_cadence"), 0)))
    if "elev_gain_m" in activity:
        _set_if(activity, "elev_gain_m", u.trim(activity.get("elev_gain_m")))


def _frame_metrics(metrics: Optional[Dict[str, Any]]) -> None:
    if not isinstance(metrics, dict):
        return
    if metrics.get("effort_score") is not None:
        metrics["effort_score"] = u.trim(metrics["effort_score"])
    if metrics.get("hr_drift") is not None:
        metrics["hr_drift"] = u.trim(metrics["hr_drift"])

    _frame_time_in_zones(metrics.get("time_in_zones"))
    _frame_efficiency(metrics.get("efficiency_analysis"))
    _frame_stops(metrics.get("stops_analysis"))
    _frame_interval_structure(metrics.get("interval_structure"))
    _frame_workout_match(metrics.get("workout_match"))
    _frame_interval_kpis(metrics.get("interval_kpis"))
    _frame_discount_signals(metrics.get("discount_signals"))


def _frame_time_in_zones(tiz: Any) -> None:
    if not isinstance(tiz, dict):
        return
    for zone, seconds in list(tiz.items()):
        tiz[zone] = u.duration_precise(seconds)


def _frame_efficiency(eff: Any) -> None:
    if isinstance(eff, dict):
        # 64 three-decimal m/min/bpm points are not a coaching artifact and invite
        # noise-narration; average + best_sustained are the usable summary.
        eff.pop("curve", None)


def _frame_stops(stops: Any) -> None:
    if not isinstance(stops, dict):
        return
    if "total_stopped_time_s" in stops:
        _rename(stops, "total_stopped_time_s", "total_stopped_time",
                u.duration_precise(stops.get("total_stopped_time_s")))
    if "longest_stop_s" in stops:
        _rename(stops, "longest_stop_s", "longest_stop", u.duration_precise(stops.get("longest_stop_s")))
    for stop in stops.get("stops") or []:
        if not isinstance(stop, dict):
            continue
        stop.pop("location", None)   # GPS lat/long: noise the coach cannot use
        stop.pop("start_time", None)  # absolute offset: near-noise, avoids mixed units
        if "duration_s" in stop:
            _rename(stop, "duration_s", "duration", u.duration_precise(stop.get("duration_s")))
        if "distance_m" in stop:
            _set_if(stop, "distance_m", u.trim(_round(stop.get("distance_m"), 0)))


def _frame_interval_structure(istruct: Any) -> None:
    if not isinstance(istruct, dict):
        return
    if "warmup_duration_s" in istruct:
        _rename(istruct, "warmup_duration_s", "warmup", u.duration_precise(istruct.get("warmup_duration_s")))
    if "cooldown_duration_s" in istruct:
        _rename(istruct, "cooldown_duration_s", "cooldown", u.duration_precise(istruct.get("cooldown_duration_s")))
    for seg in istruct.get("work_segments") or []:
        if not isinstance(seg, dict):
            continue
        seg.pop("start_time_s", None)  # per-rep offset: near-noise, avoids mixed units
        if "duration_s" in seg:
            _rename(seg, "duration_s", "duration", u.duration_precise(seg.get("duration_s")))
        if "distance_m" in seg:
            _set_if(seg, "distance_m", u.trim(_round(seg.get("distance_m"), 0)))  # reps stay metres
        _plain_bpm(seg, "avg_hr")
        _plain_bpm(seg, "peak_hr")
    for seg in istruct.get("rest_segments") or []:
        if not isinstance(seg, dict):
            continue
        if "duration_s" in seg:
            _rename(seg, "duration_s", "duration", u.duration_precise(seg.get("duration_s")))
        _plain_bpm(seg, "avg_hr")
        if seg.get("hr_recovery_bpm") is not None:
            seg["hr_recovery_bpm"] = u.trim(seg["hr_recovery_bpm"])
    _frame_interval_summary(istruct.get("summary"))


def _frame_interval_summary(summary: Any) -> None:
    if not isinstance(summary, dict):
        return
    for old, new in (
        ("total_work_time_s", "total_work_time"),
        ("total_rest_time_s", "total_rest_time"),
        ("avg_work_duration_s", "avg_work_duration"),
        ("avg_rest_duration_s", "avg_rest_duration"),
    ):
        if old in summary:
            _rename(summary, old, new, u.duration_precise(summary.get(old)))
    if "avg_work_speed_mps" in summary:
        _rename(summary, "avg_work_speed_mps", "avg_work_pace", u.pace_from_speed(summary.get("avg_work_speed_mps")))
    if summary.get("avg_hr_recovery_bpm") is not None:
        summary["avg_hr_recovery_bpm"] = u.trim(summary["avg_hr_recovery_bpm"])


def _frame_workout_match(wm: Any) -> None:
    if not isinstance(wm, dict):
        return
    detected = wm.get("detected_workout")
    if not isinstance(detected, dict):
        return
    if "rep_duration_mean_s" in detected:
        _rename(detected, "rep_duration_mean_s", "rep_duration_mean",
                u.duration_precise(detected.get("rep_duration_mean_s")))
    for old, new in (("total_work_time_s", "total_work_time"), ("total_rest_time_s", "total_rest_time")):
        if old in detected:
            _rename(detected, old, new, u.duration_precise(detected.get(old)))
    if "rep_distance_mean_m" in detected:
        _set_if(detected, "rep_distance_mean_m", u.trim(_round(detected.get("rep_distance_mean_m"), 0)))


def _frame_interval_kpis(kpis: Any) -> None:
    if not isinstance(kpis, dict):
        return
    if "total_z4_plus_s" in kpis:
        _rename(kpis, "total_z4_plus_s", "total_z4_plus", u.duration_precise(kpis.get("total_z4_plus_s")))


def _frame_discount_signals(ds: Any) -> None:
    if not isinstance(ds, dict):
        return
    if ds.get("temperature_c") is not None:
        ds["temperature_c"] = u.trim(ds["temperature_c"])


def _frame_training_history(th: Any) -> None:
    if not isinstance(th, dict):
        return
    traits = th.get("traits")
    if isinstance(traits, dict) and "peak_sustained_weekly_distance_m" in traits:
        _rename(traits, "peak_sustained_weekly_distance_m", "peak_sustained_weekly_km",
                u.km(traits.get("peak_sustained_weekly_distance_m")))
    for period in th.get("timeline") or []:
        if not isinstance(period, dict):
            continue
        _frame_history_period(period)
        for by in period.get("by_type") or []:
            _frame_history_period(by)


def _frame_history_period(period: Dict[str, Any]) -> None:
    if "avg_weekly_distance_m" in period:
        _rename(period, "avg_weekly_distance_m", "avg_weekly_km", u.km(period.get("avg_weekly_distance_m")))
    if period.get("avg_weekly_sessions") is not None:
        period["avg_weekly_sessions"] = _round(period["avg_weekly_sessions"], 1)


# --- tiny numeric helpers --------------------------------------------------

def _round(value: Any, ndigits: int) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value, ndigits) if ndigits else round(value)
    return value


def _plain_bpm(d: Dict[str, Any], key: str) -> None:
    if d.get(key) is not None:
        d[key] = u.bpm(d[key])


# ===========================================================================
# ADR 0026 Slice 5 (#682): the COMPLETED coach view.
# refine_coach_view runs AFTER frame_pack, so it reads already-framed leaves
# (durations "M:SS", distances km, etc.). It reshapes what a good coach would
# otherwise misread; every drop is drop-when-present, never fabricated.
# ===========================================================================

def refine_coach_view(grouped: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the (framed) grouped view with the Slice-5 refinements applied."""
    if not isinstance(grouped, dict):
        return grouped
    pack = copy.deepcopy(grouped)
    max_hr = _runner_max_hr(pack)

    this_run = pack.get("this_run")
    if isinstance(this_run, dict):
        _refine_metrics(this_run.get("metrics"))

    right_now = pack.get("right_now")
    if isinstance(right_now, dict):
        _refine_readiness(right_now)
        _refine_recent_weeks(right_now.get("recent_weeks"))

    the_runner = pack.get("the_runner")
    if isinstance(the_runner, dict):
        _refine_training_history(the_runner.get("training_history"))

    _drop_empty_our_thread(pack)
    return pack


def _refine_metrics(metrics: Optional[Dict[str, Any]]) -> None:
    if not isinstance(metrics, dict):
        return
    # hr_drift is stated twice: this_run.intensity_read.drift_vs_typical.observed_pct
    # already carries it WITH the confound flag and the runner's typical, so it is the
    # single home. Drop the bare metrics copy.
    metrics.pop("hr_drift", None)
    # workout_match is meaningless without a plan (match_score is a de-facto 1.0), and the
    # runner has no planned-workout capture yet — it only invites "you nailed your plan".
    wm = metrics.get("workout_match")
    if isinstance(wm, dict) and "no_planned_workout" in (wm.get("confidence_reasons") or []):
        metrics.pop("workout_match", None)
    # Collapse the four overlapping interval representations into one read.
    _merge_interval_read(metrics)


def _merge_interval_read(metrics: Dict[str, Any]) -> None:
    istruct = metrics.get("interval_structure")
    if not isinstance(istruct, dict):
        return
    summary = istruct.get("summary") or {}
    kpis = metrics.get("interval_kpis") or {}
    rest_by_num = {
        r.get("segment_number"): r
        for r in (istruct.get("rest_segments") or [])
        if isinstance(r, dict)
    }
    reps: List[Dict[str, Any]] = []
    for w in istruct.get("work_segments") or []:
        if not isinstance(w, dict):
            continue
        n = w.get("segment_number")
        rep = {
            "n": n,
            "distance_m": w.get("distance_m"),
            "duration": w.get("duration"),
            "avg_hr": w.get("avg_hr"),
            "peak_hr": w.get("peak_hr"),
        }
        rest = rest_by_num.get(n)
        if isinstance(rest, dict):
            rep["recovery"] = rest.get("duration")
            rep["recovery_drop_bpm"] = rest.get("hr_recovery_bpm")
        reps.append({k: v for k, v in rep.items() if v is not None})

    interval_read = {
        "source": istruct.get("source"),
        "rep_count": summary.get("rep_count"),
        "warmup": istruct.get("warmup"),
        "cooldown": istruct.get("cooldown"),
        "reps": reps or None,
        "avg_work_pace": summary.get("avg_work_pace"),
        "avg_work_duration": summary.get("avg_work_duration"),
        "avg_rest_duration": summary.get("avg_rest_duration"),
        "work_to_rest_ratio": summary.get("work_to_rest_ratio"),
        "consistency": summary.get("consistency_score"),
        # rep_pace_consistency_cv == work_speed_cv == work_duration_cv: keep one.
        "rep_variation_cv": summary.get("work_speed_cv"),
        "first_vs_last_fade": kpis.get("first_vs_last_fade"),
        "avg_hr_recovery_bpm": summary.get("avg_hr_recovery_bpm"),
        "recovery_quality_per_60s": kpis.get("recovery_quality_per_60s"),
        "total_work_time": summary.get("total_work_time"),
        "total_rest_time": summary.get("total_rest_time"),
        "total_z4_plus": kpis.get("total_z4_plus"),
    }
    metrics.pop("interval_structure", None)
    metrics.pop("interval_kpis", None)
    metrics["interval_read"] = {k: v for k, v in interval_read.items() if v is not None}


def _refine_readiness(right_now: Dict[str, Any]) -> None:
    """Keep the readiness VERDICT (condition/trend + a ramp flag only when aggressive) and
    drop the arbitrary-scale EWMA numbers (fitness/fatigue/form/ramp_rate/sample_count) that
    a coach can only narrate wrongly. The verdict strings are passed through verbatim."""
    r = right_now.get("readiness")
    if not isinstance(r, dict):
        return
    kept: Dict[str, Any] = {}
    for key in ("condition", "trend"):
        if r.get(key) is not None:
            kept[key] = r[key]
    if r.get("ramp_aggressive"):
        kept["ramp_aggressive"] = True
    right_now["readiness"] = kept


def _refine_recent_weeks(recent_weeks: Any) -> None:
    """Per-session `avg_hr` inside recent_weeks arrives as a raw bpm float ("115.1");
    render it as plain bpm for consistency with the rest of the pack (the per-session
    `intensity` label already carries the effort read, so no % of max is needed here)."""
    if not isinstance(recent_weeks, dict):
        return
    for window in ("this_week", "last_week"):
        w = recent_weeks.get(window)
        if not isinstance(w, dict):
            continue
        for day in w.get("days") or []:
            for act in (day.get("activities") if isinstance(day, dict) else None) or []:
                if isinstance(act, dict) and act.get("avg_hr") is not None:
                    act["avg_hr"] = u.bpm(act["avg_hr"])


def _refine_training_history(th: Any) -> None:
    if not isinstance(th, dict):
        return
    traits = th.get("traits")
    if isinstance(traits, dict):
        # Two "current vs peak" numbers (distance + load) are confusable; name the
        # distance one so it cannot be read as the load one.
        if "current_vs_peak_pct" in traits:
            traits["current_vs_peak_distance_pct"] = traits.pop("current_vs_peak_pct")
        # `no_norm` is an internal sentinel; show nothing rather than an opaque token.
        if traits.get("trajectory_direction") == "no_norm":
            traits.pop("trajectory_direction", None)
            traits.pop("trajectory_pct", None)
    for period in th.get("timeline") or []:
        if isinstance(period, dict):
            # run_share_pct duplicates by_type[type=Run].share_pct.
            period.pop("run_share_pct", None)


def _drop_empty_our_thread(pack: Dict[str, Any]) -> None:
    """our_thread holds only an empty adherence when the relationship inputs are gated off
    (prod: longitudinal/continuity). The real open threads live in the_runner.memory.lately,
    so an empty our_thread just sends the coach to an empty room — drop the whole group."""
    ot = pack.get("our_thread")
    if not isinstance(ot, dict):
        return
    adh = ot.get("adherence")
    adh_empty = adh is None or (
        isinstance(adh, dict) and not adh.get("outcomes") and not adh.get("prior_report_date")
    )
    others = any(k != "adherence" for k in ot)
    if adh_empty and not others:
        pack.pop("our_thread", None)


def coach_llm_view(pack_dict: Dict[str, Any], prompt_id: Optional[str], *, mode: str = "fuller") -> Dict[str, Any]:
    """The single outgoing-LLM view of the pack, shared by the report and chat seams.

    Composes the gated one-way transforms in order: frame the leaves (Slice 4), apply the
    completed coach view (Slice 5), then drop the fuller-only `salience` routing section.
    Every step is a no-op for a prompt that does not carry its gate, so the flat/prior packs
    are byte-identical. Never mutates `pack_dict`."""
    view = frame_pack(pack_dict) if is_metrics_coach_framed_prompt(prompt_id) else pack_dict
    if is_pack_coach_view_prompt(prompt_id):
        view = refine_coach_view(view)
    if mode != "opener" and is_salience_dropped_prompt(prompt_id) and "salience" in view:
        view = {k: v for k, v in view.items() if k != "salience"}
    return view
