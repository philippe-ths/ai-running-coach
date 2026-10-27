"""#1162: an activity not planned for its day may take the day over the runner's
limit, and it never ticks a session shown for a later day.

The owner's scenario: Tuesday shows easy spin, intervals and strength (their
limit is 3), and they walk first. The rules are the owner's real plan rules,
read from production on 2026-10-27; every row is otherwise synthetic test setup.
"""

from datetime import datetime, timedelta, timezone

from app.models.planned_session import PlannedSession
from app.models.week_recommendation import WeekRecommendation
from app.services.schedule import completion
from app.services.schedule.week import build_week
from tests.test_schedule_week import (
    MON,
    SUN,
    THU,
    TUE,
    WED,
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)

OWNER_RULES = [
    {"kind": "rest_day_after", "label": "Nothing but rest the day after a long run",
     "source": "coach", "intent": "long"},
    {"kind": "no_intent_day_before", "label": "No quality session the day before a long run",
     "source": "coach", "before_intent": "quality", "target_intent": "long"},
    {"kind": "min_days_between", "label": "At least three days between hard sessions",
     "source": "coach", "intent_a": "quality", "intent_b": "quality", "days": 3},
]


def _owners_tuesday(db):
    user = _seed_user(db)
    user.profile.max_activities_per_day = 3
    db.commit()
    plan = _seed_plan(db, user, rules=OWNER_RULES)
    s = {
        "intervals": _seed_session(db, plan, start=TUE, intent="quality", title="Intervals"),
        "spin": _seed_session(db, plan, start=TUE, end=SUN - timedelta(days=1), discipline="bike", title="Easy spin"),
        "strength": _seed_session(db, plan, start=TUE, end=THU, intent="strength", discipline="strength", title="Strength"),
        "walk": _seed_session(db, plan, start=TUE, end=SUN, discipline="walk", title="Brisk walk"),
        "long": _seed_session(db, plan, start=SUN, intent="long", title="Long run"),
    }
    shown = {"intervals": TUE, "spin": TUE, "strength": TUE, "walk": THU, "long": SUN}
    db.add(
        WeekRecommendation(
            user_id=user.id, week_start=MON, plan_id=plan.id, changes=[],
            assignment={str(s[k].id): d.isoformat() for k, d in shown.items()},
        )
    )
    db.commit()
    return user, s


def _day(week, day):
    titles = {s.id: s.title for s in week.sessions}
    items = next((d.items for d in week.recommendation.days if d.day == day), [])
    return sorted(titles[i.session_id] for i in items)


def test_a_walk_before_a_full_day_moves_nothing_off_it(db):
    user, s = _owners_tuesday(db)
    assert _day(build_week(db, user, today=TUE), TUE) == ["Easy spin", "Intervals", "Strength"]

    walk = _seed_activity(db, user, day=TUE, activity_type="Walk", distance_m=5600)
    completion.complete_from_activity(db, walk)
    db.commit()
    week = build_week(db, user, today=TUE)

    assert _day(week, TUE) == ["Easy spin", "Intervals", "Strength"]
    assert [a.unplanned for a in week.logged] == [True]
    assert week.recommendation.changes == []
    assert not [v for v in week.violations if "more than" in v.detail]


def test_a_walk_never_ticks_the_walk_shown_for_a_later_day(db):
    user, s = _owners_tuesday(db)
    walk = _seed_activity(db, user, day=TUE, activity_type="Walk", distance_m=5600)

    assert completion.complete_from_activity(db, walk) is None
    db.refresh(s["walk"])
    assert s["walk"].completed_at is None


def test_a_session_done_on_its_shown_day_still_ticks(db):
    user, s = _owners_tuesday(db)
    walk = _seed_activity(db, user, day=THU, activity_type="Walk", distance_m=5600)

    assert completion.complete_from_activity(db, walk) == s["walk"]


def test_a_session_done_late_still_ticks(db):
    """Overdue is not the future: shown Monday, done Tuesday, it is that walk."""
    user, s = _owners_tuesday(db)
    row = db.query(WeekRecommendation).filter_by(user_id=user.id).one()
    row.assignment = {**row.assignment, str(s["walk"].id): MON.isoformat()}
    db.commit()
    walk = _seed_activity(db, user, day=WED, activity_type="Walk", distance_m=5600)

    assert completion.complete_from_activity(db, walk) == s["walk"]
