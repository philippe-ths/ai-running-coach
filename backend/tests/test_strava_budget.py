"""Global Strava API call budget (#544).

Strava rate-limits per APPLICATION, shared across every athlete; with several
runners onboarding at once the per-activity streams cost can exhaust the shared
ceiling and starve everyone's webhooks. These tests pin the throttle: a global
call counter over Strava's reset windows, recorded on every metered call, that the
background jobs consult and defer on — while the live webhook path never gates and
the gate degrades permissive (never an availability risk).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import settings
from app.services.strava_ingestion import HTTPStravaAdapter
from app.services.strava_ingestion import strava_budget
from app.services.strava_ingestion.strava_budget import (
    StravaBudgetGate,
    _InMemoryBackend,
    new_in_memory_gate,
)


@pytest.fixture
def gate():
    return StravaBudgetGate(_InMemoryBackend())


def _arm(monkeypatch, *, per_15min=0, per_day=0):
    monkeypatch.setattr(settings, "STRAVA_BUDGET_GLOBAL_PER_15MIN", per_15min)
    monkeypatch.setattr(settings, "STRAVA_BUDGET_GLOBAL_PER_DAY", per_day)


# --- the gate ------------------------------------------------------------------

def test_inert_when_ceilings_zero(gate, monkeypatch):
    # Default config: both windows disabled -> never over budget, however many
    # calls are recorded. This is what makes the PR behaviour-preserving until the
    # owner arms it with the deployed app's real limits.
    _arm(monkeypatch, per_15min=0, per_day=0)
    for _ in range(1000):
        gate.record()
    assert gate.over_budget() is False


def test_trips_on_15min_ceiling(gate, monkeypatch):
    _arm(monkeypatch, per_15min=3)
    gate.record()
    gate.record()
    assert gate.over_budget() is False  # 2 < 3
    gate.record()
    assert gate.over_budget() is True  # 3 >= 3


def test_trips_on_daily_ceiling_independently(gate, monkeypatch):
    # The daily window trips on its own even when the 15-min window is disabled.
    _arm(monkeypatch, per_15min=0, per_day=5)
    gate.record(calls=5)
    assert gate.over_budget() is True


def test_15min_window_is_clock_aligned_and_rolls_over(gate, monkeypatch):
    _arm(monkeypatch, per_15min=2)
    t0 = datetime(2026, 6, 27, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(strava_budget, "_now", lambda: t0)
    gate.record(calls=2)
    assert gate.over_budget() is True
    # 16 minutes later is a new clock-aligned 15-min bucket: the count resets.
    monkeypatch.setattr(
        strava_budget, "_now", lambda: datetime(2026, 6, 27, 10, 16, 0, tzinfo=timezone.utc)
    )
    assert gate.over_budget() is False


def test_record_of_zero_or_negative_is_a_noop(gate, monkeypatch):
    _arm(monkeypatch, per_15min=1)
    gate.record(calls=0)
    gate.record(calls=-5)
    assert gate.over_budget() is False


def test_over_budget_is_permissive_on_backend_error(monkeypatch):
    # A throttle must never become an availability risk: a backend that raises
    # reads as NOT over budget (the adapter's 429 backoff is the hard floor).
    failing = MagicMock()
    failing.get.side_effect = RuntimeError("redis down")
    g = StravaBudgetGate(failing)
    _arm(monkeypatch, per_15min=1)
    assert g.over_budget() is False


def test_record_never_raises_on_backend_error(monkeypatch):
    failing = MagicMock()
    failing.incr.side_effect = RuntimeError("redis down")
    g = StravaBudgetGate(failing)
    g.record()  # must not raise


def test_module_wrappers_use_injected_singleton(monkeypatch):
    _arm(monkeypatch, per_15min=1)
    strava_budget.set_gate(new_in_memory_gate())
    try:
        assert strava_budget.over_budget() is False
        strava_budget.record()
        assert strava_budget.over_budget() is True
    finally:
        strava_budget.set_gate(None)


# --- recording flows through the HTTP adapter ----------------------------------

def _install_seq_transport(monkeypatch, responses):
    queued = list(responses)

    async def handler(request):
        return queued.pop(0)

    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


@pytest.fixture
def _no_real_sleep():
    with patch(
        "app.services.strava_ingestion.http_adapter.asyncio.sleep", new=AsyncMock()
    ):
        yield


@pytest.mark.asyncio
async def test_adapter_records_one_call_per_request(monkeypatch):
    g = new_in_memory_gate()
    strava_budget.set_gate(g)
    try:
        _install_seq_transport(monkeypatch, [httpx.Response(200, json={"id": 1})])
        await HTTPStravaAdapter().get_activity("token", 1)
        assert g._backend.get(g._key(f"15m:{strava_budget._bucket_15min(strava_budget._now())}")) == 1
    finally:
        strava_budget.set_gate(None)


@pytest.mark.asyncio
async def test_adapter_records_each_retry_against_the_budget(monkeypatch, _no_real_sleep):
    # Every actual request counts against Strava's limit, so a 429-then-200 records
    # TWO calls -- the retry is real traffic, not free.
    g = new_in_memory_gate()
    strava_budget.set_gate(g)
    try:
        _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "0"}, json={}),
                httpx.Response(200, json={"id": 1}),
            ],
        )
        await HTTPStravaAdapter().get_activity("token", 1)
        key = g._key(f"15m:{strava_budget._bucket_15min(strava_budget._now())}")
        assert g._backend.get(key) == 2
    finally:
        strava_budget.set_gate(None)


# --- background jobs defer when over budget; the live path never gates ----------

def test_backfill_job_defers_when_over_budget(monkeypatch):
    import app.jobs.backfill_streams as bf

    monkeypatch.setattr(strava_budget, "over_budget", lambda: True)
    enqueue_in = MagicMock()
    monkeypatch.setattr("app.core.queue.queue.enqueue_in", enqueue_in)
    session_local = MagicMock()
    monkeypatch.setattr(bf, "SessionLocal", session_local)

    bf.backfill_streams_job("user-1")

    session_local.assert_not_called()  # no batch ran -> no Strava calls
    enqueue_in.assert_called_once()  # rescheduled after the backoff


def test_import_job_defers_when_over_budget(monkeypatch):
    import app.jobs.strava_import as si

    monkeypatch.setattr(strava_budget, "over_budget", lambda: True)
    enqueue_in = MagicMock()
    monkeypatch.setattr("app.core.queue.queue.enqueue_in", enqueue_in)
    session_local = MagicMock()
    monkeypatch.setattr(si, "SessionLocal", session_local)

    si.strava_import_job("00000000-0000-0000-0000-000000000000")

    session_local.assert_not_called()
    enqueue_in.assert_called_once()
