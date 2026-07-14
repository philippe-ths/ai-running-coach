"""Coach-native reframing of the context pack's numeric leaves (ADR 0026 Slice 4, #680).

`frame_pack` takes the GROUPED serialized pack (`CoachContextPack.to_grouped_dict`)
and returns a copy whose leaf VALUES are in coach-native units and precision: km not
metres, min:sec/km not m/s, minute-granularity session durations and second-resolution
interval/zone times, bpm with a light % of max supplement on the two headline HRs, and
no over-precise decimals, dropped efficiency curve, or per-rep start offsets.

This is a ONE-WAY view for the outgoing LLM message only (report pack + chat pack): the
values are strings/rounded numbers that cannot round-trip to typed facts, so the canonical
unframed pack stays what is stored and what the validator, tiering, eval, and re-parse read.
It is applied ONLY under a metrics-coach-framed prompt (`PromptFeature.METRICS_COACH_FRAMED`),
so every prior prompt is byte-identical. Formatting delegates to `coach_units` so the report
pack and the chat `query_tools` render every fact identically.

Pure: no I/O, no DB. Missing/None leaves are skipped, never fabricated.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from app.services.coach import coach_units as u


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
