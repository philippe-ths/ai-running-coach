"""#946: generating one period report — the async lifecycle's worker-side half.

Mirrors `tests/test_schedule_draft.py`'s shape: a fake client injected at the one
seam a coaching turn gets its client from (`turn.build_client`), so NO TEST HERE
MAY REACH THE NETWORK.

Three things this file pins:
1. An EMPTY period never reaches the model at all — deterministic message, no
   spend, no chance to trip the safety floor over nothing.
2. An over-budget runner costs nothing: the gate is checked before any call.
3. The safety floor is not optional: a medical-overreach answer is rewritten
   once, and if it still overreaches the report FAILS rather than shipping it.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach import period_report as period_report_mod
from app.services.coach import period_report_store as store
from app.services.coach import turn
from app.services.coach.period_report import (
    PROMPT_ID,
    SCHEMA_VERSION,
    generate_period_report,
)

TODAY = date(2026, 8, 10)
FAKE_MODEL = "claude-fake-period-1"


class _FakeClient:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.model = FAKE_MODEL

    async def generate_structured(self, *, system, user, tool, max_tokens=1024):
        self.calls.append({"system": system, "user": user, "tool": tool})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _inject(monkeypatch, client, *, over_budget: bool = False):
    built = []

    def _build_client(kind, user_id):
        built.append((kind, user_id))
        return client

    monkeypatch.setattr(period_report_mod.turn, "build_client", _build_client)
    monkeypatch.setattr(period_report_mod.turn, "over_budget", lambda user_id: over_budget)
    return built


def _seed_user(db) -> User:
    user = User(email=f"period-report-gen-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_activity(db, user: User, *, day: date, effort_score: float = 30.0) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(day.year, day.month, day.day, 9, 0),
        type="Run",
        name="Run",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(activity_id=activity.id, effort_score=effort_score, confidence="high"))
    db.commit()
    return activity


def _seed_report(db, user: User, *, period_start=TODAY, period_end=None):
    return store.create_generating_report(
        db,
        user.id,
        period_start=period_start,
        period_end=period_end or (period_start + timedelta(days=6)),
        disciplines=[],
        prompt_id=PROMPT_ID,
        schema_version=SCHEMA_VERSION,
    )


@pytest.mark.asyncio
async def test_empty_period_never_calls_the_model(db, monkeypatch):
    user = _seed_user(db)
    report = _seed_report(db, user)
    built = _inject(monkeypatch, _FakeClient([]))

    outcome = await generate_period_report(db, user, report)

    assert outcome.ok
    assert built == []  # no client was even constructed
    db.refresh(report)
    assert report.status == store.READY
    assert report.report["headline"] == "Nothing logged this period"
    assert report.meta["empty_period"] is True


@pytest.mark.asyncio
async def test_over_budget_runner_costs_nothing(db, monkeypatch):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    built = _inject(monkeypatch, _FakeClient([]), over_budget=True)

    outcome = await generate_period_report(db, user, report)

    assert not outcome.ok
    assert outcome.failure_kind == store.FAILURE_OVER_BUDGET
    assert built == []
    db.refresh(report)
    assert report.status == store.FAILED
    assert report.meta["failure_kind"] == store.FAILURE_OVER_BUDGET


@pytest.mark.asyncio
async def test_a_clean_answer_is_stored_ready(db, monkeypatch):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    client = _FakeClient([
        {"message": "A steady week overall.", "headline": "Solid week", "next_steps": ["Keep it up"]},
    ])
    _inject(monkeypatch, client)

    outcome = await generate_period_report(db, user, report)

    assert outcome.ok
    db.refresh(report)
    assert report.status == store.READY
    assert report.report["message"] == "A steady week overall."
    assert report.model_id == FAKE_MODEL
    assert report.context_pack is not None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_transport_error_retries_once_then_fails(db, monkeypatch):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    client = _FakeClient([RuntimeError("boom"), RuntimeError("boom again")])
    _inject(monkeypatch, client)

    outcome = await generate_period_report(db, user, report)

    assert not outcome.ok
    assert outcome.failure_kind == store.FAILURE_UNREACHABLE
    assert len(client.calls) == 2
    db.refresh(report)
    assert report.status == store.FAILED


@pytest.mark.asyncio
async def test_off_contract_answer_is_rewritten_then_succeeds(db, monkeypatch):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    client = _FakeClient([
        {"headline": "no message field at all"},  # fails PeriodReportContent coercion
        {"message": "Fixed on the rewrite.", "headline": "Fixed", "next_steps": []},
    ])
    _inject(monkeypatch, client)

    outcome = await generate_period_report(db, user, report)

    assert outcome.ok
    assert len(client.calls) == 2
    assert "was not the shape" in client.calls[1]["user"]
    db.refresh(report)
    assert report.report["message"] == "Fixed on the rewrite."


@pytest.mark.asyncio
async def test_medical_overreach_is_rewritten_once_then_fails_if_it_persists(db, monkeypatch):
    """The safety floor is not optional (#946 requirement 5): a violation is
    never shipped, however close the rest of the answer was."""
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    overreach = {
        "message": "Based on your recent runs I diagnose you with overtraining syndrome.",
        "headline": "Concerning",
        "next_steps": [],
    }
    client = _FakeClient([overreach, overreach])
    _inject(monkeypatch, client)

    outcome = await generate_period_report(db, user, report)

    assert not outcome.ok
    assert outcome.failure_kind == store.FAILURE_POLICY
    assert len(client.calls) == 2
    db.refresh(report)
    assert report.status == store.FAILED
    assert report.report is None  # never shipped, even partially


@pytest.mark.asyncio
async def test_medical_overreach_that_self_corrects_on_rewrite_is_stored(db, monkeypatch):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    overreach = {
        "message": "Based on your recent runs I diagnose you with overtraining syndrome.",
        "headline": "Concerning",
        "next_steps": [],
    }
    clean = {"message": "This block trended easier through the month.", "headline": "Easier", "next_steps": []}
    client = _FakeClient([overreach, clean])
    _inject(monkeypatch, client)

    outcome = await generate_period_report(db, user, report)

    assert outcome.ok
    db.refresh(report)
    assert report.status == store.READY
    assert "diagnose" not in report.report["message"]


@pytest.mark.asyncio
async def test_uncalibrated_zone_reference_is_policed(db, monkeypatch):
    user = _seed_user(db)
    assert user.profile.hr_zones in (None, [])
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    client = _FakeClient([
        {"message": "You spent a lot of time in Z2 this block.", "headline": "Z2 heavy", "next_steps": []},
        {"message": "You spent a lot of time at an easy effort this block.", "headline": "Steady", "next_steps": []},
    ])
    _inject(monkeypatch, client)

    outcome = await generate_period_report(db, user, report)

    assert outcome.ok
    assert len(client.calls) == 2
    db.refresh(report)
    assert "Z2" not in report.report["message"]


@pytest.mark.asyncio
async def test_model_lane_resolves_from_turn_kind(db, monkeypatch):
    """#946 requirement 4: generation goes through `turn.build_client` with
    `TurnKind.PERIOD`, so spend is metered and the model lane is the ONE this
    surface owns."""
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)
    report = _seed_report(db, user)
    client = _FakeClient([{"message": "fine", "headline": "fine", "next_steps": []}])
    built = _inject(monkeypatch, client)

    await generate_period_report(db, user, report)

    assert built == [(turn.TurnKind.PERIOD, user.id)]


def test_period_model_id_falls_back_to_coach_model_id(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "COACH_PERIOD_MODEL_ID", "")
    monkeypatch.setattr(settings, "COACH_MODEL_ID", "claude-report-default")
    assert turn.resolve_model(turn.TurnKind.PERIOD) == "claude-report-default"

    monkeypatch.setattr(settings, "COACH_PERIOD_MODEL_ID", "claude-stronger-1")
    assert turn.resolve_model(turn.TurnKind.PERIOD) == "claude-stronger-1"
