"""Consolidation (A2c) — the background job that maintains the durable-memory
NARRATIVE.

The narrative is the LLM-written, voice-only half of durable memory (ADR 0008).
This module re-derives it from the DETERMINISTIC facts (beliefs, baseline norms,
derived preferences) plus the recent exchange digests, every run, so the story
self-corrects against ground truth instead of drifting. It is decoupled from
producing the exchange (the user-facing report never waits on it): the report
path enqueues `consolidate_narrative_job`, which runs here in the RQ worker.

The load-bearing boundary (enforced in code AND in prompt rule 24): the narrative
can never override a re-derived `DerivedMetric` or a deterministic fact, and is
never cited as fact. To keep the re-grounding STRUCTURAL, the prior narrative is
deliberately NOT fed back in — consolidation rebuilds the story from the current
facts, so a stale claim it can no longer support simply disappears. The only
thing this job writes is the narrative row; it never writes a belief, baseline,
or any deterministic fact (those stay off the LLM and auditable).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import RunnerBaseline
from app.services.coach.budget import record as budget_record
from app.services.coach.belief_store import retrieve_beliefs
from app.services.coach.llm import AnthropicClient
from app.services.coach.narrative_store import upsert_narrative
from app.services.coach.preference import build_preference_profile
from app.services.coach.retrieval import fetch_recent_user_digests

logger = logging.getLogger(__name__)

# Hardcoded by design (owner decision): the narrative is a summarise-from-
# structured-facts task, so the cheap Haiku tier is the right model and there is
# deliberately no config knob to change it. The existing AnthropicClient surface
# (model/max_tokens/temperature/system/messages) is fully compatible with Haiku
# 4.5 — no effort/budget_tokens/adaptive-thinking params, which Haiku rejects.
CONSOLIDATION_MODEL_ID = "claude-haiku-4-5"

# How many recent exchanges feed the story, and the prose ceiling. Kept small:
# the narrative is a bounded few-sentence arc, not a transcript.
_RECENT_EXCHANGES = 5
_MAX_NARRATIVE_TOKENS = 400


_SYSTEM_PROMPT = """You maintain a running coach's NARRATIVE about ONE runner: a short, \
durable story of the coaching relationship so far. This is the relationship's memory of \
voice and arc, nothing more.

