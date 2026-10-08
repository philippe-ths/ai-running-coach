"""Which request shapes a Claude model accepts: the ONE place that decides.

The Claude 5.5 generation rejects two things the 4.x lanes rely on, and an
unconditional call site is a 400 on every request after a config flip:

  * a forced `tool_choice` (`{"type": "tool"|"any"}`): Opus 5.5, Sonnet 5.5,
    Fable 5.1 and Mythos 5.1 return 400 for it (an `auto` choice is fine);
  * any non-default `temperature` / `top_p` / `top_k`: 400 on Opus 4.7 and every
    later model (so every 5.x model).

Sources (read 2026-10-07): the "Forcing tool use" section of
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools ;
https://platform.claude.com/docs/en/models/opus-5-5/migration-guide ;
https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5 ;
https://platform.claude.com/docs/en/models/haiku-5-5/overview .

Both answers are ALLOWLISTS over (family, version). An id this module cannot parse
or has never heard of gets the shape that is valid everywhere (no sampling params,
`auto` tool choice), so a new model is a degraded-but-working lane rather than a 400,
and a rollback to 4.x keeps exactly the parameters it ran with before.
"""

import re
from typing import Any, Dict, Optional, Tuple

_ID = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2})(?!\d))?")

Version = Tuple[int, int]


def parse_model(model: str) -> Optional[Tuple[str, Version]]:
    """`(family, (major, minor))` for a Claude id, or None. `claude-sonnet-4-6` is
    (sonnet, (4, 6)); `claude-haiku-4-5-20251001` is (haiku, (4, 5)); a dated
    `claude-sonnet-4-20250514` is (sonnet, (4, 0))."""
    m = _ID.match(model) if isinstance(model, str) else None
    if not m:
        return None
    return m.group(1), (int(m.group(2)), int(m.group(3) or 0))


def accepts_sampling(model: str) -> bool:
    """True when `temperature` may be sent. Haiku/Sonnet 4.x and Opus before 4.7 do;
    everything newer, and anything unrecognised, does not."""
    parsed = parse_model(model)
    if parsed is None:
        return False
    family, version = parsed
    if family in ("sonnet", "haiku"):
        return version < (5, 0)
    if family == "opus":
        return version < (4, 7)
    return False


def accepts_forced_tool_choice(model: str) -> bool:
    """True when `tool_choice` may force a tool. Opus/Sonnet below 5.5 do, Haiku 5.5
    does; Opus 5.5, Sonnet 5.5, Fable 5.1 and anything unrecognised do not."""
    parsed = parse_model(model)
    if parsed is None:
        return False
    family, version = parsed
    if family in ("opus", "sonnet"):
        return version < (5, 5)
    return family == "haiku"


def sampling_kwargs(model: str, temperature: float) -> Dict[str, Any]:
    """The request-body field that keeps a call site's temperature, or nothing.

    `anthropic` 1.x removed `temperature` from `create()`/`stream()`, so it travels
    in `extra_body` (#966, #1023). Omitted for a model that rejects it.
    """
    if not accepts_sampling(model):
        return {}
    return {"extra_body": {"temperature": temperature}}


def tool_choice_kwargs(model: str, tool_name: str) -> Dict[str, Any]:
    """`tool_choice` for a call that must answer with `tool_name`'s input: forced where
    the model accepts that, else `auto` (the caller must then say so in the prompt)."""
    if accepts_forced_tool_choice(model):
        return {"tool_choice": {"type": "tool", "name": tool_name}}
    return {"tool_choice": {"type": "auto"}}


def forced_tool_instruction(tool_name: str) -> str:
    """What replaces a forced tool choice in the user turn on a model that rejects it."""
    return (
        f"\n\nRespond by calling the `{tool_name}` tool exactly once. "
        "Do not answer in plain text."
    )


def supports_thinking(model: str) -> bool:
    """True when a call may ask for adaptive thinking and an `effort` (every 5.x).

    4.x models think only on an explicit token budget, which this app does not use
    for structured calls, and an unrecognised id gets the shape that is valid
    everywhere, which is no thinking.
    """
    parsed = parse_model(model)
    return bool(parsed and parsed[1] >= (5, 0))


def thinking_headroom(model: str) -> int:
    """Extra `max_tokens` for a model that thinks by default (5.x), because thinking
    tokens count against the cap and an unpadded cap can be spent before any text.
    Only billed if used. Zero for models that think only when asked."""
    parsed = parse_model(model)
    return 4096 if parsed and parsed[1] >= (5, 0) else 0


def chat_thinking_kwargs(model: str) -> Dict[str, Any]:
    """Thinking settings for a streamed chat turn on a model that thinks by default.

    Sonnet 5.5 returns text between tool calls in empty-by-default `thinking`
    blocks; `between_tools` switches up-front thinking off and brings the text back.
    Other 5.x models cannot switch thinking off (Opus 5.5 is always on), so their
    effort is lowered to keep a conversational reply fast. 4.x gets nothing.
    """
    parsed = parse_model(model)
    if parsed is None or parsed[1] < (5, 0):
        return {}
    if parsed[0] == "sonnet" and parsed[1] >= (5, 5):
        return {"thinking": {"type": "between_tools"}}
    return {"output_config": {"effort": "low"}}
