"""#995: the turn's wall-clock budget agrees with the ceiling that enforces it.

A thread turn is generated on the backend and delivered through a Vercel
function whose `maxDuration` is a hard kill. Nothing connected the two. The route
file asserted the relationship in a COMMENT — "the underlying LLM call is bounded
well below this" — which was true when written for #223 and false from the moment
#989 put a `max_tokens=8192` generation (a 205-second derived ceiling) inside the
request. It stayed false, and green, for three days, while two of the runner's
requests for next week's sessions were severed mid-stream and stored as user
messages with no reply.

These tests are the check that comment could not be. They read the real files, so
raising `maxDuration` without raising the backend's budget — or the reverse —
fails here rather than in front of a runner.
"""

import re
from pathlib import Path

import pytest

from app.services.coach import turn
from app.services.schedule import amend

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_ROUTE = _FRONTEND / "app" / "api" / "[...path]" / "route.ts"
_BUDGET_TS = _FRONTEND / "lib" / "turnBudget.ts"


def _number(path: Path, pattern: str) -> float:
    assert path.exists(), f"{path} is missing; the budget guard cannot read it"
    match = re.search(pattern, path.read_text())
    assert match, f"no {pattern!r} in {path}"
    return float(match.group(1))


# --- the invariant itself ----------------------------------------------------


def test_route_max_duration_matches_the_backend_turn_budget():
    max_duration = _number(_ROUTE, r"export const maxDuration\s*=\s*(\d+)")
    assert max_duration == turn.TURN_BUDGET_SECONDS, (
        f"route.ts allows a turn {max_duration}s but the backend budgets "
        f"{turn.TURN_BUDGET_SECONDS}s. These are the same ceiling seen from two "
        "sides; change both or the generation outlives the request again (#995)."
    )


def test_frontend_budget_module_matches_the_route():
    assert _number(_BUDGET_TS, r"TURN_MAX_DURATION_SECONDS\s*=\s*(\d+)") == _number(
        _ROUTE, r"export const maxDuration\s*=\s*(\d+)"
    )


def test_the_reserve_leaves_real_room_for_a_generation():
    """A budget entirely consumed by its own reserve would refuse everything."""
    assert turn.TURN_RESERVE_SECONDS < turn.TURN_BUDGET_SECONDS
    assert amend.weeks_that_fit(
        turn.TURN_BUDGET_SECONDS - turn.TURN_RESERVE_SECONDS
    ) >= 1, "the budget must fit at least a one-week amendment, the commonest ask"


# --- the guard is not vacuous ------------------------------------------------


def test_the_guard_would_fail_on_the_drift_it_exists_to_catch(monkeypatch):
    """Prove the assertion bites. A guard only ever seen passing proves nothing."""
    monkeypatch.setattr(turn, "TURN_BUDGET_SECONDS", 205.0)
    with pytest.raises(AssertionError, match="outlives the request"):
        test_route_max_duration_matches_the_backend_turn_budget()


# --- what the budget decides -------------------------------------------------


def test_budget_remaining_shrinks_with_elapsed_time():
    started = 1000.0
    full = turn.turn_budget_remaining(started, now=started)
    assert full == turn.TURN_BUDGET_SECONDS - turn.TURN_RESERVE_SECONDS
    assert turn.turn_budget_remaining(started, now=started + 10) == full - 10


def test_budget_remaining_never_goes_negative():
    assert turn.turn_budget_remaining(1000.0, now=9999.0) == 0.0


@pytest.mark.parametrize(
    "weeks, seconds",
    # The measurements the estimate is fitted to (2026-08-30, real plan), as a
    # floor-and-ceiling rather than an exact match: the point is that the
    # estimate never UNDER-states a window, which is what would let one start.
    [(1, 23.5), (2, 40.6), (4, 60.9), (6, 65.0)],
)
def test_the_estimate_is_not_optimistic_about_a_measured_window(weeks, seconds):
    assert amend.estimated_seconds(weeks) >= seconds * 0.8, (
        f"a {weeks}-week amendment measured {seconds}s; an estimate far below "
        "that would let a window start that cannot finish"
    )


def test_windows_that_do_not_fit_the_measured_budget_are_refused():
    """The line the measurements actually draw: one and two weeks fit, four does not."""
    available = turn.TURN_BUDGET_SECONDS - turn.TURN_RESERVE_SECONDS
    fits = amend.weeks_that_fit(available)
    assert 1 <= fits < 4, (
        f"{fits} weeks claimed to fit in {available}s, but a 4-week amendment was "
        "measured at 53.8s and 60.9s per generation"
    )


def test_no_window_fits_an_exhausted_budget():
    assert amend.weeks_that_fit(0.0) == 0
    assert amend.weeks_that_fit(5.0) == 0


