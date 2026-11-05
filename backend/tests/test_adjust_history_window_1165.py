"""How far back a session correction reads the runner's training (#1165).

The sibling of #1104: narrowing `adjust._HISTORY_DAYS` from 200 to 20 left every
adjust and schedule test green, because the adjust tests seed history only from
the last 60 days. The repriced load cannot pin the window on its own, since
`build_load_model` applies a shorter lookback of its own and hides any change
above 182 days, so this asserts on the rows the read hands to the load model.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.models import Activity, User
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule import adjust

TODAY = date(2026, 10, 27)


def _run(db, user, n, when: datetime) -> None:
    db.add(
        Activity(
            user_id=user.id,
            strava_activity_id=11650 + n,
            start_date=when,
            type="Run",
            name="Run",
            distance_m=8000,
            moving_time_s=2400,
            elapsed_time_s=2400,
        )
    )


def _at(days_ago: int, hour: int = 9) -> datetime:
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour, 0)


def test_a_correction_reads_exactly_the_last_200_days_through_today(db):
    user = User(email="adjust-history-window@example.com")
    db.add(user)
    db.commit()
    seeded = {
        "201 days ago": _at(201),
        "200 days ago": _at(200),
        "100 days ago": _at(100),
        "today": _at(0, hour=18),
        "tomorrow": _at(-1),
    }
    for n, when in enumerate(seeded.values()):
        _run(db, user, n, when)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    tomorrow = TODAY + timedelta(days=1)
    session = PlannedSession(
        plan_id=plan.id,
        user_id=user.id,
        window_start=tomorrow,
        window_end=tomorrow,
        intent="easy",
        discipline="run",
        commitment="committed",
        title="Easy run",
        target_distance_m=5000,
    )
    db.add(session)
    db.commit()

    with patch.object(adjust, "build_load_model", wraps=adjust.build_load_model) as model:
        adjust.adjust_planned_session(db, session, distance_m=6000, today=TODAY)

    read = {f.start_date.replace(tzinfo=None) for f in model.call_args.args[0]}
    assert {label for label, when in seeded.items() if when in read} == {
        "200 days ago",
        "100 days ago",
        "today",
    }
