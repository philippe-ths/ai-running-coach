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

# A structured call's ceiling FOLLOWS THE WORK it asked for. The flat 60s above
# is sized for a short record, and the schedule draft is what broke it (#981):
# asked for five concrete weeks instead of three it generates roughly thirty
# sessions, and every attempt died on the cap with `APITimeoutError` and no plan
# at all.
#
# Derived rather than passed, deliberately. A per-call argument puts the ceiling
# in the hands of whoever remembers to set it, and the caller that forgets is
# exactly the one generating too much to fit. `max_tokens` is already the
# caller's own statement of how much output it expects, so the ceiling reads it
# instead of asking twice. The rate is deliberately generous: this is a hung-call
# guard, not a latency budget.
_SECONDS_PER_1K_OUTPUT_TOKENS = 25.0


def structured_timeout_for(max_tokens: int) -> float:
    """Wall-clock ceiling for a structured call asking for `max_tokens`."""
    return max(_TIMEOUT_SECONDS, (max_tokens / 1000.0) * _SECONDS_PER_1K_OUTPUT_TOKENS)

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


class RetryLadder:
    """THE transport retry policy for every Anthropic call this app makes (#801).

    One ladder, three rungs, in the order a caller meets them:

    - transient transport (timeout / connection drop / mid-stream protocol
      error): `_MAX_TRANSIENT_RETRIES` attempts at a flat backoff.
    - 429 rate limit: `_MAX_RATE_LIMIT_RETRIES` attempts honoring Retry-After
      (#603) — capacity frees up, so a 429 is retriable where a 4xx is not.
    - 5xx: shares the transient budget; any other 4xx is a caller bug and is
      raised immediately.

    Written once because it was written four times, and they had DRIFTED — the
    issue's "byte-identical apart from the log event" is not what the code said:

    - the streaming method carried the 429 rung ONLY, so a connection drop or a
      5xx on a chat turn degraded on the first failure while the same failure on
      a report was retried;
    - `httpx.RemoteProtocolError` was caught by `generate_coach_message` alone.
      Sharing the ladder means the two non-streaming create-based methods now
      catch it too. That can only widen: the SDK wraps httpx errors from the
      initial HTTP call into APIConnectionError, so a non-streaming call has no
      way to raise it unwrapped in the first place.

    `event_prefix` keeps each call site's log events.

    `retriable` is the streaming guard: once a token has left the server the
    request must NOT be re-issued, because the caller has already buffered
    output and a re-run would duplicate it. A single-shot caller passes True.
    """

    def __init__(self, event_prefix: str) -> None:
        self._prefix = event_prefix
        self._transient_retries = 0
        self._rate_limit_retries = 0

    async def should_retry(self, exc: BaseException, *, retriable: bool = True) -> bool:
        """True when the caller should re-issue the request (after sleeping out
        the backoff here). False means the caller re-raises."""
        import anthropic

        if not retriable:
            return self._give_up(exc, "mid_stream_cannot_reissue")
        if isinstance(
            exc,
            (
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
                # httpx.RemoteProtocolError is raised mid-stream when the peer
                # closes the connection before the chunked response body is
                # complete ("incomplete chunked read"). The SDK wraps httpx errors
                # from the *initial* HTTP call into APIConnectionError, but errors
                # surfacing during SSE iteration escape unwrapped. Same transient
                # transport class, same rung (#302).
                httpx.RemoteProtocolError,
            ),
        ):
            return await self._transient(exc)
        if isinstance(exc, anthropic.RateLimitError):
            if self._rate_limit_retries >= _MAX_RATE_LIMIT_RETRIES:
                return self._give_up(exc, "rate_limit_budget_exhausted")
            delay = _rate_limit_backoff_seconds(exc, self._rate_limit_retries)
            self._rate_limit_retries += 1
            logger.warning(
                f"{self._prefix}_rate_limit_retry",
                extra={"attempt": self._rate_limit_retries, "sleep": delay},
            )
            await asyncio.sleep(delay)
            return True
        if isinstance(exc, anthropic.APIStatusError):
            # Retry only on 5xx; other 4xx (bad request, auth, etc.) is a caller
            # bug and won't get better on retry.
            if exc.status_code >= 500:
                return await self._transient(exc, event="5xx_retry")
            return self._give_up(exc, "non_retriable_status")
        return self._give_up(exc, "not_a_retriable_error")

    def _give_up(self, exc: BaseException, reason: str) -> bool:
        """Record WHY the ladder stopped retrying, then tell the caller to re-raise.

        #966: every one of these exits used to return False silently, so an error
        the ladder declined to retry reached the caller's fail-soft handler with
        nothing upstream explaining the decision. The 4xx exit is the one the
        issue names, but the decision is shared, so the record is made in one
        place rather than five. Not `logger.exception`: the caller re-raises and
        logs the traceback, and this line is about the RETRY decision, not the
        error. `False` is returned so each call site stays a single expression.
        """
        detail = {
            "reason": reason,
            "kind": type(exc).__name__,
            "status": getattr(exc, "status_code", None),
            "transient_retries": self._transient_retries,
            "rate_limit_retries": self._rate_limit_retries,
        }
        logger.warning(f"{self._prefix}_not_retried", extra=detail)
        return False

    async def _transient(self, exc: BaseException, event: str = "transient_failure") -> bool:
        if self._transient_retries >= _MAX_TRANSIENT_RETRIES:
            return self._give_up(exc, "transient_budget_exhausted")
        self._transient_retries += 1
        detail = (
            {"attempt": self._transient_retries, "status": getattr(exc, "status_code", None)}
            if event == "5xx_retry"
            else {"attempt": self._transient_retries, "kind": type(exc).__name__}
        )
        logger.warning(f"{self._prefix}_{event}", extra=detail)
        await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
        return True


