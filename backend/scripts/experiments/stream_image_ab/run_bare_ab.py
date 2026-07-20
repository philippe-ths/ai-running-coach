"""Bare stream-modality A/B: the model gets ONLY the stream + a one-line instruction.

No coach system prompt, no context pack, no derived metrics — just the run's stream in
one of two forms and the instruction "Analyse the run and report." This isolates the
modality question completely: can a capable model read a run better from numbers or a
picture, when the stream is the entire input? Because there is no pack to draw on, any
claim the analysis makes that the stream doesn't support is a genuine hallucination, so
the judge's faithfulness axis is clean.

  A (json):  the 60-point numeric stream_view (production's downsample), as JSON.
  B (image): the full-resolution rendered chart.

Identical model + params both arms; the only difference is the stream's form.

Run:
  EXP_OUT=<dir> PYTHONPATH="$SP/pylibs:$SP/exp:." MPLCONFIGDIR="$SP/mplcache" \
  backend/.venv/bin/python "$SP/exp/run_bare_ab.py" [activity_id ...]
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import anthropic

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import ActivityStream
from app.services.analysis.stream_view import build_stream_view

from render_chart import render_stream_chart
from run_ab import DEFAULT_ACTIVITIES  # reuse the shape-diverse sample + labels

OUT_DIR = os.environ.get("EXP_OUT") or os.path.join(os.path.dirname(__file__), "out_bare")
os.makedirs(OUT_DIR, exist_ok=True)

MODEL = "claude-sonnet-4-6"
# Neutral instruction: analyse the run, but never describe the DATA or how it was
# supplied, and write prose (no tables). This keeps the two arms blind — otherwise the
# numeric arm prints "60 samples / 3,546 source points" and the image arm says "the
# chart shows", each revealing its modality. It constrains delivery, not analysis.
INSTRUCTION = (
    "Analyse this run and report on it for the runner: pacing, heart rate, effort, "
    "terrain, and anything notable about how the run unfolded. Write a concise, flowing "
    "prose analysis (no tables, no bullet-point data dumps). Write ONLY about the run "
    "itself — never mention the data or how it was provided to you: no references to "
    "samples, data points, counts, resolution, sampling interval, charts, graphs, "
    "images, tables, or format."
)
MAX_TOKENS = 3000  # headroom so neither arm truncates


@dataclass
class ArmResult:
    input_tokens_counted: Optional[int]
    input_tokens_usage: int
    output_tokens: int
    message: str
    is_fallback: bool
    tail_degraded: bool
    policy_violations: List[str]


def _count(client, content) -> Optional[int]:
    try:
        return client.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": content}]
        ).input_tokens
    except Exception as exc:  # noqa: BLE001
        print(f"    count_tokens failed: {exc}")
        return None


def _generate(client, content) -> ArmResult:
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return ArmResult(
            input_tokens_counted=None,
            input_tokens_usage=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            message=text, is_fallback=False, tail_degraded=False, policy_violations=[],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    generate failed: {exc}")
        return ArmResult(None, 0, 0, "", True, False, [])


def run_one(db, client, activity_id: str, label: str) -> Optional[Dict[str, Any]]:
    rows = db.query(ActivityStream).filter(ActivityStream.activity_id == activity_id).all()
    streams = {r.stream_type: r.data for r in rows}

    sv = build_stream_view(streams)
    if sv is None:
        print("  no stream_view; skipping")
        return None
    json_content = f"{INSTRUCTION}\n\n{json.dumps(sv)}"

    png = render_stream_chart(streams, title="Run stream — HR / pace / elevation+grade / cadence over time")
    if png is None:
        print("  no chart; skipping")
        return None
    b64 = base64.b64encode(png).decode()
    image_content = [
        {"type": "text", "text": INSTRUCTION},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
    ]

    tok_j = _count(client, json_content)
    tok_i = _count(client, image_content)
    print(f"  tokens (counted): JSON={tok_j}  IMAGE={tok_i}  "
          f"(delta {None if None in (tok_j, tok_i) else tok_i - tok_j})")

    print("  generating JSON arm...")
    arm_j = _generate(client, json_content)
    arm_j.input_tokens_counted = tok_j
    print("  generating IMAGE arm...")
    arm_i = _generate(client, image_content)
    arm_i.input_tokens_counted = tok_i

    return {
        "activity_id": activity_id, "label": label,
        "chart_png_b64": b64,
        "arm_json": asdict(arm_j), "arm_image": asdict(arm_i),
    }


def main():
    activities = DEFAULT_ACTIVITIES
    if len(sys.argv) > 1:
        labels = dict(DEFAULT_ACTIVITIES)
        activities = [(a, labels.get(a, a)) for a in sys.argv[1:]]

    print(f"BARE test — model={MODEL}  instruction={INSTRUCTION!r}  out={OUT_DIR}")
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    db = SessionLocal()
    runs = []
    try:
        for aid, label in activities:
            print(f"\n=== {aid[:8]} | {label} ===")
            t0 = time.time()
            r = run_one(db, client, aid, label)
            if r:
                runs.append(r)
                print(f"  done in {time.time()-t0:.0f}s")
    finally:
        db.close()

    payload = {
        "prompt_id": "(bare)",
        "model": MODEL,
        "setup": "No coach pack or system prompt. The model received ONLY the run's stream — as a "
                 "60-point numeric series or as the chart above — and was asked to analyse the run in "
                 "prose without ever mentioning the data format, so the two reports stay blind. The "
                 "stream is the entire input, so any unsupported claim is a genuine misread.",
        "runs": runs,
    }
    out = os.path.join(OUT_DIR, "results.json")
    json.dump(payload, open(out, "w"), indent=2)
    print(f"\nwrote {out} ({len(runs)} runs)")


if __name__ == "__main__":
    main()
