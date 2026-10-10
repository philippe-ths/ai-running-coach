"""#1080: the runner's own daily limit, walks included.

The limit lives on the profile because a redraft rewrites the plan's rules
wholesale; it is merged into the plan's rules wherever they are checked or
shown. These tests hold each of those places to it, and hold "no limit set" to
the behaviour before it existed.
"""

from datetime import date as _date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.profile import UserProfileCreate
from app.services.schedule import amend
from app.services.schedule.coach_view import build_thread_schedule
from app.services.schedule.draft import _profile_lines, fetch_draft_facts
from app.services.schedule.plan_validator import validate_drafted_plan
from app.services.schedule.runner_rules import describe_for_coach, runner_rules
from app.services.weeks import MONDAY
from tests.test_schedule_plan_validator import MON as V_MON
from tests.test_schedule_plan_validator import TUE as V_TUE
from tests.test_schedule_plan_validator import _plan, _session, _week
from tests.test_schedule_week import MON, TUE, _seed_plan, _seed_session, _seed_user


class _Profile:
    def __init__(self, limit):
        self.max_activities_per_day = limit


# --- the setting --------------------------------------------------------------


def test_the_limit_round_trips_through_the_profile_api(client: TestClient, db):
    current = client.get("/api/profile").json()
    assert current["max_activities_per_day"] is None

    response = client.put(
        "/api/profile",
        json={
            "goal_type": current["goal_type"],
            "experience_level": current["experience_level"],
            "weekly_days_available": current["weekly_days_available"],
            "max_activities_per_day": 3,
        },
    )

    assert response.status_code == 200
    assert client.get("/api/profile").json()["max_activities_per_day"] == 3


@pytest.mark.parametrize("value", [0, 5])
def test_a_limit_outside_what_the_checker_allows_is_refused(value):
    with pytest.raises(ValidationError):
        UserProfileCreate(max_activities_per_day=value)


def test_no_limit_means_no_runner_rule():
    assert runner_rules(None) == []
    assert runner_rules(_Profile(None)) == []


# --- the runner's schedule ------------------------------------------------------


def _three_walks_and_runs_on_tuesday(db, user):
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, discipline="walk", title="Walk")
    _seed_session(db, plan, start=TUE, title="Easy run")
    _seed_session(db, plan, start=TUE, discipline="bike", title="Easy bike")
    return plan


def test_the_week_holds_a_walk_to_the_runners_limit(db):
    from app.services.schedule.week import build_week

    user = _seed_user(db)
    user.profile.max_activities_per_day = 2
    db.commit()
    _three_walks_and_runs_on_tuesday(db, user)

    week = build_week(db, user, today=MON)

    [rule] = week.rules
    assert (rule.kind, rule.count, rule.source) == ("max_sessions_per_day", 2, "runner")
    # Three on Tuesday breaks a limit of two only because the walk counts.
    assert [v.kind for v in week.violations] == ["max_sessions_per_day"]


def test_with_no_limit_set_the_same_week_is_unchanged(db):
    from app.services.schedule.week import build_week

    user = _seed_user(db)
    _three_walks_and_runs_on_tuesday(db, user)

    week = build_week(db, user, today=MON)

    assert week.rules == []
    assert week.violations == []


# --- the coach's drafted plan ---------------------------------------------------


def _stacked_tuesday():
    return _plan(
        weeks=[
            _week(
                sessions=[
                    _session(window_start=V_TUE, window_end=V_TUE, title=f"S{i}")
                    for i in range(3)
                ]
            )
        ]
    )


def test_a_drafted_plan_is_held_to_the_runners_limit():
    plan = _stacked_tuesday()

    without = validate_drafted_plan(plan, today=V_MON, norm_weekly_running_m=None)
    held = validate_drafted_plan(
        plan,
        today=V_MON,
        norm_weekly_running_m=None,
        runner_rules=runner_rules(_Profile(2)),
    )

    assert not any("max_sessions_per_day" in f or "in a day" in f for f in without.failures)
    assert held.ok is False
    assert any("set by the runner" in f for f in held.failures)


def test_the_coach_is_told_the_limit_is_the_runners_and_counts_walks():
    lines = _profile_lines(None, _Profile(3))

    assert any("Most activities in a day" in line and ": 3" in line for line in lines)

    [rule] = runner_rules(_Profile(3))
    text = describe_for_coach(rule)
    assert "never change it" in text
    assert "Walks count" in text


def test_an_amendment_prompt_lists_the_runners_limit(db):
    from tests.test_schedule_amend_decides_first_987 import _plan as _amend_plan
    from tests.test_schedule_amend_decides_first_987 import _user as _amend_user

    user = _amend_user(db)
    if user.profile is None:
        from app.models import UserProfile

        db.add(UserProfile(user_id=user.id))
        db.commit()
        db.refresh(user)
    user.profile.max_activities_per_day = 3
    db.commit()
    plan = _amend_plan(db, user)
    today = _date(2026, 8, 30)
    start, end = amend.resolve_window(today, MONDAY, weeks_from=1, weeks_through=2)

    ctx = amend.build_amend_context(
        db, user, plan, today=today, start=start, end=end,
        instruction="write these weeks", facts=fetch_draft_facts(db, user, today),
    )

    rules = ctx[ctx.index("## THE PLAN'S RULES"):]
    assert "At most 3 sessions in a day" in rules
    assert "The runner set this themselves" in rules


def test_the_coachs_view_of_the_plan_includes_the_runners_limit(db):
    user = _seed_user(db)
    user.profile.max_activities_per_day = 3
    db.commit()
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, title="Easy run")

    schedule = build_thread_schedule(db, user, today=MON)

    assert any("The runner set this themselves" in r for r in schedule["rules_in_play"])