@dataclass
class Usage:
    """Token usage for the per-user budget gate (#472). 0 when none was returned.

    `cache_read_input_tokens` / `cache_creation_input_tokens` (#629) are captured
    for verification of prompt-cache hits; they are NOT billed on the budget gate
    (which bills the uncached `input_tokens` only — a conservative under-estimate,
    since caching strictly lowers real cost)."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def _usage_from_response(response: Any) -> Usage:
    """Extract token usage from an Anthropic response, defaulting to 0/0."""
    usage = getattr(response, "usage", None)
    return Usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def _cacheable_system(system: str) -> List[Dict[str, Any]]:
    """Wrap the system prompt as a single cacheable content block (#629).

    The Anthropic prompt cache is a prefix match, and our coach system prompt is
    byte-identical across every activity for a given prompt id + voice, so caching
    it takes the ~7k-10k input tok/report system prefix from full price to ~0.1x on
    a cache read. `tools` render before `system` in cache order and our tool schema
    is deterministic too, so this one breakpoint caches tools + system together.

    Behaviour-preserving: same prompt, same params, same output. A no-op when the
    prefix is under the model's minimum cacheable length (the API silently ignores
    the breakpoint), so it never harms the shorter aux-path prompts that also route
    through the create-based methods."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


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
    # Prompt-cache verification (#629); not billed on the budget gate.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


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
        ladder = RetryLadder("anthropic")
        while True:
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    system=_cacheable_system(system),  # #629 prompt caching
                    messages=[{"role": "user", "content": user}],
                    timeout=_TIMEOUT_SECONDS,
                )
                return response.content[0].text, _usage_from_response(response)
            except Exception as exc:  # noqa: BLE001 — the ladder decides; it re-raises
                if await ladder.should_retry(exc):
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

        A `max_tokens` stop ALSO raises (#931). The SDK cannot assemble a partial
        tool call, so a truncated response arrives as a tool_use block whose input
        is `{}` — which, to the caller, is indistinguishable from a model that
        legitimately answered with nothing. That is only SILENT for a schema whose
        fields all carry defaults, which is exactly how the runner-memory writer
        stored an empty profile over a large history and stamped it as a successful
        pass. Raising makes truncation visible to every structured caller.

        Not retried, for the same reason a missing block is not: the same call at
        the same cap truncates again. The right fix is a cap that fits the work,
        and this raise is what makes an undersized one findable instead of silent.
        """
        tool_name = tool["name"]
        ladder = RetryLadder("anthropic_structured")
        while True:
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=_cacheable_system(system),  # #629 prompt caching
                    messages=[{"role": "user", "content": user}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool_name},
                    timeout=structured_timeout_for(max_tokens),
                )
                usage = _usage_from_response(response)
                stop_reason = (
                    response.get("stop_reason") if isinstance(response, dict)
                    else getattr(response, "stop_reason", None)
                )
                if stop_reason == "max_tokens":
                    raise ValueError(
                        f"{tool_name} tool call truncated at max_tokens={max_tokens}; "
                        "the tool input is incomplete and must not be read as an answer"
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
                        result = dict(tool_input) if isinstance(tool_input, dict) else {}
                        return result, usage
                raise ValueError(f"no {tool_name} tool_use block in response")
            except ValueError:
                # A missing tool_use block is a logic error, not transport: it is
                # not retried here (the caller treats it as a failed distillation).
                raise
            except Exception as exc:  # noqa: BLE001 — the ladder decides; it re-raises
                if await ladder.should_retry(exc):
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
        ladder = RetryLadder("anthropic_message")
        while True:
            try:
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=_cacheable_system(system),  # #629 prompt caching
                    messages=[{"role": "user", "content": user}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": _COACH_EFFORT},
                    tool_choice={"type": "auto"},
                    tools=tools,
                    timeout=_MESSAGE_TIMEOUT_SECONDS,
                ) as stream:
                    final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
                # Verify cache behaviour in prod (#629): ~0 read on the first report
                # in a TTL window (a write instead), then the full system-prompt
                # count on subsequent reports. A read that stays 0 across reports
                # signals a silent prefix invalidator.
                logger.info(
                    "coach_prompt_cache",
                    extra={"cache_read": cache_read, "cache_creation": cache_creation},
                )
                return MessageResult(
                    content_blocks=list(final.content),
                    stop_reason=final.stop_reason,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    cache_read_input_tokens=cache_read,
                    cache_creation_input_tokens=cache_creation,
                )
            except Exception as exc:  # noqa: BLE001 — the ladder decides; it re-raises
                if await ladder.should_retry(exc):
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

        Transport resilience: the SHARED `RetryLadder` (#801), so a chat turn now
        survives the same transient drops and 5xx a report does — before this it
        carried only the 429 rung, and a connection drop degraded on first failure.
        Every rung is gated on `started`: a rate limit is admission-time capacity
        raised BEFORE the first token, so re-issuing produces no duplicate output,
        but once ANY token has streamed a re-run would duplicate the buffered reply,
        so the error propagates to the caller's safe-degrade path instead (#625).
        """
        stream_kwargs: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.3,
            # #766: the chat/thread system prefix is byte-identical across the
            # tool rounds of one turn (and often across turns), so the #629
            # cache breakpoint applies here too — tools render before system,
            # so one breakpoint caches both.
            system=_cacheable_system(system),
            messages=messages,
            timeout=_MESSAGE_TIMEOUT_SECONDS,
        )
        if tools:
            stream_kwargs["tools"] = tools
            stream_kwargs["tool_choice"] = {"type": "auto"}

        ladder = RetryLadder("anthropic_chat")
        while True:
            started = False
            try:
                async with self.client.messages.stream(**stream_kwargs) as stream:
                    async for text in stream.text_stream:
                        started = True
                        yield ChatTurnDelta(text=text)
                    final = await stream.get_final_message()
                break
            except Exception as exc:  # noqa: BLE001 — the ladder decides; it re-raises
                if await ladder.should_retry(exc, retriable=not started):
                    continue
                raise

        usage = getattr(final, "usage", None)
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=list(final.content),
                stop_reason=final.stop_reason,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                # #786: the chat prefix is cached too (the breakpoint above), so
                # the cache buckets must ride the result or the budget counter
                # under-reports every conversational turn.
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_input_tokens=getattr(
                    usage, "cache_creation_input_tokens", 0
                ) or 0,
            )
        )
