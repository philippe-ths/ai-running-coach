"""Blind LLM semantic judge for the stream-representation A/B.

For each run, the judge sees the two coach reports ANONYMIZED and order-randomized
("Report 1" / "Report 2") plus an INDEPENDENT ground-truth digest of the run (derived
metrics + raw stream statistics). It scores each report on insight, specificity, and
faithfulness — and is explicitly told to penalize any shape/terrain claim the stream
does not support, which is the guard against the image arm winning on eloquence while
hallucinating the shape.

Determinism note: order randomization is seeded per-run from the activity id (no
Date/random dependency), so re-running the judge is reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

import anthropic
import numpy as np

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import ActivityStream, DerivedMetric

OUT_DIR = os.environ.get("EXP_OUT") or os.path.join(os.path.dirname(__file__), "out")
JUDGE_MODEL = "claude-opus-4-8"  # a stronger, independent judge than the Sonnet coach

_JUDGE_SYSTEM = """You are a rigorous evaluator of running-coach analysis quality. \
You are given two coach reports written about the SAME run, plus an independent \
ground-truth digest of that run (derived metrics and raw stream statistics). The two \
reports were generated identically EXCEPT for how the run's time-series stream was \
supplied to the coach. Your job is to judge which report is the better coaching read.

Score each report 1-5 on three axes:
- insight: how well it reads the SHAPE and story of the run (pacing, drift, terrain \
response, fade or negative split, effort distribution) rather than restating summary numbers.
- specificity: how concretely it is anchored to THIS run rather than generic advice.
- faithfulness: whether every factual/shape claim is SUPPORTED by the ground-truth digest. \
Penalize hard any claim that contradicts or is unsupported by the digest (e.g. asserting a \
negative split when the stream shows a fade, inventing intervals, misreading the climb). \
List each unsupported claim.

