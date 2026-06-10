"""
LLM client abstraction — keeps the coach service decoupled from any specific provider.
"""

import asyncio
import logging
from typing import AsyncIterator, List, Protocol

logger = logging.getLogger(__name__)

# Cap any single Anthropic call. The SDK default is 600s; the RQ worker
# would block on a single hung call for that long otherwise.
_TIMEOUT_SECONDS = 60.0

# Initial backoff before the single retry on transient failures.
_RETRY_BACKOFF_SECONDS = 1.0


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
