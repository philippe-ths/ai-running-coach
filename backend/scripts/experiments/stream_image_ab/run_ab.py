"""Stream-representation A/B: numeric stream_view vs rendered chart image.

For each activity, assembles the REAL production generation request
(`_assemble_generation_request`, lean_grouped_v5) once, then generates two arms
that differ in ONLY the stream representation:

  A (control): the pack exactly as production ships it — numeric `stream_view`.
  B (image):   `stream_view` stripped from the pack JSON and replaced by a chart
               image block (full-resolution HR / pace / elevation+grade / cadence).

Everything else — system prompt, every other pack section, model, adaptive thinking,
effort, tool tail, parse/merge/validate — is identical, so the comparison isolates
the stream representation. Input tokens are measured deterministically via
messages.count_tokens (cache-independent) and confirmed against response usage.

Run:
  COACH_PROMPT_ID=coach_message_lean_grouped_v5 \
  PYTHONPATH="$SP/pylibs:$SP/exp:." MPLCONFIGDIR="$SP/mplcache" \
  backend/.venv/bin/python "$SP/exp/run_ab.py"
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import anthropic

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Activity, ActivityStream, DerivedMetric
from sqlalchemy.orm import undefer

from app.services.coach import service as coach_service
from app.services.coach.llm import (
    AnthropicClient,
    MessageResult,
    _cacheable_system,
    _COACH_EFFORT,
    _MESSAGE_TIMEOUT_SECONDS,
)
from app.services.coach.output_contract import RECORD_COACH_TAIL_TOOL

from render_chart import render_stream_chart

OUT_DIR = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT_DIR, exist_ok=True)

# Shape-diverse sample: long hilly (image should help most) -> short high-drift
# fade -> flat steady (within-sample control; the image should NOT win here).
DEFAULT_ACTIVITIES = [
    ("2c24b603-7dc7-4e80-952e-70b3a23c995e", "10.1km / 59min / 169m — long hilly, neg-split"),
    ("0794bb01-de51-4f79-8932-2241dc003fe6", "8.4km / 52min / 151m — long hilly, neg-split"),
    ("660ba34f-be85-4a3c-b2fa-b4d807a6d777", "3.4km / 20min / 40m — short, high HR drift 13.8%"),
    ("256ebb60-2f04-4bf3-ac93-29080155b98e", "3.4km / 21min / 40m — short, high HR drift 14.6%"),
    ("106fb4b6-e5bf-45ec-ae34-76ff591e0f1c", "5.1km / 31min / 5m — FLAT steady (control)"),
    ("41b57d8c-399a-4640-8160-aedfc681d4e3", "5.5km / 34min / 64m — moderate, high pace var"),
]

_IMAGE_POINTER = (
    "\n\n[This run's consolidated stream view is provided as the attached chart image "
    "(heart rate, pace, elevation with grade, and cadence over time) instead of numeric "
    "sample points. Read its overall shape exactly as you would read the numeric stream_view.]"
)


class RecordingClient(AnthropicClient):
    """Control-arm client: production behaviour, but records each call's usage."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls: List[MessageResult] = []

    async def generate_coach_message(self, *, system, user, tools, max_tokens=8192):
        result = await super().generate_coach_message(
            system=system, user=user, tools=tools, max_tokens=max_tokens
        )
        self.calls.append(result)
        return result


class ImageClient(RecordingClient):
    """Image-arm client: identical to production's generate_coach_message SDK call
    (cacheable system, adaptive thinking, effort, tool_choice=auto, tool tail) but the
    user content is a [text, image] block list. This is the ONLY deviation from the
    control path; all downstream parse/merge/validate is the shared production code."""

    def __init__(self, *a, images: Optional[List[str]] = None, **k):
        super().__init__(*a, **k)
        self._images = images or []

    async def generate_coach_message(self, *, system, user, tools, max_tokens=8192):
        import httpx

        content: List[Dict[str, Any]] = [{"type": "text", "text": user}]
        for b64 in self._images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })
        for attempt in range(3):
            try:
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=_cacheable_system(system),
                    messages=[{"role": "user", "content": content}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": _COACH_EFFORT},
                    tool_choice={"type": "auto"},
                    tools=tools,
                    timeout=_MESSAGE_TIMEOUT_SECONDS,
                ) as stream:
                    final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                result = MessageResult(
                    content_blocks=list(final.content),
                    stop_reason=final.stop_reason,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                )
                self.calls.append(result)
                return result
            except (anthropic.APITimeoutError, anthropic.APIConnectionError,
                    httpx.RemoteProtocolError, anthropic.RateLimitError) as exc:
                if attempt == 2:
                    raise
                print(f"    image-arm transient {type(exc).__name__}, retry {attempt+1}")
                await asyncio.sleep(3)


def _load_streams(db, activity_id) -> Dict[str, List[Any]]:
    rows = db.query(ActivityStream).filter(ActivityStream.activity_id == activity_id).all()
    return {r.stream_type: r.data for r in rows}


def _strip_stream_view(pack_dict: dict) -> dict:
    """Remove the stream_view section from a copy of the (grouped or flat) pack dict,
    exactly as a stream-view-off prompt would omit it."""
    d = copy.deepcopy(pack_dict)
    if "this_run" in d and isinstance(d["this_run"], dict):
        d["this_run"].pop("stream_view", None)  # grouped shape
    d.pop("stream_view", None)  # flat shape
    return d


