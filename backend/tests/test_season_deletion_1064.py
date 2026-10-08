"""#1064: deleting an account removes the schedule's rows, seasons included.

Seasons and plans point at each other (`training_plans.season_id`), sessions point
at plans and activities, plans point at goals: the cascade only works in one
order, and a miss is a foreign-key failure that leaves the account undeleted.

All row data is synthetic test setup.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.models import (
    Activity, GoalRace, PeriodReport, PlannedSession, Season, TrainingPlan, User,
)
from app.services.account_deletion import delete_user_account

OWNED = (Season, TrainingPlan, PlannedSession, GoalRace, PeriodReport)


def _seed(db, email):
    user = User(email=email)
    db.add(user)
    db.commit()
    activity = Activity(
        user_id=user.id, strava_activity_id=abs(hash(email)) % 10**8,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    goal = GoalRace(user_id=user.id, name="g", priority="A")
    season = Season(user_id=user.id, status="active", plan={}, goals_fingerprint="x")
    db.add_all([goal, season])
    db.commit()
    plan = TrainingPlan(user_id=user.id, status="active", goal_race_id=goal.id,
                        season_id=season.id, rules=[], week_shapes=[], draft_log={})
    db.add(plan)
    db.commit()
    db.add(PlannedSession(
        plan_id=plan.id, user_id=user.id, window_start=date(2026, 6, 1),
        window_end=date(2026, 6, 1), intent="easy", discipline="run", title="t",
        completed_activity_id=activity.id, completed_at=datetime.now(timezone.utc),
    ))
    db.add(PeriodReport(
        user_id=user.id, period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
        prompt_id="p", schema_version="1",
    ))
    db.commit()
    return user.id


def _count(db, user_id):
    return sum(db.query(m).filter(m.user_id == user_id).count() for m in OWNED)


def test_deleting_an_account_removes_its_seasons_plans_sessions_goals_and_period_reports(db):
    mine = _seed(db, f"del-a-{uuid4()}@example.com")
    theirs = _seed(db, f"del-b-{uuid4()}@example.com")
    assert _count(db, mine) == len(OWNED)

    counts = delete_user_account(db, mine)

    assert counts["seasons"] == 1
    assert _count(db, mine) == 0
    assert db.query(User).filter(User.id == mine).count() == 0
    assert _count(db, theirs) == len(OWNED)  # the other runner is untouched
    assert db.query(Activity).filter(Activity.user_id == theirs).count() == 1
