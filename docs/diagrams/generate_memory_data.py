#!/usr/bin/env python3
"""Generate memory-data.js for memory-timeline.html from the local seeded DB.

The memory timeline shows how the coach's memory of a run condenses as it ages.
This traces REAL values for that story: the lifetime aggregate (the most-condensed
end state), per-band stream/DerivedMetric coverage (the honest retention picture),
and one representative real activity per time band (newest → recent → season → old).

Emits `window.MEMORY_DATA` as a sibling `memory-data.js` the diagram loads, keyed by
the same chip data-keys the HTML uses, so each block shows real data in its drawer.

Usage:
    python docs/diagrams/generate_memory_data.py
    DB_URL=postgresql://coach:coach@localhost:5433/coach python ...   (override)

Read-only. Local DB only (refuses a non-localhost target).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

DB_URL = os.environ.get("DB_URL", "postgresql://coach:coach@localhost:5433/coach")
OUT = Path(__file__).parent / "memory-data.js"


def _guard_local(url: str) -> None:
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"refusing non-local DB target: {url!r}")


# --- formatting helpers (mirror generate_flow_data.py) ------------------------

def k(s):
    return f"<k>{s}</k>"


def v(s):
    return f"<v>{s}</v>"


def km(m):
    return f"{(m or 0) / 1000:,.0f} km"


def hrs(s):
    return f"{(s or 0) / 3600:,.0f} h"


def num(x, nd=1):
    if x is None:
        return "null"
    return f"{float(x):.{nd}f}".rstrip("0").rstrip(".") if nd else str(x)


def first_n(arr, n=4):
    if not arr:
        return "[]"
    head = ", ".join(str(x) for x in arr[:n])
    return f"[{head}{', ...' if len(arr) > n else ''}]"


# --- queries ------------------------------------------------------------------

def lifetime(cur) -> str:
    cur.execute(
        """
        SELECT min(a.start_date)::date AS since, max(a.start_date)::date AS thru,
               count(*) AS acts,
               count(*) FILTER (WHERE a.type='Run')  AS runs,
               count(*) FILTER (WHERE a.type='Walk') AS walks,
               count(*) FILTER (WHERE a.type NOT IN ('Run','Walk')) AS other,
               sum(a.distance_m) AS dist, sum(a.moving_time_s) AS movs,
               sum(a.elev_gain_m) AS elev, sum(dm.effort_score) AS load
        FROM activities a LEFT JOIN derived_metrics dm ON dm.activity_id=a.id
        WHERE a.is_deleted=false
        """
    )
    r = cur.fetchone()
    cur.execute("SELECT count(*) AS n, count(distinct activity_id) AS acts FROM coach_reports WHERE not is_fallback")
    rep = cur.fetchone()
    # full per-type breakdown (no "other" bucket — show every activity type)
    cur.execute(
        "SELECT type, count(*) AS c FROM activities WHERE is_deleted=false GROUP BY type ORDER BY c DESC"
    )
    types = cur.fetchall()
    span = (r["thru"] - r["since"]).days
    elev = f"{(r['elev'] or 0):,.0f} m"
    load = f"{(r['load'] or 0):,.0f}"
    # wrap the type breakdown across lines of ~5 entries so it stays readable
    parts = [f"{v(t['c'])} {t['type']}" for t in types]
    type_lines = "\n".join(
        "  " + " · ".join(parts[i:i + 5]) for i in range(0, len(parts), 5)
    )
    return (
        f"{k('since')} {v(r['since'])}  {k('through')} {v(r['thru'])}  ({v(str(span) + ' days')})\n"
        f"{k('activities')}: {v(r['acts'])}  across {v(str(len(types)) + ' types')}:\n"
        f"{type_lines}\n"
        f"{k('distance')}: {v(km(r['dist']))}   {k('moving_time')}: {v(hrs(r['movs']))}   {k('elevation')}: {v(elev)}\n"
        f"{k('lifetime_load')} (Σ effort_score): {v(load)}\n"
        f"{k('coach_reports')}: {v(rep['n'])} across {v(rep['acts'])} activities\n"
        f"— the run is now a handful of numbers in these sums."
    )


def coverage(cur) -> dict:
    cur.execute(
        """
        WITH n AS (SELECT max(start_date)::date AS today FROM activities WHERE is_deleted=false)
        SELECT CASE WHEN (SELECT today FROM n)-a.start_date::date <= 14 THEN 'recent'
                    WHEN (SELECT today FROM n)-a.start_date::date <= 182 THEN 'season'
                    ELSE 'older' END AS band,
               count(*) AS acts,
               count(*) FILTER (WHERE EXISTS(SELECT 1 FROM activity_streams s WHERE s.activity_id=a.id)) AS streams,
               count(dm.activity_id) AS dm
        FROM activities a LEFT JOIN derived_metrics dm ON dm.activity_id=a.id
        WHERE a.is_deleted=false GROUP BY 1
        """
    )
    return {row["band"]: row for row in cur.fetchall()}


def pick(cur, where: str, order: str):
    cur.execute(
        f"""
        WITH n AS (SELECT max(start_date)::date AS today FROM activities WHERE is_deleted=false),
        sel AS (
          SELECT a.id, a.strava_activity_id, a.name, a.type, a.start_date::date AS d,
                 (SELECT today FROM n) - a.start_date::date AS age,
                 a.distance_m, a.moving_time_s, a.avg_hr, a.raw_summary,
                 dm.effort_score, dm.structure, dm.effort, dm.hr_drift, dm.risk_level,
                 EXISTS(SELECT 1 FROM coach_reports cr WHERE cr.activity_id=a.id AND not cr.is_fallback) AS has_report,
                 (SELECT count(*) FROM activity_streams s WHERE s.activity_id=a.id) AS stream_types
          FROM activities a LEFT JOIN derived_metrics dm ON dm.activity_id=a.id
          WHERE a.is_deleted=false AND a.type='Run'
        )
        SELECT * FROM sel WHERE {where} ORDER BY {order} LIMIT 1
        """
    )
    return cur.fetchone()


def streams_head(cur, aid) -> dict:
    cur.execute("SELECT stream_type, data FROM activity_streams WHERE activity_id=%s", (aid,))
    out = {}
    for s in cur.fetchall():
        out[s["stream_type"]] = {"n": len(s["data"]), "head": s["data"][:4]}
    return out


# --- quarter view: a 4-segment, type-aware, shape-preserving summary of one run ----
# Which metrics each activity type keeps in its quarter view (the "decide what data
# to chart" call). The chart renders the continuous ones; effort/time are per-quarter
# derived scalars the view would also carry.
CHART_METRICS = {
    "Run": ["pace", "hr"], "Walk": ["pace", "hr"], "Hike": ["pace", "hr"],
    "Ride": ["watts", "hr"], "VirtualRide": ["watts", "hr"],
    "Rowing": ["pace", "watts", "hr"],
}


def _mean(xs, pred=None):
    vals = [x for x in xs if x is not None and (pred(x) if pred else True)]
    return sum(vals) / len(vals) if vals else None


def quarter_view(cur, a) -> dict | None:
    """Split the run's streams into 4 equal-time segments and average the type's
    metrics in each — the real per-quarter shape the planned quarter-view would store."""
    cur.execute("SELECT stream_type, data FROM activity_streams WHERE activity_id=%s", (a["id"],))
    st = {s["stream_type"]: s["data"] for s in cur.fetchall()}
    hr, vel, watts = st.get("heartrate") or [], st.get("velocity_smooth") or [], st.get("watts") or []
    n = max(len(hr), len(vel), len(watts))
    if n < 8:
        return None

    def seg(arr, i):
        if not arr:
            return []
        L = len(arr)
        return arr[L * i // 4:L * (i + 1) // 4]

    qs = []
    for i in range(4):
        v = _mean(seg(vel, i), lambda x: x > 0.5)
        qs.append({
            "hr": round(_mean(seg(hr, i), lambda x: x > 0) or 0) or None,
            "pace": round(1000.0 / v) if v else None,        # seconds per km
            "watts": round(_mean(seg(watts, i)) or 0) or None,
        })
    wanted = CHART_METRICS.get(a["type"], ["hr"])
    have = [m for m in wanted if any(q.get(m) is not None for q in qs)]
    if not have:
        return None
    dur = a["moving_time_s"] or 0
    return {"type": a["type"], "name": a["name"], "metrics": have,
            "quarter_s": round(dur / 4), "quarters": qs}


def act_line(a) -> str:
    raw = a["raw_summary"] or {}
    temp = raw.get("average_temp")
    dist = f"{(a['distance_m'] or 0) / 1000:.2f} km"
    return (
        f"{k('name')}: {v(repr(a['name']))}  {k('date')}: {v(a['d'])}  ({v(str(a['age']) + 'd ago')})\n"
        f"{k('distance')}: {v(dist)}   {k('moving')}: {v(str(a['moving_time_s']) + ' s')}   {k('avg_hr')}: {v(num(a['avg_hr']))}"
        + (f"   {k('temp')}: {v(str(temp) + ' °C')}" if temp is not None else "")
    )


def dm_line(a) -> str:
    return (
        f"{k('structure')} {v(a['structure'])} · {k('effort')} {v(a['effort'])} · "
        f"{k('effort_score')} {v(num(a['effort_score']))} · {k('hr_drift')} {v(num(a['hr_drift']) + '%')} · {k('risk')} {v(a['risk_level'])}"
    )


# --- build --------------------------------------------------------------------

def build(cur) -> dict:
    cov = coverage(cur)
    now = pick(cur, "stream_types > 0", "age")
    recent = pick(cur, "age BETWEEN 5 AND 14 AND stream_types > 0", "age")
    season = pick(cur, "age BETWEEN 30 AND 182", "age")
    older = pick(cur, "age > 182", "age DESC")

    d = {}
    d["lifetime-agg"] = lifetime(cur)

    # ---- NOW (newest, full detail) ----
    sh = streams_head(cur, now["id"])
    sn = next((s["n"] for s in sh.values()), 0)
    d["now-activity"] = act_line(now) + f"\n{k('raw_summary')}: stored verbatim from Strava"
    d["now-streams"] = (
        f"{k(str(len(sh)) + ' stream types')}, {v(str(sn) + ' samples')} each — full resolution\n"
        f"{k('heartrate')}: {v(first_n((sh.get('heartrate') or {}).get('head')))}\n"
        f"{k('velocity_smooth')}: {v(first_n((sh.get('velocity_smooth') or {}).get('head')))}"
    )
    d["now-derived"] = dm_line(now)
    d["now-report"] = f"{k('has_report')}: {v(str(now['has_report']).lower())} — full prose message + structured tail on disk"
    d["now-exchange"] = f"open Exchange for this run's block — repliable window, RPE / pain / done taps"

    # ---- RECENT ----
    if recent:
        sh2 = streams_head(cur, recent["id"])
        sn2 = next((s["n"] for s in sh2.values()), 0)
        d["recent-activity"] = act_line(recent)
        d["recent-streams"] = f"{k(str(len(sh2)) + ' stream types')}, {v(str(sn2) + ' samples')} each — still full per-sample"
        d["recent-derived"] = dm_line(recent) + f"\n{k('stream_view')}: pulled on demand for deep-dive"
        d["recent-digest"] = (
            f"{v('M4 prior-report digest OFF by default')} (PR #418).\n"
            f"This run HAS a report ({v(str(recent['has_report']).lower())}); its digest would shrink to\n"
            f"headline + lead + next_steps — but is not read into context while gated off."
        )

    # ---- SEASON ----
    if season:
        d["season-activity"] = act_line(season)
        d["season-derived"] = dm_line(season) + f"\n→ feeds baselines, training-load EWMA, volume norms"
        d["season-streams"] = (
            f"{k('stream_types')}: {v(season['stream_types'])}  "
            + (f"({v('summary-only import')} — no per-sample streams)" if season["stream_types"] == 0 else "(full streams present)")
        )
        d["season-report"] = f"{k('has_report')}: {v(str(season['has_report']).lower())} (kept on disk, not in any context pack)"

    # ---- OLDER ----
    if older:
        d["old-activity"] = act_line(older) + "\n— summary row, kept, but outside every rolling window"
        d["old-derived"] = dm_line(older) + "\n— stored, but no aggregate reads it any more"
        d["old-streams"] = (
            f"{k('stream_types')}: {v(older['stream_types'])} — {v('summary-only import, never had per-sample streams')}\n"
            f"PLANNED prune is moot for THIS run (no streams to drop); it bites the\n"
            f"recent runs that DO carry streams once they age past the baseline window."
        )

    # ---- band coverage (the honest retention picture) ----
    def cov_line(band, label):
        c = cov.get(band) or {}
        a, s, m = c.get("acts", 0), c.get("streams", 0), c.get("dm", 0)
        pct = f"{(100 * s / a):.0f}%" if a else "0%"
        return f"{label}: {v(a)} acts · {v(str(s) + ' w/ streams')} ({pct}) · {v(str(m) + ' w/ DerivedMetric')}"

    d["_coverage"] = (
        "Real stream / DerivedMetric coverage by band:\n"
        + cov_line("recent", "  recent ≤14d") + "\n"
        + cov_line("season", "  season 15–182d") + "\n"
        + cov_line("older", "  older >182d") + "\n"
        "DerivedMetric survives for every run; raw streams thin out fast."
    )

    # ---- quarter views (real 4-segment shape) for the stream-bearing reps ----
    for key, row in (("q-now", now), ("q-recent", recent), ("q-season", season)):
        if row and row["stream_types"] > 0:
            qv = quarter_view(cur, row)
            if qv:
                d[key] = qv
    return d


def main() -> None:
    _guard_local(DB_URL)
    with psycopg.connect(DB_URL) as conn:
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        data = build(cur)
    OUT.write_text(
        "// AUTO-GENERATED by generate_memory_data.py — do not edit by hand.\n"
        "// Real memory-retention values traced from the seeded local DB.\n"
        "window.MEMORY_DATA = " + json.dumps(data, ensure_ascii=False, indent=0) + ";\n"
    )
    print(f"wrote {OUT} — {len(data)} keys")


if __name__ == "__main__":
    main()