def _count_input_tokens(sync_client, model, system, user_message, images=None) -> Optional[int]:
    """Deterministic, cache-independent input token count for one arm's exact request."""
    if images:
        content: List[Dict[str, Any]] = [{"type": "text", "text": user_message}]
        for b64 in images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })
    else:
        content = user_message
    try:
        resp = sync_client.messages.count_tokens(
            model=model,
            system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": content}],
            tools=RECORD_COACH_TAIL_TOOL and [RECORD_COACH_TAIL_TOOL],
        )
        return resp.input_tokens
    except Exception as exc:  # noqa: BLE001
        print(f"    count_tokens failed: {exc}")
        return None


@dataclass
class ArmResult:
    input_tokens_counted: Optional[int]
    input_tokens_usage: int
    output_tokens: int
    message: str
    is_fallback: bool
    tail_degraded: bool
    policy_violations: List[str]


@dataclass
class RunResult:
    activity_id: str
    label: str
    prompt_id: str
    model: str
    chart_png_b64: str
    arm_json: ArmResult
    arm_image: ArmResult


def _outcome_message(outcome) -> str:
    dump = outcome.report_dump or {}
    return dump.get("message") or dump.get("opener_message") or ""


async def run_one(db, activity_id: str, label: str, sync_client) -> Optional[RunResult]:
    activity = (
        db.query(Activity)
        .options(undefer(Activity.raw_summary))
        .filter(Activity.id == activity_id)
        .first()
    )
    if activity is None:
        print(f"  activity {activity_id} not found; skipping")
        return None

    req = coach_service._assemble_generation_request(db, activity, mode="fuller")
    prompt_id = req.prompt_id
    model = settings.COACH_MODEL_ID

    # Arm A user message = production (numeric stream_view). Arm B strips it.
    user_a = req.user_message
    pack_b = _strip_stream_view(req.pack_dict)
    user_b = coach_service._llm_pack_message(pack_b, prompt_id, mode="fuller") + _IMAGE_POINTER

    streams = _load_streams(db, activity_id)
    png = render_stream_chart(streams, title="Run stream — HR / pace / elevation+grade / cadence over time")
    if png is None:
        print(f"  {activity_id}: no chart; skipping")
        return None
    b64 = base64.b64encode(png).decode()

    # Deterministic token counts (cache-independent).
    tok_a = _count_input_tokens(sync_client, model, req.system_prompt, user_a)
    tok_b = _count_input_tokens(sync_client, model, req.system_prompt, user_b, images=[b64])

    print(f"  tokens (counted): JSON={tok_a}  IMAGE={tok_b}  (delta {None if None in (tok_a,tok_b) else tok_b-tok_a})")

    # Generate both arms through the shared production pipeline.
    client_a = RecordingClient(api_key=settings.ANTHROPIC_API_KEY, model=model)
    client_b = ImageClient(api_key=settings.ANTHROPIC_API_KEY, model=model, images=[b64])

    print("  generating JSON arm...")
    out_a = await coach_service._generate_message(client_a, req.system_prompt, user_a, req.pack, user_id=None)
    print("  generating IMAGE arm...")
    out_b = await coach_service._generate_message(client_b, req.system_prompt, user_b, req.pack, user_id=None)

    def arm(outcome, client, counted) -> ArmResult:
        first = client.calls[0] if client.calls else None
        return ArmResult(
            input_tokens_counted=counted,
            input_tokens_usage=(first.input_tokens + first.cache_read_input_tokens) if first else 0,
            output_tokens=first.output_tokens if first else 0,
            message=_outcome_message(outcome),
            is_fallback=outcome.is_fallback,
            tail_degraded=outcome.tail_degraded,
            policy_violations=list(outcome.policy_violations or []),
        )

    return RunResult(
        activity_id=activity_id,
        label=label,
        prompt_id=prompt_id,
        model=model,
        chart_png_b64=b64,
        arm_json=arm(out_a, client_a, tok_a),
        arm_image=arm(out_b, client_b, tok_b),
    )


async def main():
    activities = DEFAULT_ACTIVITIES
    if len(sys.argv) > 1:
        labels = dict(DEFAULT_ACTIVITIES)
        activities = [(a, labels.get(a, a)) for a in sys.argv[1:]]

    print(f"prompt={settings.COACH_PROMPT_ID}  model={settings.COACH_MODEL_ID}  receipt_cadence={settings.COACH_RECEIPT_CADENCE}")
    sync_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    db = SessionLocal()
    results: List[RunResult] = []
    try:
        for activity_id, label in activities:
            print(f"\n=== {activity_id[:8]} | {label} ===")
            t0 = time.time()
            try:
                r = await run_one(db, activity_id, label, sync_client)
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"  FAILED: {exc}")
                traceback.print_exc()
                continue
            if r is not None:
                results.append(r)
                print(f"  done in {time.time()-t0:.0f}s")
    finally:
        db.close()

    payload = {
        "prompt_id": settings.COACH_PROMPT_ID,
        "model": settings.COACH_MODEL_ID,
        "runs": [
            {
                **{k: v for k, v in asdict(r).items() if k != "chart_png_b64"},
                "chart_png_b64": r.chart_png_b64,
            }
            for r in results
        ],
    }
    out_path = os.path.join(OUT_DIR, "results.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nwrote {out_path} ({len(results)} runs)")


if __name__ == "__main__":
    asyncio.run(main())
