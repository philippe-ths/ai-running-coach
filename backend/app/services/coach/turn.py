"""The coaching-turn envelope (#801): what every LLM-backed coaching turn shares.

Four call sites generate coaching text from an LLM — the single-shot report, the
A4 opener, the A4 fuller turn, and the thread turn. The pack assembly they share
was already factored; everything WRAPPED around it was not, so each one
re-assembled the same envelope and a cross-cutting concern had to be remembered
four times. One of them forgot: the thread turn checked the spend cap and never
recorded against it (#786), so the most token-hungry path the product has — a
multi-round tool loop resending the full relationship baseline — spent unmetered.
That is not a property of that path; it is what happens when the envelope is a
convention rather than a module.

This module owns the parts of a turn that are not the coaching:

- WHICH MODEL a turn runs on. Conversational turns take `COACH_CHAT_MODEL_ID`
  when set, everything else `COACH_MODEL_ID`; that resolution lives here and
  nowhere else.
- THE CLIENT a turn is handed, which is a `MeteredClient`: it records every
  call's spend on the per-user budget counter. Recording is a property of the
  client rather than of each call site, so a surface built on it later cannot
  spend unmetered — that is the structural fix for #786, not a patch to the one
  path that forgot.
- THE SPEND GATE, observed BEFORE any tokens are spent, so a capped turn
  degrades to its deterministic answer rather than overshooting the ceiling.
- THE RELATIONSHIP READ, gated once on `COACH_RELATIONSHIP_ENABLED` (#522). The
  report path honoured that switch and the thread turn did not, so with the
  switch off the report spoke in the default voice while the conversation spoke
  in the runner's declared one (#791). One owner, one gate.

What genuinely VARIES between the four — the generation shape (a single
adaptive-thinking call vs a bounded streaming tool loop), the output contract
(prose + tail vs constrained JSON vs free conversational text), and what a
failed policy check does (the report forces a fallback, the conversation
substitutes a safe redirect) — deliberately stays with the callers. Forcing
those into one code path would make this module's interface as complex as its
implementation, which is the shallowness the epic is fighting.

NOT in scope here: the four auxiliary Haiku paths (the material distiller, the
runner-memory update, the receipt-voice generator, and the thread-title writer in
`jobs/thread_maintenance.py`). Those are background, regenerable artifacts on a
hardcoded cheap model, and they gate SOFTLY or not at all — a background pass
must never hard-fail on budget — so they keep their own explicit record calls.
The distinction is deliberate; see #607.

One site outside this module still reads `settings.COACH_MODEL_ID` directly:
`service._persist_report`'s `CoachReportMeta.model_id` stamp. It is provenance
written after the fact, not a selection, and it resolves to the same value for
every report kind — but the honest fix is to stamp the model the turn ACTUALLY
ran on (`client.model`), which means threading the client into persist. Left
alone here and recorded rather than changed, because it is a stored-metadata
change and this phase is behaviour-preserving.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach import budget
from app.services.coach.llm import AnthropicClient, ChatTurnDelta, MessageResult, Usage

logger = logging.getLogger(__name__)


class TurnKind(str, Enum):
    """Which coaching turn is being generated.

    The kind selects the model lane and tags the logs; it deliberately does NOT
    select a generation shape, because the shapes are the callers' own.
    """

    REPORT = "report"    # the single-shot report (either output family)
    OPENER = "opener"    # A4 stage one
    FULLER = "fuller"    # A4 stage two, and the on-demand path
    THREAD = "thread"    # a conversational turn in a Thread (ADR 0027)
    VOICE = "voice"      # the voice rewrite over a finished report (#822)


# Each lane that is not the report's own. `COACH_CHAT_MODEL_ID` exists so a chat
# turn can run on a cheaper/faster model than the report; `COACH_VOICE_MODEL_ID`
# so the rewrite can be steered independently. Both fall back to COACH_MODEL_ID
# when unset, so day-one behaviour is byte-identical.
_MODEL_LANES = {
    TurnKind.THREAD: lambda: settings.chat_model_id,
    TurnKind.VOICE: lambda: settings.voice_model_id,
}


def resolve_model(kind: TurnKind) -> str:
    """The model id a turn of this kind runs on. The ONE place that decision is
    made, so a new surface cannot quietly pick a different setting."""
    lane = _MODEL_LANES.get(kind)
    return lane() if lane else settings.COACH_MODEL_ID


class MeteredClient:
    """An `AnthropicClient` whose every call is recorded on the per-user budget
    counter (#786).

    Wraps rather than subclasses, deliberately: a test that patches an
    `AnthropicClient` method still has its call metered, because the metering
    sits OUTSIDE the method being patched. That is what makes "every coaching
    turn records its spend" testable rather than merely intended.

    Recording is best-effort and never raises: `budget.record` swallows backend
    errors, and it is a no-op when no budget window is configured. A cost cap
    must never become an availability risk.

    `user_id` is the ACTIVITY OWNER (or the thread's runner), never a
    model-supplied id, so one runner's spend can never accrue against another.
    A None user_id means "nobody to bill" and metering is skipped — the shape the
    report path used before P2.2 wired the owner through.
    """

    def __init__(self, inner: AnthropicClient, user_id: Any) -> None:
        self._inner = inner
        self._user_id = user_id

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def inner(self) -> AnthropicClient:
        """The unmetered client, for the rare caller that needs the raw seam."""
        return self._inner

    def _record(self, usage: Any) -> None:
        if self._user_id is None or usage is None:
            return
        budget.record(
            self._user_id,
            self._inner.model,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(
                usage, "cache_creation_input_tokens", 0
            ) or 0,
        )

    async def generate_coach_message(
        self,
        *,
        system: str,
        user: str,
        tools: List[Dict[str, Any]],
        max_tokens: int = 8192,
    ) -> MessageResult:
        result = await self._inner.generate_coach_message(
            system=system, user=user, tools=tools, max_tokens=max_tokens
        )
        # Every SUB-call, not just the first: the truncation/tail-skip/policy-fix
        # fan-out is the cost lever the going-live doc flags.
        self._record(result)
        return result

    async def generate_json_with_usage(
        self, *, system: str, user: str, max_tokens: int = 1024
    ) -> tuple[str, Usage]:
        text, usage = await self._inner.generate_json_with_usage(
            system=system, user=user, max_tokens=max_tokens
        )
        self._record(usage)
        return text, usage

    async def generate_json(
        self, system: str, user: str, max_tokens: int = 1024
    ) -> str:
        """The plain-text form, routed through the usage-bearing sibling so the
        legacy structured report path is metered too. It gated on the cap and
        never recorded — the same defect as #786, one prompt family over."""
        text, _usage = await self.generate_json_with_usage(
            system=system, user=user, max_tokens=max_tokens
        )
        return text

    async def generate_structured_with_usage(
        self,
        *,
        system: str,
        user: str,
        tool: Dict[str, Any],
        max_tokens: int = 1024,
    ) -> tuple[Dict[str, Any], Usage]:
        result, usage = await self._inner.generate_structured_with_usage(
            system=system, user=user, tool=tool, max_tokens=max_tokens
        )
        self._record(usage)
        return result, usage

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        tool: Dict[str, Any],
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        result, _usage = await self.generate_structured_with_usage(
            system=system, user=user, tool=tool, max_tokens=max_tokens
        )
        return result

    async def stream_chat_turn(
        self,
        *,
        system: str,
        messages: List[dict],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[ChatTurnDelta]:
        """Stream a conversational turn, recording the spend of EACH round.

        Per round rather than once at the end of the turn, so any surface built
        on the bounded tool loop is metered by construction — including a turn
        that fails mid-loop, whose earlier rounds were really spent. The deltas
        pass through untouched, so the buffering and heartbeat behaviour the
        caller depends on is unchanged.
        """
        async for delta in self._inner.stream_chat_turn(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        ):
            if delta.final is not None:
                self._record(delta.final)
            yield delta


# What a generation helper accepts: the metered client a coaching turn is handed,
# or a bare client (the aux paths, and tests that inject their own transport).
CoachClient = Union[AnthropicClient, MeteredClient]


def build_client(kind: TurnKind, user_id: Any) -> MeteredClient:
    """The client for one coaching turn: the right model, metered to its runner.

    Every coaching call site goes through here, so "which model" and "who pays"
    are decided once rather than at each site.
    """
    return MeteredClient(
        AnthropicClient(
            api_key=settings.ANTHROPIC_API_KEY, model=resolve_model(kind)
        ),
        user_id,
    )


def over_budget(user_id: Any) -> bool:
    """The spend gate every coaching turn observes BEFORE spending tokens.

    Degrades to False on any backend error (the cap must never become an
    availability risk), so a caller can treat False as "proceed" unconditionally.
    """
    return budget.over_budget(user_id)


def relationship_for_user(
    db: Session, user_id: Any
) -> Optional[CoachingRelationship]:
    """The runner's thin `CoachingRelationship` row, or None.

    The ONE gated read (#522/#791). Returns None both when the row does not exist
    (a runner who never opened their profile) and when COACH_RELATIONSHIP_ENABLED
    is off, so a missing row and a disabled input are indistinguishable by design
    and every resolver maps None to its moderate/default profile.

    It is one function because it was two: the report path checked the switch and
    the thread turn queried the row directly, so with the switch off the report
    spoke in the default voice while the conversation spoke in the runner's
    declared one. A guard on a shared READ cannot be walked around by a caller
    that has its own read; there is now only one read.
    """
    if not settings.COACH_RELATIONSHIP_ENABLED:
        return None
    return (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == user_id)
        .first()
    )
