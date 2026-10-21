"""#999: only a call that never got through is "the coach could not be reached".

Measured with a client double carrying a pre-#995 signature: its TypeError was
caught by the amend loop's catch-all, retried once with the same bug, and
reported to the runner as an unreachable coach, having made zero model calls.
#995 narrowed TypeError and AttributeError at two sites; any other bug still
took the same path. The loops now retry and report as unreachable only what
`llm.is_unreachable` names, the retry ladder's own transport set.
"""

import asyncio
from unittest.mock import patch

import anthropic
import httpx2
import pytest

from app.services.coach.llm import is_unreachable
from app.services.schedule import amend, draft, store
from tests.test_schedule_amend_decides_first_987 import _plan, _user
from tests.test_schedule_draft import _FakeClient, _inject, _seed_user, TODAY

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(code: int) -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        "status", response=httpx2.Response(code, request=_REQUEST), body=None
    )


@pytest.mark.parametrize(
    "exc, unreachable",
    [
        (anthropic.APIConnectionError(request=_REQUEST), True),
        (anthropic.APITimeoutError(request=_REQUEST), True),
        (TimeoutError("caller's budget ran out"), True),
        (_status(529), True),
        (_status(400), False),
        (KeyError("weeks"), False),
        (TypeError("unexpected keyword argument 'timeout'"), False),
        (RuntimeError("anything else"), False),
    ],
)
def test_what_counts_as_unreachable(exc, unreachable):
    assert is_unreachable(exc) is unreachable


class _AmendClient:
    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    async def generate_structured(self, *, system, user, tool, max_tokens=1024, timeout=None):
        self.calls.append(user)
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _amend(db, client):
    user = _user(db)
    plan = _plan(db, user)
    with patch.object(amend.turn, "build_client", return_value=client), \
         patch.object(amend.turn, "over_budget", return_value=False):
        return asyncio.run(
            amend.propose_amendment(
                db, user, plan, weeks_from=1, weeks_through=1,
                instruction="swap Tuesday's run for a ride",
            )
        )


def test_a_bug_in_the_amend_path_is_raised_not_called_unreachable(db):
    client = _AmendClient(KeyError("weeks"), KeyError("weeks"))

    with pytest.raises(KeyError):
        _amend(db, client)
    assert len(client.calls) == 1, "a bug is not retried"


def test_an_amend_that_never_got_through_is_still_unreachable(db):
    blip = anthropic.APIConnectionError(request=_REQUEST)

    proposal = _amend(db, _AmendClient(blip, blip))

    assert proposal.ok is False
    assert proposal.failure_kind == store.FAILURE_UNREACHABLE


def test_an_amend_answer_that_was_cut_off_is_a_rewrite_not_unreachable(db):
    """The structured call raises ValueError when the answer was truncated or
    called no tool: the call ran, its output failed, and the coach is told why."""
    client = _AmendClient(
        ValueError("record_amendment tool call truncated at max_tokens"),
        {"weeks": "not a list"},
    )

    proposal = _amend(db, client)

    assert proposal.failure_kind != store.FAILURE_UNREACHABLE
    assert len(client.calls) == 2
    assert "cut off" in client.calls[1]


@pytest.mark.asyncio
async def test_a_bug_in_the_draft_path_is_raised_not_called_unreachable(db, monkeypatch):
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([KeyError("weeks"), KeyError("weeks")])
    _inject(monkeypatch, client)

    with pytest.raises(KeyError):
        await draft.draft_plan(db, user, plan, today=TODAY)
    assert len(client.calls) == 1


def test_a_cut_off_amendment_tells_the_runner_what_happened_not_the_model_what_to_do(db):
    """The last failure is shown to the runner and handed to the chat coach, so
    it may not be the rewrite's instruction to the model."""
    cut = ValueError("record_amendment tool call truncated at max_tokens")

    proposal = _amend(db, _AmendClient(cut, cut))

    said = " ".join(proposal.failures)
    assert proposal.ok is False
    assert "cut off" in said
    assert "record_" not in said and "Answer only" not in said


def test_an_amend_job_that_raises_still_tells_the_runner(db, monkeypatch):
    """Raising instead of reporting "unreachable" must not bring back #984's
    silence: the job's own handler says it failed and ends the watch."""
    from app.jobs import amend_schedule
    from app.models.coach_chat_message import CoachChatMessage
    from app.services.schedule import amend_watch
    from tests.test_amendment_watch_1003 import _NoCloseSession, _plan as _active_plan, _thread
    from tests.test_amendment_watch_1003 import _FakeRedis as _WatchRedis

    monkeypatch.setattr(amend_watch, "redis_conn", _WatchRedis())
    user = _user(db)
    plan = _active_plan(db, user)
    thread = _thread(db, user)

    async def _bug(*_a, **_k):
        raise _status(400)

    class _KeepSeed(_NoCloseSession):
        """The handler rolls back, which in this harness would also drop the
        seeded rows; nothing was written, so there is nothing to undo."""

        def rollback(self):
            pass

    with patch.object(amend_schedule, "SessionLocal", return_value=_KeepSeed(db)), \
         patch.object(amend_schedule, "amend_plan", _bug):
        amend_schedule.amend_schedule_job(
            str(user.id), str(plan.id), 1, 1, "reason", str(thread.id), "Rewrite the week."
        )

    notes = db.query(CoachChatMessage).filter(CoachChatMessage.thread_id == thread.id).all()
    assert [n.role for n in notes] == ["assistant"]
    assert "could not write" in notes[0].content
    assert amend_watch.current(user.id)["status"] == "failed"
