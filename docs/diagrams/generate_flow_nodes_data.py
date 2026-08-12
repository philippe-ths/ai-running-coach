#!/usr/bin/env python3
"""Regenerate the DATA blob + SYSTEM_PROMPT in flow-nodes.js (the ai-flow-graph.html
data-flow diagram) from a real activity in the local seeded DB, under the active
coach prompt version.

Unlike generate_flow_data.py (which emits HTML snippets for the older ai-flow.html),
this rebuilds the structured `const DATA = {...}` and `const SYSTEM_PROMPT = "..."`
that ai-flow-graph.html renders, by calling the REAL pipeline code:
build_context_pack(prompt_id=...) for the pack and build_system_prompt(...) for the
instructions. So the diagram stays a one-to-one representation of what the coach
actually receives at the chosen prompt version. The hand-authored NODES / fate maps /
helpers below the data in flow-nodes.js are left untouched.

Usage:
    python docs/diagrams/generate_flow_nodes_data.py                  # newest good activity, live prod prompt (lean_v1)
    PROMPT_ID=coach_message_v14 ACTIVITY_ID=<uuid> python ...         # pin both (e.g. to a specific prompt / activity)

Read-only against the DB; rewrites only the two generated lines of flow-nodes.js.
Local DB only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import undefer  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Activity, CoachReport, ActivityStream, UserProfile  # noqa: E402
from app.models import CoachingRelationship, Block  # noqa: E402
from app.services.analysis.classifier import Classification, playbook_key  # noqa: E402
from app.services.analysis.smoothing import smooth_cadence  # noqa: E402
from app.services.coach.coach_framing import coach_llm_view  # noqa: E402
from app.services.coach.context import build_context_pack  # noqa: E402
from app.services.coach.prompts import build_system_prompt, is_grouped_pack_prompt  # noqa: E402
from app.services.coach.service import (  # noqa: E402
    _resolve_stance_for_activity,
    _resolve_voice_for_activity,
)

# Default to the live prod prompt so the diagram is a one-to-one picture of what the
# coach actually receives in production. Prod flipped to coach_message_lean_grouped_v5
# on 2026-07-14 (the ADR 0026 finale): the SAME canonical pack as lean_v1, but served
# GROUPED into the five coaching-question groups through the completed coach-native LLM
# view (coach units, one merged intensity_read + interval_read, readiness verdict-only,
# salience dropped from the fuller view). So DATA.pack captures the flat canonical
# SUBSTRATE (to_serializable_dict — what the read-time builders compute and store, and
# what the flat pack nodes render), while DATA.llm_view captures the ACTUAL grouped
# message the model receives (coach_llm_view over to_grouped_dict), shown at the llm
# node. Under grouped_v5 the flat substrate itself already carries the grouped-era
# sections (readiness / recent_weeks / intensity_read / intensity_mix) and drops the
# flat originals they replace (training_load / training_volume / recent_training /
# perceived_effort / calibration / intensity). pack.memory is absent only when the
# runner has no graduated runner_memory profile yet (cold start drops the section).
PROMPT_ID = os.environ.get("PROMPT_ID", "coach_message_lean_grouped_v5")
TARGET = Path(__file__).parent / "flow-nodes.js"

# DerivedMetric columns the diagram shows (order = render order; excludes id / fks /
# timestamps). stream_view is the deferred consolidated view, undeferred below.
_DM_FIELDS = [
    "effort_score", "pace_variability", "hr_drift", "time_in_zones", "efficiency_analysis", "flags",
    "confidence", "confidence_reasons", "structure", "effort", "duration_class",
    "is_hilly", "is_race", "risk_level", "risk_score", "risk_reasons",
    "interval_structure", "workout_match", "interval_kpis", "discount_signals",
    "training_context", "stops_analysis", "stream_view",
]
_RAW_SUMMARY_KEYS = [
    "average_temp", "average_speed", "total_elevation_gain", "nlaps",
    "sport_type", "average_heartrate",
]
_PROFILE_FIELDS = [
    "goal_type", "experience_level", "weekly_days_available", "current_weekly_km",
    "max_hr", "max_hr_source", "hr_zones_source", "injury_notes", "stimulant_use",
]


def _guard_local() -> None:
    url = settings.DATABASE_URL
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"refusing non-local DB target: {url!r}")


def _downsample(series, cap=100):
    """Evenly downsample a numeric series to <= cap points, preserving shape."""
    vals = [v for v in series]
    n = len(vals)
    if n <= cap:
        return vals
    step = n / cap
    return [vals[min(n - 1, int(i * step))] for i in range(cap)]


def _is_numeric_series(data) -> bool:
    return bool(data) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in data)


def _pick_activity(db):
    if os.environ.get("ACTIVITY_ID"):
        a = db.get(Activity, os.environ["ACTIVITY_ID"])
        if a is None:
            sys.exit(f"no activity {os.environ['ACTIVITY_ID']!r}")
        return a
    # Newest non-deleted activity that has streams, a DerivedMetric, and a non-fallback
    # message-shaped coach report (so D.report renders a real prose message). A Run is
    # the richest subject for a coaching diagram (HR zones, pace, drift), so prefer one;
    # fall back to any qualifying activity if no Run qualifies.
    def _qualifies(a):
        if a.metrics is None:
            return False
        # The stream-view nodes (a_streamview + pack.stream_view) need a real
        # downsample to render. Accessing the deferred column lazy-loads it.
        if a.metrics.stream_view is None:
            return False
        has_streams = db.execute(
            select(ActivityStream.id).where(ActivityStream.activity_id == a.id).limit(1)
        ).first()
        if not has_streams:
            return False
        rep = db.execute(
            select(CoachReport)
            .where(CoachReport.activity_id == a.id, CoachReport.is_fallback.is_(False))
            .order_by(CoachReport.created_at.desc())
        ).scalars().first()
        return bool(rep and isinstance(rep.report, dict) and (
            rep.report.get("message") or rep.report.get("opener_message")
        ))

    rows = list(db.execute(
        select(Activity)
        .where(Activity.is_deleted.is_(False))
        .order_by(Activity.start_date_local.desc().nullslast())
    ).scalars())
    for want_run in (True, False):
        for a in rows:
            if want_run and (a.type or "").lower() != "run":
                continue
            if _qualifies(a):
                return a
    sys.exit("no suitable activity (need streams + a non-fallback message report)")


def _streams(db, activity_id):
    out = {}
    for s in db.execute(
        select(ActivityStream).where(ActivityStream.activity_id == activity_id)
    ).scalars():
        data = s.data or []
        if _is_numeric_series(data):
            out[s.stream_type] = {"n": len(data), "series": _downsample(data, 100)}
        else:
            out[s.stream_type] = {"n": len(data), "head": data[:8]}
    return out


def _smoothing(db, activity_id):
    by = {
        s.stream_type: (s.data or [])
        for s in db.execute(
            select(ActivityStream).where(ActivityStream.activity_id == activity_id)
        ).scalars()
    }
    cad = by.get("cadence")
    if not cad:
        return None
    vel = by.get("velocity_smooth", [0.0] * len(cad))
    mov = by.get("moving", [True] * len(cad))
    tim = by.get("time", list(range(len(cad))))
    sm = smooth_cadence(cad, vel, mov, tim)
    return {
        "n": len(cad),
        "cadence_raw": _downsample(cad, 128),
        "cadence_smoothed": _downsample(sm, 128),
    }


def _derived(metrics):
    d = {}
    for f in _DM_FIELDS:
        d[f] = getattr(metrics, f, None)
    return d


def build_data(db):
    activity = _pick_activity(db)
    metrics = activity.metrics
    _ = metrics.stream_view  # access the deferred consolidated view to load it

    stance = _resolve_stance_for_activity(db, activity)
    voice = _resolve_voice_for_activity(db, activity)
    try:
        stance_note = (f"resolved at generation time: school {stance.school_id}, "
                       f"emphasis {stance.emphasis.data_sentiment}/{stance.emphasis.process_outcome}")
    except Exception:
        stance_note = "resolved at generation time"
    pack = build_context_pack(db, activity, prompt_id=PROMPT_ID, stance=stance)
    # The ACTUAL message the model receives: for a grouped prompt (prod's grouped_v5)
    # this is the five-group envelope passed through the completed coach-native view
    # (coach units + interval_read/intensity merges + readiness verdict-only + the
    # fuller salience drop); for a flat prompt it is the flat coach view. Mirrors the
    # service seam service._llm_pack_message so the diagram's llm node is byte-faithful
    # to production, distinct from DATA.pack (the flat canonical substrate).
    llm_pack_dict = (
        pack.to_grouped_dict() if is_grouped_pack_prompt(PROMPT_ID)
        else pack.to_serializable_dict()
    )
    llm_view = coach_llm_view(llm_pack_dict, PROMPT_ID)
    classification = Classification.from_metrics(metrics)
    # No `voice=`: since #822 the report is generated VOICELESS and re-voiced
    # afterwards by `voice_rewrite`, so the system prompt no longer takes one.
    # The generator kept passing it and had been raising a TypeError ever since —
    # `make diagram-check` compares pack sections and DerivedMetric columns, so it
    # cannot see a generator that will not run.
    system_prompt = build_system_prompt(PROMPT_ID, playbook_key(activity, classification))

    rep = db.execute(
        select(CoachReport)
        .where(CoachReport.activity_id == activity.id, CoachReport.is_fallback.is_(False))
        .order_by(CoachReport.created_at.desc())
    ).scalars().first()

    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == activity.user_id)
    ).scalars().first()
    rel = db.execute(
        select(CoachingRelationship).where(CoachingRelationship.user_id == activity.user_id)
    ).scalars().first()
    block = db.get(Block, activity.block_id) if activity.block_id else None

    raw = activity.raw_summary or {}
    data = {
        "meta": {
            "activity_id": str(activity.id),
            "prompt_id": PROMPT_ID,
            "schema_version": "2.0",
            "captured": date.today().isoformat(),
        },
        "pack": pack.to_serializable_dict(),
        "llm_view": llm_view,
        "grouped": is_grouped_pack_prompt(PROMPT_ID),
        "derived": _derived(metrics),
        "report": rep.report if rep else None,
        "streams": _streams(db, activity.id),
        "raw_summary": {k: raw.get(k) for k in _RAW_SUMMARY_KEYS},
        "activity": {
            "strava_activity_id": activity.strava_activity_id,
            "name": activity.name,
            "type": activity.user_intent or activity.type,
            "distance_m": activity.distance_m,
            "moving_time_s": activity.moving_time_s,
            "elapsed_time_s": activity.elapsed_time_s,
            "avg_hr": activity.avg_hr,
            "max_hr": activity.max_hr,
            "avg_cadence": activity.avg_cadence,
            "average_speed_mps": activity.average_speed_mps,
            "elev_gain_m": activity.elev_gain_m,
            "start_date": activity.start_date,
            "start_date_local": activity.start_date_local,
        },
        "profile": {f: getattr(profile, f, None) for f in _PROFILE_FIELDS} if profile else {},
        "relationship": {
            "voice_preset": getattr(rel, "voice_preset", None),
            "voice_warmth": getattr(rel, "voice_warmth", None),
            "voice_humor": getattr(rel, "voice_humor", None),
            "voice_directness": getattr(rel, "voice_directness", None),
            "voice_energy": getattr(rel, "voice_energy", None),
            "stance_school": getattr(rel, "stance_school", None),
            "stance_data_sentiment": getattr(rel, "stance_data_sentiment", None),
            "stance_process_outcome": getattr(rel, "stance_process_outcome", None),
            "note": stance_note,
        },
        "block": {
            "id": str(block.id),
            "primary_activity_id": str(block.primary_activity_id) if block.primary_activity_id else None,
        } if block else None,
        "smoothing": _smoothing(db, activity.id),
        # The real COACH_*_ENABLED kill-switch states for THIS capture, so the diagram's
        # #522 "turned off" banners render only when a switch is ACTUALLY off (not as a
        # static claim that contradicts the data chips when everything is on).
        "flags": {
            k: getattr(settings, k)
            for k in dir(settings)
            if k.startswith("COACH_") and k.endswith("_ENABLED")
        },
    }
    return data, system_prompt, activity


def _rewrite(data, system_prompt):
    src = TARGET.read_text()
    data_line = "const DATA = " + json.dumps(data, default=str, separators=(",", ":")) + ";"
    sp_line = "const SYSTEM_PROMPT = " + json.dumps(system_prompt) + ";"
    src = re.sub(r"^const DATA = .*;$", lambda _: data_line, src, count=1, flags=re.M)
    src = re.sub(r"^const SYSTEM_PROMPT = .*;$", lambda _: sp_line, src, count=1, flags=re.M)
    TARGET.write_text(src)


def main():
    _guard_local()
    db = SessionLocal()
    try:
        data, system_prompt, activity = build_data(db)
    finally:
        db.close()
    _rewrite(data, system_prompt)
    pack_sections = list(data["pack"].keys())
    print(f"regenerated flow-nodes.js from activity {activity.id} ({activity.name})")
    def _has(k):
        return "YES" if k in pack_sections else "no"
    llm_sections = list(data["llm_view"].keys())
    print(f"  prompt_id={PROMPT_ID}  grouped={data['grouped']}  flat pack sections={len(pack_sections)}  "
          f"readiness={_has('readiness')} recent_weeks={_has('recent_weeks')} "
          f"intensity_read={_has('intensity_read')} stream_view={_has('stream_view')}")
    print(f"  llm_view top-level keys ({len(llm_sections)}): {llm_sections}")
    print(f"  system prompt: {len(system_prompt):,} chars")


if __name__ == "__main__":
    main()
