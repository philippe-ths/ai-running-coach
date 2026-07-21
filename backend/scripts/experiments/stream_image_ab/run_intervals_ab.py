"""Interval A/B: the REAL production coach prompt, stream-only, numbers vs image.

This is the product-relevant probe for the case where the image should win biggest:
interval sessions, where the 60-point downsample smears reps that a full-resolution
chart keeps. The coach is driven by the ACTUAL production system prompt
(coach_message_lean_grouped_v5, fuller mode) — lightly redacted so it describes only the
stream it is given rather than the full pack — and receives ONLY the run's stream, in one
of two forms:

  A (json):  the 60-point numeric stream_view (production's downsample), as JSON.
  B (image): the full-resolution rendered chart.

The derived interval_structure is deliberately NOT provided, so this tests whether the
coach can READ the intervals from the raw stream in each form. Identical system prompt,
model, thinking, and tool tail across arms; only the stream's form differs.
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
from app.models import Activity, ActivityStream
from sqlalchemy.orm import undefer

from app.services.analysis.classifier import Classification, playbook_key
from app.services.analysis.stream_view import build_stream_view
from app.services.coach.prompts import build_system_prompt
from app.services.coach.output_contract import RECORD_COACH_TAIL_TOOL, parse_blocks, merge_report

from render_chart import render_stream_chart
from run_ab import RecordingClient, ImageClient

OUT_DIR = os.environ.get("EXP_OUT") or os.path.join(os.path.dirname(__file__), "out_intervals")
os.makedirs(OUT_DIR, exist_ok=True)

PROMPT_ID = "coach_message_lean_grouped_v5"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192

# Three real interval sessions (8-rep clean · 31-min lap-marked · 31-min stream-detected).
DEFAULT_ACTIVITIES = [
    ("5f43937f-b5ee-4ed6-86a8-3ec817e593ef", "4.4km / 23min — 8 reps, lap-marked, high-conf"),
    ("ecb90eee-23c0-4adc-8faa-f11501b000b5", "4.9km / 31min — 7 reps, lap-marked, high-conf"),
    ("5b1e9cd6-8a79-4dcf-a03c-503b4bc3e053", "4.9km / 31min — 7 reps, stream-detected, med-conf"),
]

_STREAM_NOTE = (
    "# What you are given\n\n"
    "For this analysis you are given ONLY this run's stream — heart rate, pace, "
    "elevation/grade and cadence over time (as `this_run.stream_view`). Read the run "
    "directly from it; you are not handed pre-computed metrics for this run, so derive "
    "what you need from the stream itself. No other context sections (readiness, recent "
    "training, memory, history, threads, coaching school) are provided — do not reach for "
    "them or remark on their absence.\n\n"
)


def redact_prompt(sp: str) -> str:
    """Light, faithful redaction of the production prompt: replace the 'how your context
    is organized' group enumeration (we provide only the stream) with an honest note, and
    drop the opener/continuity paragraph. Everything else — identity, the grounding rule,
    the numbers-you'd-misread (incl. the interval discipline), the safety lane, the
    delivery protocol, the voice examples — is kept verbatim."""
    a = sp.index("# How your context is organized")
    b = sp.index("# The one rule about what is true")
    sp = sp[:a] + _STREAM_NOTE + sp[b:]
    marker = "If you already sent this runner an opener"
    if marker in sp:
        i = sp.index(marker)
        j = sp.find("\n\n", i)
        sp = sp[:i] + (sp[j + 2:] if j != -1 else "")
    return sp


def build_prompt(activity: Activity) -> str:
    classification = Classification.from_metrics(activity.metrics)
    sp = build_system_prompt(
        PROMPT_ID, playbook_key(activity, classification), mode="fuller", voice=None, pack=None
    )
    return redact_prompt(sp)


@dataclass
class ArmResult:
    input_tokens_counted: Optional[int]
    input_tokens_usage: int
    output_tokens: int
    message: str
    is_fallback: bool
    tail_degraded: bool
    policy_violations: List[str]


def _count(sync_client, system, content) -> Optional[int]:
    try:
        return sync_client.messages.count_tokens(
            model=MODEL, system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": content}], tools=[RECORD_COACH_TAIL_TOOL],
        ).input_tokens
    except Exception as exc:  # noqa: BLE001
        print(f"    count_tokens failed: {exc}")
        return None


async def _generate(client, system, content) -> ArmResult:
    from app.services.coach.output_contract import EmptyMessageError
    for attempt in range(3):  # #217: an empty-prose response is a known transient; retry
        first = None
        try:
            result = await client.generate_coach_message(
                system=system, user=content, tools=[RECORD_COACH_TAIL_TOOL], max_tokens=MAX_TOKENS,
            )
            first = client.calls[-1] if client.calls else None
            parsed = parse_blocks(result.content_blocks)
            report = merge_report(parsed)  # raises EmptyMessageError on tail-only output
            dump = report.model_dump()
            return ArmResult(
                input_tokens_counted=None,
                input_tokens_usage=(first.input_tokens + first.cache_read_input_tokens) if first else 0,
                output_tokens=first.output_tokens if first else 0,
                message=dump.get("message") or "",
                is_fallback=False,
                tail_degraded=bool(dump.get("tail_degraded")),
                policy_violations=[],
            )
        except EmptyMessageError:
            print(f"    empty-prose response, retry {attempt + 1}")
            continue
        except Exception:  # noqa: BLE001
            import traceback; traceback.print_exc()
            break
    return ArmResult(None, 0, 0, "", True, False, [])


async def run_one(db, sync_client, activity_id: str, label: str) -> Optional[Dict[str, Any]]:
    activity = (
        db.query(Activity).options(undefer(Activity.raw_summary))
        .filter(Activity.id == activity_id).first()
    )
    if activity is None:
        print("  not found"); return None
    system = build_prompt(activity)

    rows = db.query(ActivityStream).filter(ActivityStream.activity_id == activity_id).all()
    streams = {r.stream_type: r.data for r in rows}
    sv = build_stream_view(streams)
    if sv is None:
        print("  no stream_view"); return None
    json_content = json.dumps({"this_run": {"stream_view": sv}})

    optimized = os.environ.get("CHART_OPTIMIZED") == "1"
    png = render_stream_chart(
        streams, title="Run stream — HR / pace / elevation+grade / cadence over time",
        optimized=optimized,
    )
    if png is None:
        print("  no chart"); return None
    b64 = base64.b64encode(png).decode()
    image_content = [
        {"type": "text", "text": "this_run.stream_view is provided as the chart image below "
         "(heart rate, pace, elevation with grade, and cadence over time)."},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
    ]

    tok_j = _count(sync_client, system, json_content)
    tok_i = _count(sync_client, system, image_content)
    print(f"  tokens (counted): JSON={tok_j}  IMAGE={tok_i}  "
          f"(delta {None if None in (tok_j, tok_i) else tok_i - tok_j})")

    print("  generating JSON arm...")
    arm_j = await _generate(RecordingClient(api_key=settings.ANTHROPIC_API_KEY, model=MODEL), system, json_content)
    arm_j.input_tokens_counted = tok_j
    print("  generating IMAGE arm...")
    # The ImageClient appends the PNG block; the user text is just the pointer line.
    arm_i = await _generate(ImageClient(api_key=settings.ANTHROPIC_API_KEY, model=MODEL, images=[b64]), system, image_content[0]["text"])
    arm_i.input_tokens_counted = tok_i

    return {
        "activity_id": activity_id, "label": label,
        "chart_png_b64": b64,
        "arm_json": asdict(arm_j), "arm_image": asdict(arm_i),
    }


async def main():
    activities = DEFAULT_ACTIVITIES
    if len(sys.argv) > 1:
        labels = dict(DEFAULT_ACTIVITIES)
        activities = [(a, labels.get(a, a)) for a in sys.argv[1:]]

    print(f"INTERVAL test — prompt={PROMPT_ID} (redacted)  model={MODEL}  out={OUT_DIR}")
    sync_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    db = SessionLocal()
    runs = []
    try:
        for aid, label in activities:
            print(f"\n=== {aid[:8]} | {label} ===")
            t0 = time.time()
            r = await run_one(db, sync_client, aid, label)
            if r:
                runs.append(r)
                print(f"  done in {time.time()-t0:.0f}s")
    finally:
        db.close()

    payload = {
        "prompt_id": f"{PROMPT_ID} (redacted, stream-only)",
        "model": MODEL,
        "setup": "The REAL production coach system prompt (coach_message_lean_grouped_v5, fuller "
                 "mode), lightly redacted to describe only the stream it is given. The coach received "
                 "ONLY this interval run's stream — as a 60-point numeric series or as the chart above "
                 "— with NO derived interval structure, so this tests whether it reads the reps from "
                 "the raw stream in each form. Reports are blind and order-shuffled.",
        "runs": runs,
    }
    out = os.path.join(OUT_DIR, "results.json")
    json.dump(payload, open(out, "w"), indent=2)
    print(f"\nwrote {out} ({len(runs)} runs)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
