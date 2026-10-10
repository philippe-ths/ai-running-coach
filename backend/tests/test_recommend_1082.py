"""#1082 part B: the recommended week.

Held to: every recommendation is legal under the rules; what does not fit is
dropped by the runner's priority; an unchanged week stays put and a missed day
says what moved; preferences and the day's order come with fixed reasons;
extras and hand ticks use up their day exactly once.
"""

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.week_recommendation import WeekRecommendation
from app.schemas.schedule import PlanPreference, SpacingRule
from app.services.schedule.placement import PlacedSession
from app.services.schedule.recommend import (
    REASON_ANY_TIME,
    REASON_KEY_FIRST,
    REASON_RUN_BEFORE_STRENGTH,
    alternative_notes,
    recommend_week,
)
from app.services.schedule.rules import violations_for
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

D = lambda n: MON + timedelta(days=n)  # noqa: E731


@dataclass
class _S:
    id: str
    intent: str
    discipline: str
    title: str
    window_start: date
    window_end: date


def _limit(n):
    return SpacingRule(kind="max_sessions_per_day", label="limit", count=n)


def _prefs(*kinds):
    return [PlanPreference(kind=k) for k in kinds]


def _by_day(rec, sessions):
    titles = {s.id: s.title for s in sessions}
    out = {}
    for p in rec.placements.values():
        out.setdefault(p.day, []).append(titles[p.session_id])
    return {day: sorted(names) for day, names in out.items()}


# --- legality and what does not fit -------------------------------------------------


def test_every_recommendation_keeps_every_rule():
    rng = random.Random(1082)
    rules_pool = [
        SpacingRule(kind="no_intent_day_before", label="x", before_intent="quality", target_intent="long"),
        SpacingRule(kind="rest_day_after", label="y", intent="long"),
    ]
    for trial in range(40):
        sessions = []
        for i in range(rng.randint(3, 16)):
            a = rng.randint(0, 6)
            b = rng.randint(a, 6)
            intent = rng.choice(["easy", "easy", "strength", "quality", "long"])
            sessions.append(_S(f"s{i}", intent, "run", f"S{i}", D(a), D(b)))
        rules = [_limit(rng.randint(1, 3))] + rng.sample(rules_pool, rng.randint(0, 2))

        rec = recommend_week(sessions, rules, _prefs("spread_hard_days"), MON)

        intents = {s.id: s.intent for s in sessions}
        placed = [
            PlacedSession(session_id=p.session_id, intent=intents[p.session_id], day=p.day)
            for p in rec.placements.values()
        ]
        assert violations_for(placed, rules) == [], f"trial {trial}"
        assert set(rec.placements) | set(rec.dropped) <= {s.id for s in sessions}


def test_what_does_not_fit_is_dropped_walks_first_long_run_last():
    # Two days left, one activity a day, four sessions: two must go.
    sessions = [
        _S("walk", "easy", "walk", "Walk", D(5), D(6)),
        _S("bike", "easy", "bike", "Easy bike", D(5), D(6)),
        _S("long", "long", "run", "Long run", D(5), D(6)),
        _S("gym", "strength", "strength", "Strength", D(5), D(6)),
    ]

    rec = recommend_week(sessions, [_limit(1)], [], D(5))

    assert set(rec.dropped) == {"walk", "bike"}
    assert set(rec.placements) == {"long", "gym"}


# --- staying put, and saying what moved ----------------------------------------------


def _week_of_easy(n=4):
    return [_S(f"e{i}", "easy", "run", "Easy run", D(0), D(6)) for i in range(n)]


def test_an_unchanged_week_stays_where_it_was_shown():
    sessions = _week_of_easy()
    first = recommend_week(sessions, [_limit(1)], [], MON)
    shown = {sid: p.day for sid, p in first.placements.items()}

    again = recommend_week(sessions, [_limit(1)], [], MON, previous=shown)

    assert {sid: p.day for sid, p in again.placements.items()} == shown
    assert again.moved == []


def test_a_session_whose_day_passed_moves_and_says_so():
    sessions = _week_of_easy()
    shown = {"e0": D(0), "e1": D(1), "e2": D(2), "e3": D(3)}

    # Tuesday: Monday's session was never done.
    rec = recommend_week(sessions, [_limit(1)], [], D(1), previous=shown)

    [(sid, before, after)] = rec.moved
    assert (sid, before) == ("e0", D(0))
    assert after >= D(1)
    # Everything else stayed.
    for other in ("e1", "e2", "e3"):
        assert rec.placements[other].day == shown[other]