def test_max_tokens_is_sized_to_the_window_not_flat():
    assert amend.amend_max_tokens(1) < amend.amend_max_tokens(6)
    # A settled 4-week window came back as 28 sessions, so the cap must leave
    # real room — `generate_structured` RAISES on a max_tokens stop (#931).
    assert amend.amend_max_tokens(4) >= 6000
    assert amend.amend_max_tokens(6) <= 8192


# --- the refusal, end to end -------------------------------------------------
#
# The behaviour the runner actually meets: a window too wide to settle inside the
# request is declined IN CONVERSATION, before a token is spent, carrying the
# window that would have fitted. The old path started the generation and was
# severed partway, which is why the runner's two requests for next week's
# sessions are stored with no reply at all.

import asyncio
from unittest.mock import patch

from tests.test_schedule_amend_decides_first_987 import _plan, _user


class _RecordingClient:
    """Fails the test if it is ever called. Refusal must cost nothing."""

    def __init__(self):
        self.calls = 0

    async def generate_structured(
        self, *, system, user, tool, max_tokens=1024, timeout=None
    ):
        self.calls += 1
        raise AssertionError(
            "a window that cannot fit the budget must be refused before the "
            "generation starts, not by letting it be severed"
        )


def _propose(db, user, plan, *, weeks_from, weeks_through, budget):
    client = _RecordingClient()
    with patch.object(amend.turn, "build_client", return_value=client), \
         patch.object(amend.turn, "over_budget", return_value=False):
        proposal = asyncio.run(
            amend.propose_amendment(
                db, user, plan,
                weeks_from=weeks_from, weeks_through=weeks_through,
                instruction="write the next weeks",
                budget_seconds=budget,
            )
        )
    return proposal, client


def test_a_window_too_wide_for_the_budget_is_refused_without_generating(db):
    user, plan = _user(db), None
    plan = _plan(db, user)
    available = turn.TURN_BUDGET_SECONDS - turn.TURN_RESERVE_SECONDS

    proposal, client = _propose(
        db, user, plan, weeks_from=1, weeks_through=6, budget=available
    )

    assert proposal.ok is False
    assert client.calls == 0, "refusal spent tokens it did not need to"


def test_the_refusal_names_the_window_that_would_have_fitted(db):
    """Not a dead end: the coach is handed the smaller ask to offer instead."""
    user = _user(db)
    plan = _plan(db, user)
    available = turn.TURN_BUDGET_SECONDS - turn.TURN_RESERVE_SECONDS

    proposal, _ = _propose(
        db, user, plan, weeks_from=1, weeks_through=6, budget=available
    )

    detail = " ".join(proposal.failures or [])
    fits = amend.weeks_that_fit(available)
    assert "longer than one message allows" in detail
    assert f"weeks_through={fits}" in detail, (
        f"the refusal must name the narrower window to offer; got {detail!r}"
    )


class _CountingClient:
    """Counts calls and answers off-contract, so the loop rejects it.

    Deliberately NOT a client that raises: `propose_amendment` treats any
    exception from the call as a transport failure and retries, so a double that
    raises to signal "I was called" is swallowed and the test learns nothing.
    """

    def __init__(self):
        self.calls = 0

    async def generate_structured(
        self, *, system, user, tool, max_tokens=1024, timeout=None
    ):
        self.calls += 1
        return {"weeks": "not a list"}


def _count_calls(db, user, plan, *, weeks_from, weeks_through, budget):
    client = _CountingClient()
    with patch.object(amend.turn, "build_client", return_value=client), \
         patch.object(amend.turn, "over_budget", return_value=False):
        asyncio.run(
            amend.propose_amendment(
                db, user, plan,
                weeks_from=weeks_from, weeks_through=weeks_through,
                instruction="write the next weeks",
                budget_seconds=budget,
            )
        )
    return client.calls


def test_a_window_that_fits_is_not_refused(db):
    """The gate must not become a blanket refusal — one week is the common ask."""
    user = _user(db)
    plan = _plan(db, user)
    available = turn.TURN_BUDGET_SECONDS - turn.TURN_RESERVE_SECONDS

    calls = _count_calls(
        db, user, plan, weeks_from=1, weeks_through=1, budget=available
    )
    assert calls >= 1, "a one-week window must reach the model"


def test_no_budget_means_no_size_gate(db):
    """Outside a request there is no ceiling to respect, so nothing is refused."""
    user = _user(db)
    plan = _plan(db, user)

    calls = _count_calls(
        db, user, plan, weeks_from=1, weeks_through=6, budget=None
    )
    assert calls >= 1, "a six-week window must still run when nothing is waiting"
