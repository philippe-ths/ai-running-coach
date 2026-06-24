"""
LLM client abstraction — keeps the coach service decoupled from any specific provider.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

# Cap any single Anthropic call. The SDK default is 600s; the RQ worker
# would block on a single hung call for that long otherwise.
_TIMEOUT_SECONDS = 60.0

# The A3 prose-message call runs adaptive thinking before the prose and the
# tool tail, so it needs more wall-clock headroom than the old constrained-JSON
# call. Streaming removes the SDK's non-streaming idle-timeout guard; this is the
# total-call ceiling so a hung call never blocks the worker indefinitely.
_MESSAGE_TIMEOUT_SECONDS = 180.0

# Thinking depth / token spend for the A3 message call. GA on Sonnet 4.6 and
# every Opus tier, so the Sonnet->Opus choice stays a pure COACH_MODEL_ID flip.
# "high" favours coaching quality (the product is the prose); drop to "medium"
# if per-report cost matters more.
_COACH_EFFORT = "high"

# Initial backoff before the single retry on transient failures.
_RETRY_BACKOFF_SECONDS = 1.0


@dataclass
class MessageResult:
    """The raw output of the A3 prose-message call: the response content blocks
    (text + the single tool_use tail, in token order) and the stop_reason. The
    service interprets stop_reason (end_turn = tail skipped -> corrective retry;
    max_tokens = truncated -> retry; refusal -> fallback) and hands the blocks to
    output_contract.parse_blocks. Kept transport-only so the contract/parsing
    logic stays out of the client."""
    content_blocks: List[Any]
    stop_reason: Optional[str]
    # Usage for the per-user budget gate (P2.2); 0 when no usage was returned.
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    """Protocol for LLM clients that return raw JSON strings."""

    async def generate_json(self, system: str, user: str, max_tokens: int) -> str: ...


class AnthropicClient:
    """Anthropic Claude client for JSON generation."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate_json(
        self, system: str, user: str, max_tokens: int = 1024
    ) -> str:
        import anthropic

        max_attempts = 2  # initial + one retry
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    timeout=_TIMEOUT_SECONDS,
                )
                return response.content[0].text
            except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "anthropic_transient_failure",
                    extra={"attempt": attempt + 1, "kind": type(exc).__name__},
                )
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
            except anthropic.APIStatusError as exc:
                # Retry only on 5xx; 4xx (bad request, auth, etc.) is a caller
                # bug and won't get better on retry.
                if exc.status_code >= 500 and attempt + 1 < max_attempts:
                    last_exc = exc
                    logger.warning(
                        "anthropic_5xx_retry",
                        extra={"attempt": attempt + 1, "status": exc.status_code},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
        # Defensive: loop only exits via return or raise above.
        assert last_exc is not None
        raise last_exc

    async def generate_text(
        self, system: str, user: str, max_tokens: int = 512
    ) -> str:
        """Generate a free-text (prose) completion.

        Same transport, retry, and timeout behaviour as `generate_json`; the only
        difference is intent — the caller expects prose, not JSON, so there is no
        parsing downstream. Used by the A2c Consolidation job to write the
        relationship narrative on the cheap Haiku tier.
        """
        return await self.generate_json(system=system, user=user, max_tokens=max_tokens)

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        tool: Dict[str, Any],
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """A structured-output-only call: force the model to emit exactly the given
        tool's input and return it as a dict.

        The containment lever for untrusted input (P4, #285): `tool_choice` is FORCED
        to the named tool and there is NO extended thinking, so the model has no
        free-form text channel at all — its only output is the structured tool input,
        which the caller then validates against a strict schema. Same retry / timeout
        discipline as `generate_json`. Raises ValueError if no matching tool_use block
        is returned (a logic error, not transient — not retried here; the caller
        treats it as a failed distillation).
        """
        import anthropic

        tool_name = tool["name"]
        max_attempts = 2  # initial + one retry on transient transport failure
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool_name},
                    timeout=_TIMEOUT_SECONDS,
                )
                for block in response.content:
                    btype = (
                        block.get("type") if isinstance(block, dict)
                        else getattr(block, "type", None)
                    )
                    bname = (
                        block.get("name") if isinstance(block, dict)
                        else getattr(block, "name", None)
                    )
                    if btype == "tool_use" and bname == tool_name:
                        tool_input = (
                            block.get("input") if isinstance(block, dict)
                            else getattr(block, "input", None)
                        )
                        return dict(tool_input) if isinstance(tool_input, dict) else {}
                raise ValueError(f"no {tool_name} tool_use block in response")
            except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "anthropic_structured_transient_failure",
                    extra={"attempt": attempt + 1, "kind": type(exc).__name__},
                )
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempt + 1 < max_attempts:
                    last_exc = exc
                    logger.warning(
                        "anthropic_structured_5xx_retry",
                        extra={"attempt": attempt + 1, "status": exc.status_code},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def generate_coach_message(
        self,
        *,
        system: str,
        user: str,
        tools: List[Dict[str, Any]],
        max_tokens: int = 8192,
    ) -> MessageResult:
        """The A3 single call: adaptive thinking -> prose message -> one tool tail.

        Built to the strictest (Opus) parameter surface so the Sonnet/Opus choice
        stays a pure COACH_MODEL_ID flip: no sampling params (temperature/top_p/
        top_k 400 on Opus 4.x), adaptive thinking, and tool_choice=auto (forced
        tool choice is an API error with extended thinking and would also suppress
        the preceding prose). Streams and takes the final message so a large
        max_tokens (thinking tokens count against it) does not hit an HTTP idle
        timeout. Returns the content blocks + stop_reason; the caller parses and
        interprets them.
        """
        import anthropic

        max_attempts = 2  # initial + one retry on transient transport failure
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": _COACH_EFFORT},
                    tool_choice={"type": "auto"},
                    tools=tools,
                    timeout=_MESSAGE_TIMEOUT_SECONDS,
                ) as stream:
                    final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                return MessageResult(
                    content_blocks=list(final.content),
                    stop_reason=final.stop_reason,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                )
            except (
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
                # httpx.RemoteProtocolError is raised mid-stream when the peer
                # closes the connection before the chunked response body is
                # complete ("incomplete chunked read"). The SDK wraps httpx
                # errors that occur during the *initial* HTTP call into
                # APIConnectionError, but errors that surface during SSE
                # stream iteration (inside stream.get_final_message()) escape
                # unwrapped. Treat them as the same transient transport class
                # so they follow the same retry-then-propagate path (#302).
                httpx.RemoteProtocolError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "anthropic_message_transient_failure",
                    extra={"attempt": attempt + 1, "kind": type(exc).__name__},
                )
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempt + 1 < max_attempts:
                    last_exc = exc
                    logger.warning(
                        "anthropic_message_5xx_retry",
                        extra={"attempt": attempt + 1, "status": exc.status_code},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def stream_chat(
        self,
        system: str,
        messages: List[dict],
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Stream text deltas for a multi-turn conversation."""
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.3,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
