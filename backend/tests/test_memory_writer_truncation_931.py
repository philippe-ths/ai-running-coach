"""#931 — a truncated writer call must never be stored as an empty profile.

The production account with the longest history held a memory profile of five
empty sections against 206 consumed sources, stamped with fresh provenance as a
successful pass. Reproduced against that real history: the writer hit
`max_tokens=2000` mid-tool-call, the SDK returned a `tool_use` block whose input
was `{}`, `candidates` defaulted to `[]`, and an empty profile was written over
the runner's whole stated history.

Three defects, pinned here:

1. A `max_tokens` stop is now a raised error rather than a partial dict, so the
   silence is impossible for EVERY structured caller, not just this one. The
   memory writer was the only silent victim because it is the only structured
   schema whose fields all carry defaults.
2. The pass writes nothing when the call fails, so a stored profile is never
   worse than the one it replaced.
3. Coercion is per-candidate. The same real history produced 43 candidates of
   which 2 overran MAX_LINE_LENGTH; whole-object validation discarded all 43.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.coach_memory import MAX_LINE_LENGTH
from app.services.coach.llm import AnthropicClient, Usage
from app.services.coach.memory_store import get_memory
from app.services.coach.memory_update import (
    _MAX_WRITER_TOKENS,
    coerce_candidates,
    update_memory,
)

from tests.test_memory_update import _activity, _chat, _user  # reuse the fixtures


_TOOL = {"name": "record_runner_memory", "input_schema": {"type": "object"}}


def _response(stop_reason, tool_input):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        content=[SimpleNamespace(type="tool_use", name="record_runner_memory", input=tool_input)],
    )


# --------------------------------------------------------------------------- #
# 1. The shared seam: truncation raises instead of returning a partial answer.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_truncated_tool_call_raises_instead_of_returning_empty():
    """The exact production shape: stop_reason=max_tokens with an empty input."""
    client = AnthropicClient(api_key="k", model="m")
    client.client.messages.create = AsyncMock(return_value=_response("max_tokens", {}))

    with pytest.raises(ValueError, match="truncated"):
        await client.generate_structured_with_usage(
            system="s", user="u", tool=_TOOL, max_tokens=2000
        )


@pytest.mark.asyncio
async def test_truncation_is_not_retried():
    """The same call at the same cap truncates again; retrying only burns spend."""
    client = AnthropicClient(api_key="k", model="m")
    create = AsyncMock(return_value=_response("max_tokens", {}))
    client.client.messages.create = create

    with pytest.raises(ValueError):
        await client.generate_structured_with_usage(
            system="s", user="u", tool=_TOOL, max_tokens=2000
        )
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_a_complete_call_still_returns_its_input():
    """The guard must not fire on a normal completion (positive control)."""
    client = AnthropicClient(api_key="k", model="m")
    payload = {"candidates": [{"text": "t", "section": "lately", "supporting_source_ids": ["chat0"]}]}
    client.client.messages.create = AsyncMock(return_value=_response("tool_use", payload))

    result, usage = await client.generate_structured_with_usage(
        system="s", user="u", tool=_TOOL, max_tokens=2000
    )
    assert result == payload
    assert usage.output_tokens == 20


# --------------------------------------------------------------------------- #
# 2. The pass writes nothing when the writer call fails.
# --------------------------------------------------------------------------- #
class _TruncatingClient:
    """Stands in for the truncated call, which now raises out of the client."""

    model = "claude-haiku-4-5"

    async def generate_structured_with_usage(self, *, system, user, tool, max_tokens):
        raise ValueError("record_runner_memory tool call truncated at max_tokens=2000")


def test_a_truncated_pass_stores_no_profile(db):
    """The regression: the bug was an EMPTY profile written and stamped as success."""
    uid = _user(db)
    activity = _activity(db, uid)
    _chat(db, activity, "user", "My race is on September 27th.")
    db.flush()

    result = asyncio.run(update_memory(db, uid, client=_TruncatingClient()))

    assert result is None, "a failed pass must not return a stored row"
    assert get_memory(db, uid) is None, "a failed pass must not write a profile"


def test_the_production_shape_end_to_end_writes_no_profile(db, monkeypatch):
    """The whole path, exactly as production hit it: a real AnthropicClient whose
    underlying call truncates. Before the fix this stored five empty sections and
    stamped fresh provenance; the failure was invisible at every layer."""
    uid = _user(db)
    activity = _activity(db, uid)
    _chat(db, activity, "user", "My race is on September 27th.")
    db.flush()

    client = AnthropicClient(api_key="k", model="claude-haiku-4-5")
    # stop_reason=max_tokens with an empty tool input — what the SDK returns when
    # it cannot assemble a partial tool call.
    client.client.messages.create = AsyncMock(return_value=_response("max_tokens", {}))

    result = asyncio.run(update_memory(db, uid, client=client))

    assert result is None
    assert get_memory(db, uid) is None


# --------------------------------------------------------------------------- #
# 3. Per-candidate coercion: one bad line does not cost the pass.
# --------------------------------------------------------------------------- #
def test_an_over_long_line_drops_and_the_rest_survive():
    """Measured on the real history: 2 of 43 candidates overran MAX_LINE_LENGTH."""
    good = {"text": "Races a half marathon on 27 Sep", "section": "goals_and_plans",
            "supporting_source_ids": ["chat0", "chat1"]}
    over_long = {"text": "x" * (MAX_LINE_LENGTH + 1), "section": "lately",
                 "supporting_source_ids": ["chat2"]}
    other_good = {"text": "Uses a metronome at 170 spm", "section": "what_works_for_you",
                  "supporting_source_ids": ["chat3", "chat4"]}

    kept = coerce_candidates({"candidates": [good, over_long, other_good]})

    assert [c.text for c in kept] == [good["text"], other_good["text"]]


def test_a_missing_candidates_key_is_an_empty_list_not_a_crash():
    assert coerce_candidates({}) == []


def test_an_off_contract_payload_raises_so_the_caller_fails_the_pass():
    """A non-list `candidates` is off-contract output, not one bad line."""
    with pytest.raises(ValueError):
        coerce_candidates({"candidates": "not a list"})
    with pytest.raises(ValueError):
        coerce_candidates(["not", "an", "object"])


# --------------------------------------------------------------------------- #
# 4. The cap is sized for the work the gather bounds allow.
# --------------------------------------------------------------------------- #
def test_the_writer_cap_covers_the_measured_need():
    """A real 206-source history cost ~3.5-4.7k output tokens. The gather caps
    admit ~325 sources, so the cap must clear that scaled need with headroom.
    This is a floor, not a target: raising the cap is safe, lowering it below
    the measurement is the bug."""
    assert _MAX_WRITER_TOKENS >= 7500
