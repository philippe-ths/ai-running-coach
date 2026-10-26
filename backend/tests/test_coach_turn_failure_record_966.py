"""#966: a failed coach turn must leave a record saying what failed and where.

The runner-visible failure was the LEAST observable path in the chat surface, and
it was the one that kept firing. Three gaps, all reproduced below:

  1. the exception handler logged the message alone, with no type and no
     traceback, so a bug in our own code recorded a bare string with nothing
     locating it (the live example: "AsyncMessages.stream() got an unexpected
     keyword argument 'temperature'");
  2. the stream-ended-with-no-final-message branch wrote NO log line at all while
     producing the identical runner-facing message, so the two were
     indistinguishable from the outside;
  3. the retry ladder re-raised without recording that it had declined to retry,
     so nothing upstream filled the gap either.

The fourth requirement is a negative: nothing added here reaches the runner. The
two runner-facing strings are pinned byte for byte.
"""

import logging

import pytest

from app.services.coach.chat import _buffered_tool_loop
from tests._chat_stubs import chat_no_final_stub, chat_raising_stub

# The exact strings the runner sees. Pinned here so a change to the log lines
# cannot quietly leak diagnostics into the reply (#966 AC3).
GENERIC = "Sorry, I encountered an error. Please try again."
RATE_LIMITED_LEAD = "I'm getting a lot of requests right now"


async def _run_loop(db, monkeypatch, stub) -> dict:
    """Drive the shared generation core directly with a stubbed client.

    The core is what both chat surfaces share, so testing it here covers the
    activity chat box and the thread turn at once.
    """
    from app.services.coach.llm import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "stream_chat_turn", stub, raising=True)
    client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
    out: dict = {}
    async for _event in _buffered_tool_loop(
        db,
        client,
        system_prompt="you are a coach",
        llm_messages=[{"role": "user", "content": "talk me through the schedule"}],
        owner_user_id=None,
        out=out,
        tools=None,
    ):
        pass
    return out


@pytest.mark.asyncio
async def test_upstream_error_records_type_and_traceback(db, monkeypatch, caplog):
    """Gap 1. A TypeError from our own arguments must be locatable in the log.

    `chat_raising_stub` raises RuntimeError; the point is not which class it is
    but that the class name and a traceback are recorded. Before #966 the record
    was the message alone, which for the production failure was a bare string
    naming neither the call site nor that it came from our own kwargs.
    """
    with caplog.at_level(logging.ERROR, logger="app.services.coach.chat"):
        out = await _run_loop(db, monkeypatch, chat_raising_stub())

    assert out["stream_failed"] is True
    records = [r for r in caplog.records if "coach_turn_failed" in r.getMessage()]
    assert len(records) == 1, "the failure the runner sees must be recorded exactly once"
    record = records[0]
    assert "RuntimeError" in record.getMessage(), "the exception TYPE must be named"
    assert record.exc_info is not None, "a traceback must be attached"


@pytest.mark.asyncio
async def test_empty_stream_is_recorded_and_distinguishable(db, monkeypatch, caplog):
    """Gap 2. The branch that raised nothing wrote nothing; now it writes its own
    line, and that line is DIFFERENT from the upstream-error one."""
    with caplog.at_level(logging.ERROR, logger="app.services.coach.chat"):
        out = await _run_loop(db, monkeypatch, chat_no_final_stub())

    assert out["stream_failed"] is True
    records = [r for r in caplog.records if "coach_turn_failed" in r.getMessage()]
    assert len(records) == 1, "the silent branch must now record"
    message = records[0].getMessage()
    assert "no final message" in message
    # The distinguishing property the issue asks for: an empty stream must not
    # read like an upstream error. It carries no exception, hence no traceback.
    assert records[0].exc_info is None
    assert "RuntimeError" not in message


