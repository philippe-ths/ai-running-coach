"""#1082 part A: a slot may offer choices, and a plan carries preferences.

The coach writes alternatives on a session and preferences on the plan; both
survive the whole draft chain onto the stored rows; any option ticks the slot
and records which one was done; and the week hands the options to the screen.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule import completion, store
from app.services.schedule.draft import draft_plan
from app.services.schedule.draft_contract import DraftedPlan, DraftedSession
from app.services.schedule.week import build_week
from tests.test_schedule_draft import (
    TODAY,
    TUE,
    WED,
    _FakeClient,
    _good_plan,
    _inject,
    _seed_history,
)
from tests.test_schedule_draft import _seed_user as _seed_draft_user
from tests.test_schedule_week import MON, _seed_plan, _seed_session, _seed_user

BIKE = {
    "intent": "easy",
    "discipline": "bike",
    "title": "Easy bike",
    "target_duration_s": 3600,
}


def _easy_run(**overrides):
    payload = {
        "window_start": TUE,
        "window_end": WED,
        "intent": "easy",
        "discipline": "run",
        "title": "Easy run",
        "target_duration_s": 2400,
        "alternatives": [BIKE],
    }
    payload.update(overrides)
    return payload


# --- the contract ----------------------------------------------------------------


def test_an_easy_run_may_offer_an_easy_bike():
    session = DraftedSession(**_easy_run())

    assert session.alternatives_json() == [BIKE]


@pytest.mark.parametrize(
    "overrides, why",
    [
        ({"alternatives": [{**BIKE, "target_duration_s": None}]}, "unsized option"),
        ({"intent": "rest", "target_duration_s": 0}, "a rest day has nothing to swap"),
        ({"commitment": "suggested"}, "a suggestion is already optional"),
        (
            {"alternatives": [{**BIKE, "intent": "quality", "title": "Hill reps"}]},
            "an option harder than the session",
        ),
        ({"alternatives": [BIKE, BIKE, BIKE]}, "more than three options"),
    ],
)
def test_a_choice_the_runner_could_not_safely_take_is_refused(overrides, why):
    with pytest.raises(ValidationError):
        DraftedSession(**_easy_run(**overrides))


def test_a_plan_states_known_preferences_only():
    DraftedPlan(rules=[], weeks=[], preferences=[{"kind": "strength_after_run"}])
    with pytest.raises(ValidationError):
        DraftedPlan(rules=[], weeks=[], preferences=[{"kind": "run_before_breakfast"}])


# --- the whole draft chain ---------------------------------------------------------


@pytest.mark.asyncio
async def test_choices_and_preferences_survive_the_draft_onto_the_rows(db, monkeypatch):
    user = _seed_draft_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    payload = _good_plan()
    payload["preferences"] = [{"kind": "spread_hard_days"}]
    payload["weeks"][0]["sessions"][0]["alternatives"] = [BIKE]
    _inject(monkeypatch, _FakeClient([payload]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok, outcome.failures
    row = db.query(PlannedSession).filter(PlannedSession.title == "Easy hour").one()
    assert row.alternatives == [BIKE]
    stored = db.get(TrainingPlan, plan.id)
    assert stored.preferences == [{"kind": "spread_hard_days"}]


# --- doing an option ----------------------------------------------------------------


def _choice_session(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(db, plan, start=MON, title="Easy run", target_duration_s=2400)
    session.alternatives = [BIKE]
    db.commit()
    return user, session


def test_a_ride_ticks_the_slot_as_its_bike_option(db):
    _, session = _choice_session(db)
    ride = SimpleNamespace(id=None, type="Ride", distance_m=20000, moving_time_s=3500)

    assert completion.matching_option(session, ride) == 1
    assert completion._closeness(session, ride) >= completion.MIN_COMPLETION_FRACTION

    completion.complete_planned_session(db, session, source=completion.AUTO, activity=ride)
    assert session.done_option == 1


def test_a_run_ticks_the_slot_as_the_session_itself(db):
    _, session = _choice_session(db)
    run = SimpleNamespace(id=None, type="Run", distance_m=6000, moving_time_s=2400)

    assert completion.matching_option(session, run) == 0


def test_a_tick_names_its_option_and_an_untick_clears_it(client, db):
    client.get("/api/profile")  # creates the test client's own user
    from app.models import User

    owner = db.query(User).first()
    plan = _seed_plan(db, owner)
    session = _seed_session(db, plan, start=MON, title="Easy run", target_duration_s=2400)
    session.alternatives = [BIKE]
    db.commit()

    assert client.post(f"/api/schedule/sessions/{session.id}/complete?option=2").status_code == 422
    assert client.post(f"/api/schedule/sessions/{session.id}/complete?option=1").status_code == 204
    db.refresh(session)
    assert session.done_option == 1

    client.delete(f"/api/schedule/sessions/{session.id}/complete")
    db.refresh(session)
    assert session.done_option is None


# --- the week the runner sees ----------------------------------------------------------


def test_the_week_hands_the_options_to_the_screen(db):
    user, session = _choice_session(db)
    session.completed_at = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
    session.done_option = 1
    db.commit()

    week = build_week(db, user, today=MON)

    [read] = week.sessions
    assert [alt.title for alt in read.alternatives] == ["Easy bike"]
    assert read.done_option == 1


def test_an_off_shape_option_is_dropped_not_the_whole_session(db):
    user, session = _choice_session(db)
    session.alternatives = [BIKE, {"title": "no shape at all"}]
    db.commit()

    week = build_week(db, user, today=MON)

    [read] = week.sessions
    assert [alt.title for alt in read.alternatives] == ["Easy bike"]


def test_rules_see_the_option_that_was_done(db):
    from app.services.schedule.week import _done_intent

    _, session = _choice_session(db)
    session.alternatives = [{**BIKE, "intent": "strength", "discipline": "strength", "title": "Gym"}]
    session.done_option = 1

    assert _done_intent(session) == "strength"
    session.done_option = 0
    assert _done_intent(session) == "easy"


def test_every_preference_kind_has_one_wording():
    from typing import get_args

    from app.schemas.schedule import PreferenceKind
    from app.services.schedule.preferences import PREFERENCE_TEXT

    assert set(get_args(PreferenceKind)) == set(PREFERENCE_TEXT)
