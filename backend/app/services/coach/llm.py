"""
LLM client abstraction — keeps the coach service decoupled from any specific provider.
"""

from typing import Protocol


class LLMClient(Protocol):
    """Protocol for LLM clients that return raw JSON strings."""

    async def generate_json(self, system: str, user: str, max_tokens: int) -> str: ...


class AnthropicClient:
    """Anthropic Claude client for JSON generation."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate_json(
        self, system: str, user: str, max_tokens: int = 1024
    ) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.2,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
