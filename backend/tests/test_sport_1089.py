"""#1089: a session may name its exact sport, so the screen can show it.

Held to: a sport must belong to the session's discipline; it survives the
whole draft chain onto the stored row; and the week hands it to the screen.
"""

import pytest
from pydantic import ValidationError

from app.models.planned_session import PlannedSession
from app.services.schedule import store
from app.services.schedule.draft import draft_plan
from app.services.schedule.draft_contract import DraftedSession
from app.services.schedule.week import build_week
from tests.test_schedule_draft import (
    TODAY,
    TUE,
    _FakeClient,
    _good_plan,
    _inject,
    _seed_history,
)
from tests.test_schedule_draft import _seed_user as _seed_draft_user
from tests.test_schedule_week import MON, _seed_plan, _seed_session, _seed_user


def _session(discipline, sport, **extra):
    return DraftedSession(
        window_start=TUE, window_end=TUE, intent="easy", discipline=discipline,
        activity_type=sport, title="Session", target_duration_s=1800, **extra,
    )


@pytest.mark.parametrize(
    "discipline, sport, fits",
    [
        ("other", "Swim", True),
        ("walk", "Hike", True),
        ("run", "TrailRun", True),
        ("run", "Ride", False),
        ("run", "Swim", False),
    ],
)
def test_a_sport_must_belong_to_its_discipline(discipline, sport, fits):
    if fits:
        assert _session(discipline, sport).activity_type == sport
    else:
        with pytest.raises(ValidationError):
            _session(discipline, sport)


def test_an_alternative_is_held_to_the_same_fit():
    with pytest.raises(ValidationError):
        _session("run", None, alternatives=[{
            "intent": "easy", "discipline": "bike", "activity_type": "Swim",
            "title": "Swim", "target_duration_s": 1800,
        }])


@pytest.mark.asyncio
async def test_the_sport_survives_the_draft_and_reaches_the_screen(db, monkeypatch):
    user = _seed_draft_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    payload = _good_plan()
    payload["weeks"][0]["sessions"][0].update(discipline="other", activity_type="Swim")
    _inject(monkeypatch, _FakeClient([payload]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok, outcome.failures
    row = db.query(PlannedSession).filter(PlannedSession.title == "Easy hour").one()
    assert row.activity_type == "Swim"


def test_the_week_hands_the_sport_to_the_screen(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(db, plan, start=MON, title="Pool", discipline="other")
    session.activity_type = "Swim"
    db.commit()

    [read] = build_week(db, user, today=MON).sessions

    assert read.activity_type == "Swim"