Be skeptical. Eloquence is not insight. A confident report that misreads the run is WORSE \
than a plainer report that reads it correctly. Then pick the overall winner for coaching value.
Call record_judgment exactly once."""

_JUDGE_TOOL = {
    "name": "record_judgment",
    "description": "Record the blind comparison verdict.",
    "input_schema": {
        "type": "object",
        "properties": {
            "report_1": {
                "type": "object",
                "properties": {
                    "insight": {"type": "integer", "minimum": 1, "maximum": 5},
                    "specificity": {"type": "integer", "minimum": 1, "maximum": 5},
                    "faithfulness": {"type": "integer", "minimum": 1, "maximum": 5},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["insight", "specificity", "faithfulness", "unsupported_claims"],
            },
            "report_2": {
                "type": "object",
                "properties": {
                    "insight": {"type": "integer", "minimum": 1, "maximum": 5},
                    "specificity": {"type": "integer", "minimum": 1, "maximum": 5},
                    "faithfulness": {"type": "integer", "minimum": 1, "maximum": 5},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["insight", "specificity", "faithfulness", "unsupported_claims"],
            },
            "winner": {"type": "string", "enum": ["report_1", "report_2", "tie"]},
            "margin": {"type": "string", "enum": ["clear", "slight", "tie"]},
            "rationale": {"type": "string"},
        },
        "required": ["report_1", "report_2", "winner", "margin", "rationale"],
    },
}


def _stream_digest(db, activity_id: str) -> Dict[str, Any]:
    """Independent ground-truth digest: derived metrics + raw stream stats."""
    dm = db.query(DerivedMetric).filter(DerivedMetric.activity_id == activity_id).first()
    rows = db.query(ActivityStream).filter(ActivityStream.activity_id == activity_id).all()
    s = {r.stream_type: r.data for r in rows}

    def arr(key):
        v = s.get(key)
        return np.asarray([x for x in v if x is not None], dtype=float) if v else None

    hr = arr("heartrate")
    vel = arr("velocity_smooth")
    alt = arr("altitude")
    cad = arr("cadence")
    digest: Dict[str, Any] = {}
    if hr is not None and hr.size:
        # first vs last third mean HR — the raw drift signal the judge checks against.
        third = max(1, hr.size // 3)
        digest["hr"] = {
            "min": round(float(hr.min())), "max": round(float(hr.max())),
            "mean": round(float(hr.mean())),
            "first_third_mean": round(float(hr[:third].mean())),
            "last_third_mean": round(float(hr[-third:].mean())),
        }
    if vel is not None and vel.size:
        moving = vel[vel > 0.5]
        if moving.size:
            paces = 1000.0 / moving
            digest["pace_s_per_km"] = {
                "fastest": round(float(paces.min())), "slowest": round(float(paces.max())),
                "mean": round(float(paces.mean())),
            }
    if alt is not None and alt.size:
        gain = float(np.clip(np.diff(alt), 0, None).sum())
        digest["elevation"] = {
            "min_m": round(float(alt.min())), "max_m": round(float(alt.max())),
            "gain_m": round(gain), "start_m": round(float(alt[0])), "end_m": round(float(alt[-1])),
        }
    if cad is not None and cad.size:
        from app.services.units.cadence import cadence_doubling_factor
        pos = cad[cad > 0]
        if pos.size:
            spm = pos * cadence_doubling_factor(float(pos.mean()))
            digest["cadence_spm"] = {
                "mean": round(float(spm.mean())), "min": round(float(spm.min())),
                "max": round(float(spm.max())),
            }
    if dm is not None:
        digest["derived_metrics"] = {
            "structure": dm.structure, "duration_class": dm.duration_class,
            "effort": dm.effort, "hr_drift_pct": dm.hr_drift,
            "pace_variability": dm.pace_variability, "is_hilly": dm.is_hilly,
            "is_race": dm.is_race, "time_in_zones": dm.time_in_zones,
            "risk_level": dm.risk_level, "flags": dm.flags,
        }
        # Interval ground truth (the point of the interval test): the actual rep
        # structure the coach had to read from the raw stream.
        istruct = dm.interval_structure or {}
        segs = istruct.get("work_segments") if isinstance(istruct, dict) else None
        if segs:
            digest["interval_structure"] = {
                "source": istruct.get("source") or "stream-detected",
                "n_work_segments": len(segs),
                "warmup_s": istruct.get("warmup_duration_s"),
                "cooldown_s": istruct.get("cooldown_duration_s"),
                "segments": [
                    {"n": s.get("segment_number"), "duration_s": s.get("duration_s"),
                     "pace_s_per_km": s.get("pace_s_per_km"), "avg_hr": s.get("avg_hr"),
                     "peak_hr": s.get("peak_hr")}
                    for s in segs
                ],
            }
    return digest


def _order(activity_id: str) -> bool:
    """Deterministic per-run coin: True => report_1 is the IMAGE arm."""
    h = hashlib.sha256(activity_id.encode()).hexdigest()
    return int(h[:8], 16) % 2 == 0


def judge_run(client, run: Dict[str, Any], digest: Dict[str, Any]) -> Dict[str, Any]:
    image_is_1 = _order(run["activity_id"])
    msg_json = run["arm_json"]["message"]
    msg_image = run["arm_image"]["message"]
    report_1, report_2 = (msg_image, msg_json) if image_is_1 else (msg_json, msg_image)

    user = (
        f"GROUND-TRUTH DIGEST (independent facts about the run):\n"
        f"{json.dumps(digest, indent=2)}\n\n"
        f"REPORT 1:\n{report_1}\n\n"
        f"REPORT 2:\n{report_2}\n"
    )
    resp = client.messages.create(
        model=JUDGE_MODEL, max_tokens=2048,
        system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
        tools=[_JUDGE_TOOL], tool_choice={"type": "tool", "name": "record_judgment"},
    )
    verdict = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
    if verdict is None:
        return {"error": "no verdict", "activity_id": run["activity_id"]}

    # De-anonymize: map report_1/2 verdict back to json/image arms.
    def arm_of(report_key: str) -> str:
        is_report_1 = report_key == "report_1"
        return "image" if (is_report_1 == image_is_1) else "json"

    winner_arm = None if verdict["winner"] == "tie" else arm_of(verdict["winner"])
    return {
        "activity_id": run["activity_id"],
        "label": run["label"],
        "image_was_report_1": image_is_1,
        "scores": {
            "json": verdict["report_1" if not image_is_1 else "report_2"],
            "image": verdict["report_1" if image_is_1 else "report_2"],
        },
        "winner_arm": winner_arm,
        "margin": verdict["margin"],
        "rationale": verdict["rationale"],
    }


def main():
    results = json.load(open(os.path.join(OUT_DIR, "results.json")))
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    db = SessionLocal()
    judged = []
    try:
        for run in results["runs"]:
            if run["arm_json"]["is_fallback"] or run["arm_image"]["is_fallback"]:
                print(f"  {run['activity_id'][:8]}: an arm is a fallback; skipping judge")
                continue
            digest = _stream_digest(db, run["activity_id"])
            v = judge_run(client, run, digest)
            judged.append(v)
            w = v.get("winner_arm") or "tie"
            print(f"  {run['activity_id'][:8]} [{run['label'][:30]}]: winner={w} ({v.get('margin')})")
    finally:
        db.close()
    out = os.path.join(OUT_DIR, "judgments.json")
    json.dump(judged, open(out, "w"), indent=2)
    # Tally
    tally = {"json": 0, "image": 0, "tie": 0}
    for v in judged:
        tally[v.get("winner_arm") or "tie"] += 1
    print(f"\nsemantic-judge tally: {tally}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
