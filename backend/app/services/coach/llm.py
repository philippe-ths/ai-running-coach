"""
LLM client abstraction — keeps the coach service decoupled from any specific provider.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

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

# Transient (timeout / connection / 5xx) retry budget: initial attempt + this
# many retries. Kept at 1 to preserve the file's prior "initial + one retry".
_MAX_TRANSIENT_RETRIES = 1

# 429 / rate-limit retry budget (#603). A burst of concurrent generations
# (worker fuller turns + web-process chat) can trip Anthropic's per-minute
# limit; a 429 is retriable (unlike a 4xx bug) because capacity frees up. We
# honor the Retry-After header when present, else fall back to exponential
# backoff, and cap both so a single report generation stays well under the RQ
# ~600s death penalty. Worst case: _MAX_RATE_LIMIT_RETRIES * cap seconds of
# sleep before propagating to the caller's deterministic fallback.
_MAX_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BACKOFF_BASE_SECONDS = 1.0
_RATE_LIMIT_BACKOFF_CAP_SECONDS = 30.0


def _retry_after_header_seconds(exc: Any) -> Optional[float]:
    """Parse the Retry-After header (a count of seconds) off a RateLimitError,
    or None when it is absent, non-numeric (e.g. an HTTP-date form we don't
    honor), or negative."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _rate_limit_backoff_seconds(exc: Any, retry_index: int) -> float:
    """Seconds to sleep before the next 429 retry: honor Retry-After when the
    server sent it, else exponential backoff on the retry index; both capped."""
    retry_after = _retry_after_header_seconds(exc)
    if retry_after is not None:
        return min(retry_after, _RATE_LIMIT_BACKOFF_CAP_SECONDS)
    backoff = _RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** retry_index)
    return min(backoff, _RATE_LIMIT_BACKOFF_CAP_SECONDS)


@dataclass
class Usage:
    """Token usage for the per-user budget gate (#472). 0 when none was returned."""
    input_tokens: int = 0
    output_tokens: int = 0


def _usage_from_response(response: Any) -> Usage:
    """Extract token usage from an Anthropic response, defaulting to 0/0."""
    usage = getattr(response, "usage", None)
    return Usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


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


@dataclass
class ChatTurnDelta:
    """One item from a streamed, tool-aware chat turn (#648): a `text` delta while
    the turn generates (so the caller can pace keepalive heartbeats during
    buffering), or the terminal `final` MessageResult once the turn ends, carrying
    the content blocks (text + any tool_use blocks) and the stop_reason."""
    text: Optional[str] = None
    final: Optional[MessageResult] = None