@pytest.mark.asyncio
async def test_the_two_failures_do_not_look_alike(db, monkeypatch, caplog):
    """The acceptance criterion stated directly: given only the log, the two
    failure modes must be tellable apart. They produce the same runner-facing
    message, which is why the log is the only place they can differ."""
    with caplog.at_level(logging.ERROR, logger="app.services.coach.chat"):
        upstream = await _run_loop(db, monkeypatch, chat_raising_stub())
        upstream_log = [r for r in caplog.records if "coach_turn_failed" in r.getMessage()][-1]
        caplog.clear()
        empty = await _run_loop(db, monkeypatch, chat_no_final_stub())
        empty_log = [r for r in caplog.records if "coach_turn_failed" in r.getMessage()][-1]

    # Same thing shown to the runner ...
    assert upstream["stream_fail_message"] == empty["stream_fail_message"] == GENERIC
    # ... different thing written down.
    assert upstream_log.getMessage() != empty_log.getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("stub_factory", [chat_raising_stub, chat_no_final_stub])
async def test_runner_message_carries_no_diagnostics(db, monkeypatch, stub_factory):
    """Gap 4 (the negative). Nothing added for observability reaches the runner:
    the served text is byte-identical to the string the issue quotes."""
    out = await _run_loop(db, monkeypatch, stub_factory())

    assert out["stream_fail_message"] == GENERIC
    for leak in ("Traceback", "RuntimeError", "TypeError", "keyword argument", "final message"):
        assert leak not in out["stream_fail_message"]


@pytest.mark.asyncio
async def test_rate_limited_message_still_specialises(db, monkeypatch):
    """The #625 behaviour must survive: an exhausted 429 keeps its own transparent
    message rather than collapsing into the generic one."""
    import anthropic
    import httpx

    from app.services.coach.llm import ChatTurnDelta  # noqa: F401  (stub shape)

    def _rate_limited_stub():
        async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
            raise anthropic.RateLimitError(
                "rate limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.anthropic.com")
                ),
                body=None,
            )
            yield  # pragma: no cover - makes this an async generator

        return _stub

    out = await _run_loop(db, monkeypatch, _rate_limited_stub())

    assert out["stream_failed"] is True
    assert out["stream_fail_message"].startswith(RATE_LIMITED_LEAD)


def test_retry_ladder_records_why_it_gave_up(caplog):
    """Gap 3. Every exit that declines to retry now says so, including the
    non-retriable 4xx the issue names. Before this the caller re-raised into the
    fail-soft handler with nothing upstream explaining the decision."""
    import asyncio

    import anthropic
    import httpx

    from app.services.coach.llm import RetryLadder

    # The class production actually raised on 18 Aug: a 400 invalid_request_error
    # ("Your credit balance is too low"), which the ladder correctly declines to
    # retry and, before this change, re-raised with nothing written down.
    credit_exhausted = anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API.",
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://api.anthropic.com")
        ),
        body=None,
    )

    with caplog.at_level(logging.WARNING, logger="app.services.coach.llm"):
        ladder = RetryLadder("anthropic_chat")
        assert asyncio.run(ladder.should_retry(credit_exhausted)) is False

    records = [r for r in caplog.records if r.getMessage() == "anthropic_chat_not_retried"]
    assert len(records) == 1
    assert records[0].reason == "non_retriable_status"
    assert records[0].status == 400
    assert records[0].kind == "BadRequestError"


def test_retry_ladder_records_the_mid_stream_refusal(caplog):
    """The rung that is NOT about the error at all: once a token has streamed the
    request must not be re-issued, so the ladder declines regardless of class.
    That decision was the most invisible of the five, and is now recorded."""
    import asyncio

    from app.services.coach.llm import RetryLadder

    with caplog.at_level(logging.WARNING, logger="app.services.coach.llm"):
        ladder = RetryLadder("anthropic_chat")
        decision = asyncio.run(
            ladder.should_retry(TypeError("unexpected keyword argument"), retriable=False)
        )

    assert decision is False
    records = [r for r in caplog.records if r.getMessage() == "anthropic_chat_not_retried"]
    assert len(records) == 1
    assert records[0].reason == "mid_stream_cannot_reissue"
    assert records[0].kind == "TypeError"