def test_the_same_week_always_gets_the_same_recommendation():
    sessions = _week_of_easy(6) + [_S("g", "strength", "strength", "Strength", D(0), D(6))]
    prefs = _prefs("strength_after_run", "spread_repeats")

    a = recommend_week(sessions, [_limit(2)], prefs, MON)
    b = recommend_week(list(reversed(sessions)), [_limit(2)], prefs, MON)

    assert {k: v.day for k, v in a.placements.items()} == {k: v.day for k, v in b.placements.items()}


# --- preferences and the day's order ----------------------------------------------------


def test_strength_goes_on_a_run_day_when_the_coach_prefers_it():
    sessions = [
        _S("run", "easy", "run", "Easy run", D(2), D(2)),
        _S("gym", "strength", "strength", "Strength", D(0), D(6)),
    ]

    rec = recommend_week(sessions, [_limit(2)], _prefs("strength_after_run"), MON)

    assert rec.placements["gym"].day == D(2)
    assert rec.placements["run"].order == 1
    assert (rec.placements["gym"].order, rec.placements["gym"].reason) == (2, REASON_RUN_BEFORE_STRENGTH)


def test_repeats_are_spread_when_the_coach_prefers_it():
    sessions = [_S(f"b{i}", "easy", "bike", "Easy bike", D(0), D(6)) for i in range(3)]

    rec = recommend_week(sessions, [_limit(3)], _prefs("spread_repeats"), MON)

    assert len({p.day for p in rec.placements.values()}) == 3


def test_a_hard_session_leads_its_day_and_a_walk_is_any_time():
    sessions = [
        _S("walk", "easy", "walk", "Walk", D(1), D(1)),
        _S("easy", "easy", "run", "Easy run", D(1), D(1)),
        _S("ints", "quality", "run", "Intervals", D(1), D(1)),
    ]

    rec = recommend_week(sessions, [], [], MON)

    assert (rec.placements["ints"].order, rec.placements["ints"].reason) == (1, REASON_KEY_FIRST)
    assert rec.placements["easy"].order == 2
    assert (rec.placements["walk"].order, rec.placements["walk"].reason) == (None, REASON_ANY_TIME)


def test_a_day_where_order_does_not_matter_has_no_numbers():
    sessions = [
        _S("walk", "easy", "walk", "Walk", D(1), D(1)),
        _S("bike", "easy", "bike", "Easy bike", D(1), D(1)),
    ]

    rec = recommend_week(sessions, [], [], MON)

    assert {p.order for p in rec.placements.values()} == {None}


def test_an_alternative_says_what_taking_it_gives_or_costs():
    run = SimpleNamespace(discipline="run", intent="easy", target_duration_s=2400)
    bike = SimpleNamespace(discipline="bike", intent="easy", target_duration_s=3600)

    assert alternative_notes(run, [bike]) == [
        "Kinder on the legs, +20 min toward the week's time."
    ]


# --- the week the runner sees --------------------------------------------------------------


def _limited_user(db, limit):
    user = _seed_user(db)
    user.profile.max_activities_per_day = limit
    db.commit()
    return user


def test_an_extra_activity_uses_up_its_day(db):
    user = _limited_user(db, 1)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, end=WED, title="Easy run")
    _seed_activity(db, user, day=TUE, activity_type="Swim")

    week = build_week(db, user, today=TUE)

    [day] = week.recommendation.days
    assert day.day == WED
    assert week.sessions[0].open_days == [WED]


def test_a_hand_tick_and_its_recording_count_once(db):
    user = _limited_user(db, 2)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=TUE, end=TUE, title="Walk", discipline="walk",
        completed_at=datetime(2026, 8, 11, 18, tzinfo=timezone.utc),
    )
    _seed_activity(db, user, day=TUE, activity_type="Walk")
    _seed_session(db, plan, start=TUE, end=WED, title="Easy run")

    week = build_week(db, user, today=TUE)

    run = next(s for s in week.sessions if s.title == "Easy run")
    assert TUE in run.open_days


def test_the_current_week_remembers_what_it_showed_and_reports_a_missed_day(db):
    user = _limited_user(db, 1)
    plan = _seed_plan(db, user)
    for title in ("A", "B", "C"):
        _seed_session(db, plan, start=MON, end=THU, title=title)

    first = build_week(db, user, today=MON)
    shown = {item.session_id: day.day for day in first.recommendation.days for item in day.items}
    monday_title = next(
        s.title for s in first.sessions if shown.get(s.id) == MON
    )
    again = build_week(db, user, today=MON)
    assert again.recommendation.changes == []
    assert db.query(WeekRecommendation).count() == 1

    later = build_week(db, user, today=TUE)

    [change] = later.recommendation.changes
    assert change.startswith(f"{monday_title} not done Mon, moved to")


def test_a_past_week_has_no_recommendation(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=MON, end=SUN, title="Easy run")

    week = build_week(db, user, today=SUN + timedelta(days=3), target_week=MON)

    assert week.recommendation is None
