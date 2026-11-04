"""Shared constructed data for the schedule completion tests (#830).

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
SUN = MON + timedelta(days=6)


def _seed_user(db) -> User:
    user = User(email=f"sched-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_plan(db, user: User, *, status: str = "active") -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id, status=status, rules=[], week_shapes=[]
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _seed_session(
    db,
    plan: TrainingPlan,
    *,
    start: date = TUE,
    end: date = None,
    intent: str = "easy",
    discipline: str = "run",
    commitment: str = "committed",
    title: str = "Session",
    target_distance_m: float = None,
    target_duration_s: int = None,
    structure: dict = None,
    completed_at: datetime = None,
    completion_source: str = None,
    completed_activity_id=None,
    dismissed_at: datetime = None,
) -> PlannedSession:
    session = PlannedSession(
        plan_id=plan.id,
        user_id=plan.user_id,
        window_start=start,
        window_end=end or start,
        intent=intent,
        discipline=discipline,
        commitment=commitment,
        title=title,
        target_distance_m=target_distance_m,
        target_duration_s=target_duration_s,
        structure=structure,
        completed_at=completed_at,
        completion_source=completion_source,
        completed_activity_id=completed_activity_id,
        dismissed_at=dismissed_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _seed_activity(
    db,
    user: User,
    *,
    day: date = TUE,
    activity_type: str = "Run",
    distance_m: float = 10000,
    moving_time_s: int = 3000,
    utc_day: date = None,
) -> Activity:
    """An activity whose LOCAL day is `day`.

    `utc_day` lets a test put the stored UTC instant on a different calendar day,
    which is the only way to prove the matcher reads the runner's local day.
    """
    utc = utc_day or day
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(utc.year, utc.month, utc.day, 9, 0, tzinfo=timezone.utc),
        start_date_local=datetime(day.year, day.month, day.day, 9, 0),
        type=activity_type,
        name=activity_type,
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=moving_time_s,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity
