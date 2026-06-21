#!/usr/bin/env python3
"""Generate flow-data.js for ai-flow.html from the local seeded DB.

Walks every activity that has a non-fallback coach report with a stored
context pack, traces the real values at each block of the pipeline, and
emits `window.FLOW_DATA` (the activity picker list + per-activity per-block
snippets) as a sibling `flow-data.js` the diagram loads.

Usage:
    python docs/diagrams/generate_flow_data.py
    DB_URL=postgresql://coach:coach@localhost:5433/coach python ... (override)

Read-only. Local DB only (refuses a non-localhost target).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg

# Compute the training_volume section live (the same pure builder the Trends page and
# the coach use), so the diagram shows real current vs-norm data even when the newest
# cached report predates the volume-aware prompt (coach_message_v9).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
try:
    from app.services.coach.volume import build_volume_report
except Exception:  # pragma: no cover - diagram degrades to "not available" without it
    build_volume_report = None

DB_URL = os.environ.get("DB_URL", "postgresql://coach:coach@localhost:5433/coach")
OUT = Path(__file__).parent / "flow-data.js"
MAX_ACTIVITIES = int(os.environ.get("MAX_ACTIVITIES", "60"))

# The M8 belief loop + A2c narrative ship disabled-by-default (PR #403): a poisoned
# belief replayed a rest-day fixation into every report. Mirror that off-state in the
# traced snippets so the diagram is honest. Flip to True when #401/#402 re-enable them.
DURABLE_MEMORY_ENABLED = False


def _guard_local(url: str) -> None:
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"refusing non-local DB target: {url!r}")


# --- formatting helpers -------------------------------------------------------

def k(s):  # key span
    return f"<k>{s}</k>"


def v(s):  # value span
    return f"<v>{s}</v>"


def num(x, nd=1):
    if x is None:
        return "null"
    return f"{float(x):.{nd}f}".rstrip("0").rstrip(".") if nd else str(x)


def first_n(arr, n=4):
    if not arr:
        return "[]"
    head = ", ".join(str(x) for x in arr[:n])
    return f"[{head}{', ...' if len(arr) > n else ''}]"


def whole(x):
    if x is None:
        return "n/a"
    f = float(x)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def trunc(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rstrip() + "…"


# --- training_volume snippet (mirrors the Trends-page quick-view cards) -----------

def _fmt_km(m):
    return f"{(m or 0) / 1000:.2f} km"


def _fmt_dur(s):
    s = int(round(s or 0))
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _fmt_int(x):
    return str(int(round(x or 0)))


_VOL_METRICS = [
    ("Distance", "distance_m", _fmt_km),
    ("Time", "moving_time_s", _fmt_dur),
    ("Activities", "sessions", _fmt_int),
    ("Load", "effort_score", _fmt_int),
]


def _metric_val(f, metric):
    return 1.0 if metric == "sessions" else float(getattr(f, metric) or 0)


def _win_sum(facts, lo, hi, metric):
    return sum(_metric_val(f, metric) for f in facts if lo <= f.local_date <= hi)


def _signed_pct(pct):
    return f"{'+' if pct > 0 else ''}{pct}%"


def _volume_snippet(facts, as_of):
    """The 7-day rolling vs-norm read, in the Trends quick-view card shape: each
    metric's current total, vs typical (the 12-week norm), and vs prev (the prior
    7-day period). Computed live so it is real and prompt-independent."""
    if not (build_volume_report and facts and as_of):
        return f"{k('training_volume')}: not available (volume builder not importable)"
    rep = build_volume_report(facts, as_of, "7D")
    norm_by = {m.metric: m for m in rep.rolling.metrics}
    cur_lo, cur_hi = as_of - timedelta(days=6), as_of
    prev_lo, prev_hi = as_of - timedelta(days=13), as_of - timedelta(days=7)
    lines = [f"7-day rolling — {k('vs typical')} = 12-wk norm · {k('vs prev')} = prior 7 days"]
    for label, metric, fmt in _VOL_METRICS:
        mm = norm_by.get(metric)
        cur = mm.current_all if mm else _win_sum(facts, cur_lo, cur_hi, metric)
        head = f"{k(label)}: {v(fmt(cur))}"
        if metric == "sessions" and mm:
            head += f" ({_fmt_int(mm.current_runs)} runs)"
        parts = [head]
        if mm and mm.direction != "no_norm" and mm.norm is not None:
            parts.append(f"typ {v(_signed_pct(round(mm.pct_vs_norm or 0)))} ({fmt(mm.norm)})")
        prev = _win_sum(facts, prev_lo, prev_hi, metric)
        if prev > 0:
            parts.append(f"prev {v(_signed_pct(round((cur - prev) / prev * 100)))} ({fmt(prev)})")
        lines.append("  " + "   ".join(parts))
    return "\n".join(lines)


# --- per-block snippet builders ----------------------------------------------
# Each returns a multi-line string using <k>/<v> tags, mirroring the diagram.

def build_blocks(row: dict) -> dict:
    a = row["activity"]
    raw = row["raw_summary"] or {}
    dm = row["dm"]
    cp = row["context_pack"] or {}
    rep = row["report"] or {}
    rel = row["relationship"] or {}
    streams = row["streams"]  # {type: {"n": int, "head": [...]}}

    tiz = dm.get("time_in_zones") or {}
    disc = dm.get("discount_signals") or {}
    flags = dm.get("flags") or []
    sv = dm.get("stream_view") or {}
    tl = cp.get("training_load") or {}
    pe = cp.get("perceived_effort") or {}
    cal = (cp.get("calibration") or {}).get("hr_drift") or {}
    sal = cp.get("salience") or {}
    nov = sal.get("novelty") or {}
    so = sal.get("safety_override") or {}
    rts = cp.get("recent_training_summary") or {}
    l7 = rts.get("last_7d") or {}
    l28 = rts.get("last_28d") or {}
    adh = cp.get("adherence") or {}
    bf = (cp.get("believed_facts") or {}).get("facts") or []
    narr = (cp.get("narrative") or {}).get("narrative") or ""
    corpus = cp.get("corpus") or {}
    school = corpus.get("school") or {}
    stance = cp.get("stance") or {}
    emph = stance.get("emphasis") or []
    steps = rep.get("next_steps") or []
    step0 = steps[0] if steps else {}
    is_fallback = row["is_fallback"]

    sample = lambda t: first_n((streams.get(t) or {}).get("head"))
    stream_types = ", ".join(sorted(streams.keys())) or "—"
    sn = next((s["n"] for s in streams.values()), 0)

    inflated = disc.get("likely_inflated_by") or []
    temp_c = raw.get("average_temp")
    laps = len(raw.get("laps") or [])

    emph_str = " · ".join(
        f"{e.get('low_pole')}↔{e.get('high_pole')} {v(e.get('value'))} ({e.get('descriptor')})"
        for e in emph
    ) or "—"

    b = {}
    b["strava"] = (
        f"{k('strava_activity_id')}: {v(a['strava_activity_id'])}\n"
        f"{k('name')}: {v(repr(a['name']))}   {k('type')}: {v(a['type'])}\n"
        f"{k('distance')}: {v(str(a['distance_m']) + ' m')}   {k('moving')}: {v(str(a['moving_time_s']) + ' s')}   {k('elapsed')}: {v(str(a['elapsed_time_s']) + ' s')}\n"
        f"{k('avg_hr')}: {v(num(a['avg_hr']))}   {k('max_hr')}: {v(whole(a['max_hr']))}   {k('elev_gain')}: {v(whole(a['elev_gain_m']) + ' m')}\n"
        f"{k('raw_summary.average_temp')}: {v(str(temp_c) + ' °C' if temp_c is not None else 'n/a')}   {k('laps')}: {v(laps)}"
    )
    b["trigger"] = (
        f"webhook {k('aspect_type')}={v('create')}  →  {v('process_new_activity_job')}\n"
        f"activity {v(a['strava_activity_id'])}  →  ingest → analyze → assign block\n"
        f"(PIPELINE_RETRY: 3 attempts @ 60/300/900s)"
    )
    b["ingestion"] = (
        f"{k(str(len(streams)) + ' stream types')} stored, {v(str(sn) + ' samples')} each:\n"
        f"{stream_types}\n"
        f"{k('sync_athlete_zones')} → runner's HR zone bounds refreshed"
    )
    b["activity"] = (
        f"Activity row + ActivityStream:\n"
        f"{k('heartrate')}:        {v(sample('heartrate'))}  ({sn} pts)\n"
        f"{k('velocity_smooth')}:  {v(sample('velocity_smooth'))}\n"
        f"{k('temp')}:            {v(sample('temp'))}"
    )
    zline = " · ".join(f"{z} {tiz.get(z, 0)}" for z in ("Z1", "Z2", "Z3", "Z4", "Z5"))
    b["metrics"] = (
        f"{k('hr_drift')}: {v(num(dm.get('hr_drift')) + ' %')}\n"
        f"{k('pace_variability')}: {v(num(dm.get('pace_variability'), 2))}\n"
        f"{k('time_in_zones')} (sec): {v(zline)}"
    )
    b["classifier"] = (
        f"{k('structure')}: {v(dm.get('structure'))}\n"
        f"{k('effort')}: {v(dm.get('effort'))}\n"
        f"{k('duration_class')}: {v(dm.get('duration_class'))}   {k('is_race')}: {v(str(dm.get('is_race')).lower())}"
    )
    iv = dm.get("interval_structure")
    if iv:
        src = iv.get("source", "?")
        b["intervals"] = (
            f"{k('interval_structure.source')}: {v(src)}\n"
            f"{k('detection_confidence')}: {v((dm.get('workout_match') or {}).get('detection_confidence', '?'))}\n"
            f"interval structure detected on this run."
        )
    else:
        b["intervals"] = (
            f"{k('interval_structure')}: {v('null')}\n"
            f"{laps} recorded lap(s), no bimodal work/rest pattern →\n"
            f"read as a {v(dm.get('structure') or 'continuous')} run, not an interval session."
        )
    b["flags"] = (
        f"{k('flags')}: {v(json.dumps(flags))}\n"
        f"{k('risk_level')}: {v(dm.get('risk_level'))}\n"
        f"{k('discount_signals.likely_inflated_by')}: {v(json.dumps(inflated))}\n"
        f"{k('.temperature_c')}: {v(disc.get('temperature_c', 'n/a'))}  {k('.confidence')}: {v(disc.get('confidence', 'n/a'))}"
    )
    b["effort"] = (
        f"{k('effort_score')}: {v(num(dm.get('effort_score')))}\n"
        f"= Σ (minutes-in-zone × zone-number), Edwards-style.\n"
        f"Cumulative training LOAD — not an intensity verdict."
    )
    b["streamview"] = (
        f"{k('stream_view')}: {v(str(sv.get('n_points', '?')) + ' points')}  (downsampled from {sv.get('source_n', sn)})\n"
        f"{k('keys')}: time_s · hr · pace_s_per_km · grade_pct · cadence_spm"
    )
    b["derived"] = (
        f"{k('structure')} {v(dm.get('structure'))} · {k('effort')} {v(dm.get('effort'))} · {k('effort_score')} {v(num(dm.get('effort_score')))}\n"
        f"{k('hr_drift')} {v(num(dm.get('hr_drift')) + '%')} · {k('pace_var')} {v(num(dm.get('pace_variability'), 2))} · {k('confidence')} {v(dm.get('confidence'))} · {k('risk')} {v(dm.get('risk_level'))}\n"
        f"{k('flags')} {v(json.dumps(flags))} · {k('discount')} {v(json.dumps(inflated))}"
    )
    b["blocks"] = (
        f"{k('block_id')}: {v((row['block_id'] or '—')[:8] + '…' if row['block_id'] else '—')}   {k('members')}: {v(row['block_members'])}"
        + ("  (block-of-one)" if row["block_members"] == 1 else "  (multi-member block)") + "\n"
        f"{k('primary_activity')}: this run"
    )
    if cal.get("calibrated"):
        b["baseline"] = (
            f"{k('calibration.basis')}:\n"
            f"\"this runner's typical HR drift for these conditions is\n"
            f"about {v(num(cal.get('expected_drift_pct')) + '%')} across {v(cal.get('sample_count'))} comparable runs\"\n"
            f"observed {v(num(cal.get('observed_drift_pct')) + '%')} → {v(cal.get('comparison'))}"
        )
    else:
        b["baseline"] = (
            f"{k('calibration.hr_drift.calibrated')}: {v('false')}\n"
            f"bucket still abstaining (< 4 comparable runs);\n"
            f"falls back to the labelled ~5% population drift heuristic."
        )
    b["focus"] = (
        f"pack.{k('activity')} + pack.{k('metrics')} =\n"
        f"this run's DerivedMetric, flattened byte-stably into the pack"
    )
    if tl:
        b["load"] = (
            f"{k('training_load')}: {k('fitness')} {v(num(tl.get('fitness')))} · {k('fatigue')} {v(num(tl.get('fatigue')))} · {k('form')} {v(num(tl.get('form')))}\n"
            f"{k('condition')}: {v(tl.get('condition'))} · {k('trend')}: {v(tl.get('trend'))} · {k('ramp_rate')}: {v(num(tl.get('ramp_rate')))} · {k('samples')}: {v(tl.get('sample_count'))}"
        )
    else:
        b["load"] = f"{k('training_load')}: not emitted (prompt not training-load-aware)"
    # Computed live (real current data, prompt-independent), in the Trends-card shape.
    b["volume"] = _volume_snippet(row.get("all_facts"), row.get("as_of_date"))
    # baseline trend + M9 calibration (both prior-report-INDEPENDENT, still ON).
    ref = (cp.get("calibration") or {}).get("referral")
    bt = (cp.get("longitudinal") or {}).get("baseline_trend")
    if cal.get("calibrated"):
        cline = (
            f"{k('calibration.hr_drift')}: observed {v(num(cal.get('observed_drift_pct')) + '%')} "
            f"vs your typical {v(num(cal.get('expected_drift_pct')) + '%')} "
            f"({cal.get('sample_count')} runs) → {v(cal.get('comparison'))}"
        )
    else:
        cline = f"{k('calibration.hr_drift.calibrated')}: {v('false')} (<4 comparable runs → ~5% heuristic)"
    b["calibration"] = (
        cline + "\n"
        + f"{k('baseline_trend')}: {v('present' if bt else 'abstaining')} · "
        f"{k('referral')}: {v('present' if ref else 'null')}\n"
        + "(M9 stays ON — it carries the non-diagnostic safety referral)"
    )
    # prior-report digest (M4) + adherence (M7): off by default (PR #418).
    if not DURABLE_MEMORY_ENABLED:
        b["adherence"] = (
            f"{v('DISABLED')} — prior-report digest (M4) + adherence (M7) off by default (PR #418).\n"
            f"{k('longitudinal.prior_reports')}: {v('[]')}   {k('adherence.outcomes')}: {v('[]')}\n"
            f"The coach no longer replays prior reports' next_steps or nags about adherence."
        )
    else:
        b["adherence"] = (
            f"{k('recent_training_summary.last_7d')}: {v(str(l7.get('activity_count', '?')) + ' activities')}, {v(num(l7.get('total_distance_m', 0) / 1000) + ' km')}\n"
            f"{k('.last_28d')}: {v(str(l28.get('activity_count', '?')) + ' activities')}, {v(num(l28.get('total_distance_m', 0) / 1000) + ' km')}\n"
            f"{k('adherence.outcomes')}: {v(json.dumps(adh.get('outcomes', [])) if not adh.get('outcomes') else str(len(adh.get('outcomes'))) + ' judged')}"
        )
    if pe:
        b["perceived"] = (
            f"{k('perceived_effort.rpe')}: {v(pe.get('rpe'))}   {k('effort_axis')}: {v(pe.get('effort_axis'))}\n"
            f"{k('divergence')}: {v(pe.get('divergence'))}   {k('hr_confounded')}: {v(str(pe.get('hr_confounded')).lower())}\n"
            f"{k('recommended_weighting')}: {v(pe.get('recommended_weighting'))}"
        )
    else:
        b["perceived"] = f"{k('perceived_effort')}: nulls (no check-in for this run)"
    if not DURABLE_MEMORY_ENABLED:
        b["beliefs"] = (
            f"{v('DISABLED')} — COACH_BELIEFS_ENABLED defaults off (PR #403).\n"
            f"Emits the empty new-runner form; no belief is read or written.\n"
            f"Stored rows kept intact for a recalibrated re-enable (#401)."
        )
    elif bf:
        f0 = bf[0]
        b["beliefs"] = (
            f"{k('believed_facts.facts[0]')}:\n"
            f"\"{trunc(f0.get('statement', ''), 90)}\"\n"
            f"{k('confidence')} {v(f0.get('confidence'))} · {k('observed_count')} {v(f0.get('observed_count'))} · {k('last_seen')} {v(str(f0.get('last_seen_days_ago')) + 'd ago')}"
        )
    else:
        b["beliefs"] = f"{k('believed_facts.facts')}: {v('[]')}  (no quality-cleared beliefs yet)"
    if not DURABLE_MEMORY_ENABLED:
        b["narrative"] = (
            f"{v('DISABLED')} — COACH_NARRATIVE_ENABLED defaults off (PR #403).\n"
            f"The section is empty; the Consolidation job never enqueues. (#402 recalibrates.)"
        )
    else:
        b["narrative"] = (f"\"{trunc(narr, 220)}\"" if narr else f"{k('narrative')}: none yet (first exchanges)")
    b["voice"] = (
        f"{k('voice_preset')}: {v(repr(rel.get('voice_preset')))}  (warmth {v(rel.get('voice_warmth'))} · humor {v(rel.get('voice_humor'))} · directness {v(rel.get('voice_directness'))} · energy {v(rel.get('voice_energy'))})\n"
        f"{k('stance_school')}: {v(rel.get('stance_school'))}\n"
        f"{k('emphasis')}: {emph_str}\n"
        f"{k('corpus.school.stance')}: \"{trunc(school.get('stance', ''), 70)}\""
    )
    b["llmcall"] = (
        f"{k('prompt_id')}: {v(row['prompt_id'])} · {k('schema')}: {v(row['schema_version'])}\n"
        f"{k('message')}: \"{trunc(rep.get('message', ''), 300)}\""
    )
    b["contract"] = (
        f"{k('tail.headline')}: \"{trunc(rep.get('headline', ''), 110)}\"\n"
        + (f"{k('tail.next_steps[0].action')}: \"{trunc(step0.get('action', ''), 80)}\"\n" if step0 else "")
        + (f"{k('.next_steps')}: {v(str(len(steps)) + ' step(s)')}")
    )
    b["validator"] = (
        f"{k('is_fallback')}: {v(str(is_fallback).lower())}  →  {'forced fallback (overreach survived retry)' if is_fallback else 'passed all 8 rules'}\n"
        f"{k('calibration.referral')}: {v('present' if (cp.get('calibration') or {}).get('referral') else 'null')}"
    )
    b["retry"] = (
        f"{v('No retry needed')} — output passed the validator first time.\n"
        if not is_fallback else
        f"{v('Retry exhausted')} — a violation survived the re-generation.\n"
    ) + "(A surviving medical overreach forces is_fallback=true.)"
    b["fallback"] = (
        f"{v('Not triggered')} — generation + parse + policy all succeeded."
        if not is_fallback else
        f"{v('Triggered')} — a safe fallback report was persisted (is_fallback=true)."
    )
    b["coachreport"] = (
        f"{k('key')}: (activity_id, {v(row['prompt_id'])}, {v(row['schema_version'])})\n"
        f"{k('report')}: prose message + structured tail + digest stored\n"
        f"(display-safe read serves this even after a prompt flip)"
    )
    if not DURABLE_MEMORY_ENABLED:
        b["writeback"] = (
            f"{v('DISABLED')} — COACH_BELIEFS_ENABLED off (PR #403).\n"
            f"No belief deltas are written after a report.\n"
            f"Stored rows untouched, so a recalibrated re-enable resumes from history."
        )
    elif bf:
        f0 = bf[0]
        b["writeback"] = (
            f"reinforced belief {k(f0.get('kind', 'adherence_pattern'))}:\n"
            f"\"{trunc(f0.get('statement', ''), 70)}\"\n"
            f"{k('observed_count')} → {v(f0.get('observed_count'))} · derived LLM-free"
        )
    else:
        b["writeback"] = f"belief deltas derived from discount_signals + adherence outcomes (LLM-free)"
    if not DURABLE_MEMORY_ENABLED:
        b["consolidation"] = (
            f"{v('DISABLED')} — COACH_NARRATIVE_ENABLED off (PR #403).\n"
            f"The job is never enqueued; no narrative is re-written."
        )
    else:
        b["consolidation"] = (
            f"{k('CoachNarrative')} re-written by {v('claude-haiku-4-5')}\n"
            f"from deterministic facts + recent digests (voice only)"
        )
    b["notify"] = (
        f"{k('channel')}: {v('Telegram')} (Railway blocks SMTP)\n"
        f"voice {v(repr(rel.get('voice_preset')))} · opener keyboard: {v('RPE / pain / done')} taps\n"
        f"a tap writes a CheckIn and can fire the fuller turn early"
    )
    b["frontend"] = (
        f"renders the prose report + stream charts +\n"
        + (f"{k('Training Load card')}: fitness {v(num(tl.get('fitness')))} / fatigue {v(num(tl.get('fatigue')))} / form {v(num(tl.get('form')))}\n" if tl else "")
        + f"next_steps surface as one-tap chat starter chips"
    )
    b["chat"] = (
        f"follow-up speaks in the {v(repr(rel.get('voice_preset')))} voice, carries the report's\n"
        f"authority disciplines, and injects recent chat from the runner's\n"
        f"OTHER activities — same policy gate, re-streamed."
    )
    return b


# --- DB load -----------------------------------------------------------------

def load(conn) -> tuple[list, dict]:
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    # one relationship (single-user)
    cur.execute("SELECT * FROM coaching_relationship LIMIT 1")
    relationship = cur.fetchone() or {}

    # All activity facts (for the live training_volume computation): the norm needs
    # the full ~91-day history, not just activities that have a coach report.
    cur.execute(
        """
        SELECT a.type, a.distance_m, a.moving_time_s, a.start_date, a.start_date_local,
               dm.effort_score
        FROM activities a
        LEFT JOIN derived_metrics dm ON dm.activity_id = a.id
        WHERE a.is_deleted = false
        """
    )
    all_facts = [
        SimpleNamespace(
            local_date=(f["start_date_local"] or f["start_date"]).date(),
            activity_type=f["type"],
            distance_m=f["distance_m"] or 0,
            moving_time_s=f["moving_time_s"] or 0,
            effort_score=f["effort_score"] or 0,
        )
        for f in cur.fetchall()
    ]

    # latest non-fallback report w/ pack per activity
    cur.execute(
        """
        SELECT DISTINCT ON (a.id)
          a.id, a.strava_activity_id, a.name, a.type, a.start_date, a.start_date_local,
          a.distance_m, a.moving_time_s, a.elapsed_time_s, a.elev_gain_m,
          a.avg_hr, a.max_hr, a.block_id, a.raw_summary,
          cr.report, cr.context_pack, cr.prompt_id, cr.schema_version,
          cr.is_fallback, cr.created_at
        FROM activities a
        JOIN coach_reports cr ON cr.activity_id = a.id
        WHERE a.is_deleted = false AND cr.is_fallback = false
          AND cr.context_pack IS NOT NULL
        ORDER BY a.id, cr.created_at DESC
        """
    )
    rows = cur.fetchall()
    rows.sort(key=lambda r: r["start_date"], reverse=True)
    rows = rows[:MAX_ACTIVITIES]

    activities, real = [], {}
    for r in rows:
        aid = str(r["id"])
        # derived metrics
        cur.execute("SELECT * FROM derived_metrics WHERE activity_id=%s", (r["id"],))
        dm = cur.fetchone() or {}
        # block members
        cur.execute(
            "SELECT count(*) AS n FROM activities WHERE block_id=%s AND is_deleted=false",
            (r["block_id"],),
        )
        members = (cur.fetchone() or {}).get("n", 1)
        # streams (head sample of each type)
        cur.execute(
            "SELECT stream_type, data FROM activity_streams WHERE activity_id=%s", (r["id"],)
        )
        streams = {}
        for s in cur.fetchall():
            data = s["data"]
            streams[s["stream_type"]] = {"n": len(data), "head": data[:4]}

        date = r["start_date"].date().isoformat()
        label = f"{date} · {r['name']} ({r['prompt_id']})"
        activities.append({"id": aid, "label": label, "date": date,
                           "name": r["name"], "prompt": r["prompt_id"]})

        real[aid] = build_blocks({
            "activity": r,
            "raw_summary": r["raw_summary"],
            "dm": dm,
            "context_pack": r["context_pack"],
            "report": r["report"],
            "relationship": relationship,
            "streams": streams,
            "block_id": str(r["block_id"]) if r["block_id"] else None,
            "block_members": members,
            "prompt_id": r["prompt_id"],
            "schema_version": r["schema_version"],
            "is_fallback": r["is_fallback"],
            "all_facts": all_facts,
            "as_of_date": (r["start_date_local"] or r["start_date"]).date(),
        })
    return activities, real


def main() -> None:
    _guard_local(DB_URL)
    with psycopg.connect(DB_URL) as conn:
        activities, real = load(conn)
    payload = {"activities": activities, "real": real}
    OUT.write_text(
        "// AUTO-GENERATED by generate_flow_data.py — do not edit by hand.\n"
        "// Real per-activity pipeline values traced from the seeded local DB.\n"
        "window.FLOW_DATA = " + json.dumps(payload, ensure_ascii=False, indent=0) + ";\n"
    )
    print(f"wrote {OUT} — {len(activities)} activities, {len(real)} traces")


if __name__ == "__main__":
    main()