class AnthropicClient:
    """Anthropic Claude client for JSON generation."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate_json(
        self, system: str, user: str, max_tokens: int = 1024
    ) -> str:
        text, _usage = await self.generate_json_with_usage(
            system=system, user=user, max_tokens=max_tokens
        )
        return text

    async def generate_json_with_usage(
        self, *, system: str, user: str, max_tokens: int = 1024
    ) -> tuple[str, Usage]:
        """`generate_json` that also returns token usage for the budget gate (#472)."""
        import anthropic

        transient_retries = 0
        rate_limit_retries = 0
        while True:
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    timeout=_TIMEOUT_SECONDS,
                )
                return response.content[0].text, _usage_from_response(response)
            except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                if transient_retries < _MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    logger.warning(
                        "anthropic_transient_failure",
                        extra={"attempt": transient_retries, "kind": type(exc).__name__},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
            except anthropic.RateLimitError as exc:
                # 429: capacity, not a caller bug — retry honoring Retry-After (#603).
                if rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                    delay = _rate_limit_backoff_seconds(exc, rate_limit_retries)
                    rate_limit_retries += 1
                    logger.warning(
                        "anthropic_rate_limit_retry",
                        extra={"attempt": rate_limit_retries, "sleep": delay},
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except anthropic.APIStatusError as exc:
                # Retry only on 5xx; other 4xx (bad request, auth, etc.) is a
                # caller bug and won't get better on retry.
                if exc.status_code >= 500 and transient_retries < _MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    logger.warning(
                        "anthropic_5xx_retry",
                        extra={"attempt": transient_retries, "status": exc.status_code},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        tool: Dict[str, Any],
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """A structured-output-only call: force the model to emit exactly the given
        tool's input and return it as a dict. See `generate_structured_with_usage`."""
        result, _usage = await self.generate_structured_with_usage(
            system=system, user=user, tool=tool, max_tokens=max_tokens
        )
        return result

    async def generate_structured_with_usage(
        self,
        *,
        system: str,
        user: str,
        tool: Dict[str, Any],
        max_tokens: int = 1024,
    ) -> tuple[Dict[str, Any], Usage]:
        """A structured-output-only call that also returns token usage (#472).

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
        transient_retries = 0
        rate_limit_retries = 0
        while True:
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
                usage = _usage_from_response(response)
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
                        result = dict(tool_input) if isinstance(tool_input, dict) else {}
                        return result, usage
                raise ValueError(f"no {tool_name} tool_use block in response")
            except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
                if transient_retries < _MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    logger.warning(
                        "anthropic_structured_transient_failure",
                        extra={"attempt": transient_retries, "kind": type(exc).__name__},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
            except anthropic.RateLimitError as exc:
                if rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                    delay = _rate_limit_backoff_seconds(exc, rate_limit_retries)
                    rate_limit_retries += 1
                    logger.warning(
                        "anthropic_structured_rate_limit_retry",
                        extra={"attempt": rate_limit_retries, "sleep": delay},
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and transient_retries < _MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    logger.warning(
                        "anthropic_structured_5xx_retry",
                        extra={"attempt": transient_retries, "status": exc.status_code},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise

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

        transient_retries = 0
        rate_limit_retries = 0
        while True:
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
                if transient_retries < _MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    logger.warning(
                        "anthropic_message_transient_failure",
                        extra={"attempt": transient_retries, "kind": type(exc).__name__},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
            except anthropic.RateLimitError as exc:
                # 429 under concurrent-generation load: retry honoring
                # Retry-After before the caller's fallback fires (#603).
                if rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                    delay = _rate_limit_backoff_seconds(exc, rate_limit_retries)
                    rate_limit_retries += 1
                    logger.warning(
                        "anthropic_message_rate_limit_retry",
                        extra={"attempt": rate_limit_retries, "sleep": delay},
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and transient_retries < _MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    logger.warning(
                        "anthropic_message_5xx_retry",
                        extra={"attempt": transient_retries, "status": exc.status_code},
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise

    async def stream_chat_turn(
        self,
        *,
        system: str,
        messages: List[dict],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator["ChatTurnDelta"]:
        """Stream one turn of a tool-aware chat conversation (#648).

        Yields a ChatTurnDelta(text=...) for each text delta so the caller can pace
        keepalive heartbeats while buffering, then a terminal ChatTurnDelta(final=...)
        once the turn ends, carrying the content blocks (text + any tool_use blocks)
        and the stop_reason for the tool loop to dispatch on.

        When `tools` is falsy the call carries no tool surface, forcing a text answer
        (the loop's tools-off final round).

        429 resilience (#625): a rate limit is admission-time capacity, raised when
        the request is issued — BEFORE the first token — so a bounded retry honoring
        Retry-After (the same `_MAX_RATE_LIMIT_RETRIES` budget and backoff as the
        report path, #603) re-issues the request with no duplicate output. If any
        token has already streamed, we do NOT re-run (that would duplicate the
        buffered reply); the 429 then propagates to the caller's safe-degrade path,
        exactly as any other transport error does.
        """
        import anthropic

        stream_kwargs: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.3,
            system=system,
            messages=messages,
            timeout=_MESSAGE_TIMEOUT_SECONDS,
        )
        if tools:
            stream_kwargs["tools"] = tools
            stream_kwargs["tool_choice"] = {"type": "auto"}

        rate_limit_retries = 0
        while True:
            started = False
            try:
                async with self.client.messages.stream(**stream_kwargs) as stream:
                    async for text in stream.text_stream:
                        started = True
                        yield ChatTurnDelta(text=text)
                    final = await stream.get_final_message()
                break
            except anthropic.RateLimitError as exc:
                # Only retriable before the first token; once tokens have streamed a
                # re-run would duplicate the buffered reply, so propagate instead.
                if not started and rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                    delay = _rate_limit_backoff_seconds(exc, rate_limit_retries)
                    rate_limit_retries += 1
                    logger.warning(
                        "anthropic_chat_rate_limit_retry",
                        extra={"attempt": rate_limit_retries, "sleep": delay},
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

        usage = getattr(final, "usage", None)
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=list(final.content),
                stop_reason=final.stop_reason,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            )
        )