You are given DETERMINISTIC FACTS (durable beliefs the coach has learned, the runner's \
baseline norms, and their advice-adherence preferences) and DIGESTS of the most recent \
coaching exchanges (each with its date, headline, the lead argument, and the next steps \
that were recommended). Re-write the narrative from scratch from ONLY this material.

WRITE: 2-4 sentences capturing the arc of the relationship so far, the tone that seems to \
land, what the runner tends to act on, and any open thread the coach is carrying forward.

ABSOLUTE RULES:
- This narrative is VOICE ONLY. It is colour, never data. It must never be read as a fact \
or a number, and a later coach will be told never to cite it as evidence.
- Ground every sentence in the supplied facts/digests. Invent NOTHING — no metrics, no \
numbers, no events, no diagnoses that are not present in the input.
- Do NOT give medical advice or training instructions, and do NOT address the runner in \
the second person as if coaching them now. You are writing the coach's private memory of \
the relationship, in the third person ("this runner ...").
- If there is little to say, say little. A single honest sentence beats a padded paragraph.

Output the narrative as plain prose only: no JSON, no headings, no preamble, no quotes."""


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def assemble_consolidation_inputs(db: Session, user_id) -> dict:
    """Gather the DETERMINISTIC facts + recent exchange digests the narrative is
    grounded from. Pure data-gathering; reads existing rows only and never the
    prior narrative (re-grounding is structural — see module docstring)."""
    uid = _as_uuid(user_id)

    beliefs = retrieve_beliefs(db, uid)
    belief_facts = [
        {
            "kind": b.kind,
            "statement": (b.value or {}).get("statement", ""),
            "confidence": b.confidence,
        }
        for b in beliefs
    ]

    preference = build_preference_profile(
        [b for b in beliefs if b.kind == "adherence_pattern"]
    )
    preference_themes = [
        {"theme": t.theme, "tendency": t.tendency, "acted": t.acted, "total": t.total}
        for t in preference.themes
    ]

    baseline: Optional[RunnerBaseline] = (
        db.query(RunnerBaseline).filter(RunnerBaseline.user_id == uid).first()
    )
    baseline_facts = {}
    if baseline is not None:
        for field in (
            "typical_easy_hr",
            "typical_easy_pace_s_per_km",
            "typical_long_run_duration_s",
            "typical_weekly_distance_m",
            "typical_weekly_run_count",
            "sample_count",
        ):
            value = getattr(baseline, field, None)
            if value is not None:
                baseline_facts[field] = value

    digests = fetch_recent_user_digests(db, uid, limit=_RECENT_EXCHANGES)
    recent_exchanges = [
        {
            "date": d.activity_date,
            "headline": d.headline,
            "lead_argument": d.lead_argument,
            "next_steps": d.next_steps,
        }
        for d in digests
    ]

    return {
        "beliefs": belief_facts,
        "preferences": preference_themes,
        "baseline": baseline_facts,
        "recent_exchanges": recent_exchanges,
    }


def render_consolidation_messages(inputs: dict) -> tuple[str, str]:
    """Render the (system, user) messages for the narrative call. Pure."""
    return _SYSTEM_PROMPT, json.dumps(inputs, default=str)


def _latest_grounded_through(inputs: dict) -> Optional[datetime]:
    """The date of the most recent exchange the narrative is grounded from, parsed
    from the digest's ISO `activity_date`. Best-effort; None if unparseable."""
    exchanges = inputs.get("recent_exchanges") or []
    if not exchanges:
        return None
    raw = exchanges[0].get("date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


async def consolidate_narrative(
    db: Session, user_id, *, client: Optional[AnthropicClient] = None
):
    """Re-derive and persist the runner's narrative from current facts + recent
    exchanges. Returns the stored `CoachNarrative` row, or None when it skipped.

    Skips (returns None) when there are no exchanges to narrate yet, or when no
    Anthropic key is configured. Best-effort throughout: any failure logs and
    leaves the prior narrative untouched, because consolidation is decoupled from
    the user-facing turn and must never surface as an error there.
    """
    inputs = assemble_consolidation_inputs(db, user_id)
    if not inputs["recent_exchanges"]:
        return None  # nothing has happened to narrate yet

    if client is None:
        if not settings.ANTHROPIC_API_KEY:
            logger.info("consolidation skipped for user %s: no ANTHROPIC_API_KEY", user_id)
            return None
        client = AnthropicClient(
            api_key=settings.ANTHROPIC_API_KEY, model=CONSOLIDATION_MODEL_ID
        )

    system, user = render_consolidation_messages(inputs)
    try:
        text, usage = await client.generate_text_with_usage(
            system=system, user=user, max_tokens=_MAX_NARRATIVE_TOKENS
        )
        narrative = text.strip()
    except Exception:  # noqa: BLE001 — decoupled background work, never raises out
        logger.exception("narrative generation failed for user %s", user_id)
        return None

    # Record the Haiku spend on the per-user budget counter (#472). Best-effort;
    # record() is a no-op when budgets are unconfigured.
    budget_record(user_id, client.model, usage.input_tokens, usage.output_tokens)

    if not narrative:
        return None

    return upsert_narrative(
        db,
        user_id,
        narrative=narrative,
        model_id=CONSOLIDATION_MODEL_ID,
        source_report_count=len(inputs["recent_exchanges"]),
        grounded_through=_latest_grounded_through(inputs),
    )


def enqueue_consolidation(user_id) -> None:
    """Enqueue the Consolidation job for `user_id`, decoupled from the caller.

    Best-effort: a Redis hiccup or missing queue must never break report storage
    (the report is the record; the narrative is a derived convenience that the
    next exchange can rebuild). Mocked in tests to assert the enqueue without a
    live Redis.
    """
    try:
        from app.core.queue import queue
        from app.jobs.consolidation import consolidate_narrative_job

        queue.enqueue(consolidate_narrative_job, str(user_id))
    except Exception:  # noqa: BLE001 — enqueue is fire-and-forget
        logger.exception("failed to enqueue consolidation for user %s", user_id)
