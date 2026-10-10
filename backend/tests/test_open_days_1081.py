"""#1081: the days each floating session can still go on.

A day is open when the whole week can still be arranged legally with the
session on it, done sessions holding the day they were done. These tests hold
the search to that, and the week view to what it hands the runner.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas.schedule import SpacingRule
from app.services.schedule.placement import PlacedSession
from app.services.schedule.rules import open_days
from app.services.schedule.week import build_week
from tests.test_schedule_week import (
    MON,
    SAT,
    SUN,
    THU,
    TUE,
    WED,
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)

FRI = THU.fromordinal(THU.toordinal() + 1)

ONE_A_DAY = SpacingRule(kind="max_sessions_per_day", label="one a day", count=1)


@dataclass
class _S:
    id: str
    intent: str
    window_start: object
    window_end: object


# --- the search -----------------------------------------------------------------


def test_three_sessions_over_three_days_can_go_in_any_order():
    sessions = [_S(f"s{i}", "easy", TUE, THU) for i in range(3)]

    opened = open_days(sessions, [ONE_A_DAY], MON)

    assert all(opened[s.id] == [TUE, WED, THU] for s in sessions)


def test_a_done_session_uses_up_its_day():
    sessions = [_S(f"s{i}", "easy", TUE, THU) for i in range(2)]
    done_tuesday = PlacedSession(session_id="done", intent="easy", day=TUE)

    opened = open_days(sessions, [ONE_A_DAY], MON, fixed=[done_tuesday])

    assert all(opened[s.id] == [WED, THU] for s in sessions)


def test_a_rule_closes_the_day_it_forbids():
    no_quality_before_long = SpacingRule(
        kind="no_intent_day_before",
        label="no quality before long",
        before_intent="quality",
        target_intent="long",
    )
    quality = _S("q", "quality", THU, SAT)
    long_run = _S("l", "long", SUN, SUN)

    opened = open_days([quality, long_run], [no_quality_before_long], MON)

    assert opened["q"] == [THU, FRI]


def test_days_already_gone_are_not_offered():
    session = _S("s", "easy", MON, SUN)

    opened = open_days([session], [], THU)

    assert opened["s"][0] == THU


def test_a_week_that_cannot_be_arranged_keeps_each_sessions_own_days():
    sessions = [_S(f"s{i}", "easy", TUE, TUE) for i in range(2)]

    opened = open_days(sessions, [ONE_A_DAY], MON)

    assert opened == {"s0": [TUE], "s1": [TUE]}


def test_a_search_past_its_budget_says_so_rather_than_guessing():
    sessions = [_S(f"s{i}", "easy", TUE, THU) for i in range(3)]

    assert open_days(sessions, [ONE_A_DAY], MON, placements=1) is None


# --- the week the runner sees ----------------------------------------------------


def _one_a_day_user(db):
    user = _seed_user(db)
    user.profile.max_activities_per_day = 1
    db.commit()
    return user


def test_a_matched_activity_places_its_session_on_the_activitys_day(db):
    user = _one_a_day_user(db)
    plan = _seed_plan(db, user)
    activity = _seed_activity(db, user, day=TUE)
    _seed_session(
        db, plan, start=TUE, end=THU, title="Done run",
        completed_at=datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc),
    )
    from app.models.planned_session import PlannedSession

    row = db.query(PlannedSession).filter_by(title="Done run").one()
    row.completed_activity_id = activity.id
    db.commit()
    _seed_session(db, plan, start=TUE, end=THU, title="Still to do")

    week = build_week(db, user, today=TUE)

    by_title = {s.title: s for s in week.sessions}
    # Ticked on Thursday, but the run itself was on Tuesday.
    assert by_title["Done run"].done_on == TUE
    assert by_title["Still to do"].open_days == [WED, THU]


def test_a_hand_tick_counts_on_the_day_it_was_ticked(db):
    user = _one_a_day_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=TUE, end=THU, title="Walk",
        completed_at=datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc),
    )
    _seed_session(db, plan, start=TUE, end=THU, title="Easy run")

    week = build_week(db, user, today=TUE)

    by_title = {s.title: s for s in week.sessions}
    assert by_title["Walk"].done_on == WED
    assert by_title["Easy run"].open_days == [TUE, THU]


def test_a_suggestion_never_takes_a_day_from_a_committed_session(db):
    user = _one_a_day_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, end=WED, title="Committed")
    _seed_session(db, plan, start=TUE, end=TUE, title="Offer", commitment="suggested")

    week = build_week(db, user, today=MON)

    by_title = {s.title: s for s in week.sessions}
    assert by_title["Committed"].open_days == [TUE, WED]
    assert by_title["Offer"].open_days is None


def test_a_pinned_session_carries_no_open_days(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, title="Pinned")

    week = build_week(db, user, today=MON)

    assert week.sessions[0].open_days is None


def test_a_week_with_more_activities_than_the_limit_allows_is_reported_not_searched():
    """Seven walks plus a run, bike and gym against three a day used to try
    every ordering of them before giving up, which ran for minutes. The room
    check and the interchangeable-session ordering settle it at once."""
    from app.services.schedule.rules import check_rules

    sessions = (
        [_S(f"walk{i}", "easy", MON, SUN) for i in range(7)]
        + [_S(f"run{i}", "easy", MON, SUN) for i in range(5)]
        + [_S(f"bike{i}", "easy", TUE, SUN) for i in range(5)]
        + [_S(f"gym{i}", "strength", MON, SUN) for i in range(5)]
    )
    three_a_day = SpacingRule(kind="max_sessions_per_day", label="three", count=3)

    satisfiable, violations = check_rules(sessions, [three_a_day], MON)

    assert satisfiable is False
    assert [v["kind"] for v in violations] == ["max_sessions_per_day"]
