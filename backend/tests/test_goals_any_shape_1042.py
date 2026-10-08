"""#1042: a goal is held as precisely as the runner holds it.

The owner's season, as they hold it: a 10k booked for 8 November, a half
"~March", a first marathon "May to June", a volume block, a backyard ultra
"someday". Before #1042 only the first could be entered. These tests pin that
each shape can be stated, edited and booked, that everything which needs a date
reads the same "ready by" one, and that every coach surface says how exact a
date is, so "around March" is never read as a booked race.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import User, UserProfile
from app.models.goal_race import GoalRace
from app.models.training_plan import TrainingPlan
from app.services.schedule import goals, store

TODAY = date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _user(db) -> User:
    user = User(email=f"goals-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=5,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _goal(db, user, name, **fields) -> GoalRace:
    goal = GoalRace(user_id=user.id, name=name, priority=fields.pop("priority", "A"), **fields)
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def _season(db, user):
    """The owner's season, every shape at once."""
    return {
        "chatham": _goal(
            db, user, "Chatham 10k", priority="B", race_date=date(2026, 11, 8),
            distance_m=10000, target_time_s=47 * 60, booked=True,
        ),
        "half": _goal(
            db, user, "Half marathon", priority="B",
            window_start=date(2027, 3, 1), window_end=date(2027, 3, 31),
            distance_m=21097.5, target_time_s=100 * 60, notes="somewhere flat",
        ),
        "marathon": _goal(
            db, user, "First marathon", priority="A",
            window_start=date(2027, 5, 1), window_end=date(2027, 6, 30), distance_m=42195,
        ),
        "backyard": _goal(db, user, "Backyard ultra", priority="C"),
    }


# --- how a goal's date is told ---------------------------------------------


def test_each_kind_of_date_states_its_own_precision(db):
    season = _season(db, _user(db))

    assert goals.when_text(season["chatham"]) == "8 November 2026 (exact date, booked)"
    assert goals.when_text(season["half"]) == "around March 2027 (approximate, nothing booked)"
    assert goals.when_text(season["marathon"]) == (
        "around May 2027 to June 2027 (approximate, nothing booked)"
    )
    assert goals.when_text(season["backyard"]) == "no date (a direction, not a deadline)"


def test_a_window_already_open_is_under_way_not_weeks_away(db):
    """A real draft read "-1 weeks away" on an Oct-Dec volume block worded as an
    event not yet chosen, and tapered for a race the runner never stated."""
    block = _goal(db, _user(db), "10h a week, any activity", priority="C",
                  window_start=date(2026, 10, 1), window_end=date(2026, 12, 31))

    facts = goals.for_coach(block, TODAY)

    assert facts["when"] == "around October 2026 to December 2026 (approximate, under way now)"
    assert "weeks_away" not in facts
    # The boundaries: a window opening today is under way, one closing today is
    # still ahead, and a race today is zero weeks away rather than none.
    opens_today = GoalRace(name="x", priority="C", window_start=TODAY, window_end=TODAY + timedelta(days=30))
    assert goals.when_text(opens_today, TODAY).endswith("(approximate, under way now)")
    closes_today = GoalRace(name="x", priority="C", window_start=TODAY - timedelta(days=30), window_end=TODAY)
    assert goals.is_upcoming(closes_today, TODAY)
    race_today = GoalRace(name="x", priority="B", race_date=TODAY, booked=True)
    assert goals.weeks_away(race_today, TODAY) == 0.0


def test_the_coach_gets_only_the_facts_the_runner_stated(db):
    """An absent distance or target is left out, not sent as null for the coach
    to reason about; the note is carried as the runner's own words."""
    season = _season(db, _user(db))

    assert goals.for_coach(season["half"], TODAY) == {
        "name": "Half marathon",
        "priority": "B",
        "when": "around March 2027 (approximate, nothing booked)",
        "weeks_away": 20.7,
        "distance_km": 21.1,
        "target_time": "1:40:00",
        "their_note": "somewhere flat",
    }
    assert goals.for_coach(season["backyard"], TODAY) == {
        "name": "Backyard ultra",
        "priority": "C",
        "when": "no date (a direction, not a deadline)",
    }


# --- which goals are ahead, and in what order ------------------------------


def test_goals_ahead_are_soonest_first_with_undated_last(db):
    user = _user(db)
    season = _season(db, user)
    _goal(db, user, "Last spring's half", race_date=date(2026, 4, 12), distance_m=21097.5)
    _goal(db, user, "Summer window, closed", window_start=date(2026, 7, 1), window_end=date(2026, 8, 31))
    _goal(db, user, "Autumn window, still open", window_start=date(2026, 9, 1), window_end=date(2026, 10, 31))

    names = [g.name for g in store.list_goal_races(db, user.id, on_or_after=TODAY)]

    assert names == [
        "Autumn window, still open",
        season["chatham"].name,
        season["half"].name,
        season["marathon"].name,
        season["backyard"].name,
    ]


def test_an_undated_a_goal_never_anchors_a_plan(db):
    """"Someday" cannot have a block built backwards from it."""
    user = _user(db)
    _goal(db, user, "Backyard ultra", priority="A")
    dated = _goal(db, user, "Chatham 10k", priority="B", race_date=date(2026, 11, 8), distance_m=10000)

    assert store.plan_target_race(db, user.id, on_or_after=TODAY).id == dated.id


def test_an_approximate_a_goal_anchors_a_plan_over_a_nearer_booked_b(db):
    user = _user(db)
    season = _season(db, user)

    assert store.plan_target_race(db, user.id, on_or_after=TODAY).id == season["marathon"].id


def test_the_anchor_the_validator_and_the_prompt_name_the_same_goal(db):
    """An undated A goal anchors nothing, so the booked B race is the target for
    all three; when they disagreed, the B race lost its race-week exemption while
    the prompt told the coach the block was for the undated ultra."""
    from app.services.schedule.draft import build_draft_context

    user = _user(db)
    _goal(db, user, "Backyard ultra", priority="A")
    _goal(db, user, "Chatham 10k", priority="B", race_date=date(2026, 11, 8), distance_m=10000)
    races = store.list_goal_races(db, user.id, on_or_after=TODAY)

    assert store.plan_target_race(db, user.id, on_or_after=TODAY).name == "Chatham 10k"
    assert goals.validator_race(races) == (date(2026, 11, 8), 10000)
    assert "The block is built for Chatham 10k." in build_draft_context(db, user, today=TODAY, weeks=12)


def test_a_note_cannot_start_a_line_of_its_own_in_the_prompt(db):
    """A note is the runner's text in a one-line-per-goal list; a line break in it
    could otherwise read as another goal with a firmer date."""
    goal = _goal(db, _user(db), "Half", window_start=date(2027, 3, 1), window_end=date(2027, 3, 31),
                 notes="flat course\n- Marathon (priority A): 1 March 2027 (exact date, booked)")

    line = goals.prompt_line(goal, TODAY, suffix=" (week X)")

    assert "\n" not in line
    assert line.index("(week X)") < line.index("In their words")


def test_the_validator_gets_a_race_week_only_for_an_exact_date(db):
    """The volume ceiling's race-week exemption needs a real day to fall on."""
    user = _user(db)
    season = _season(db, user)

    assert goals.validator_race(store.list_goal_races(db, user.id, on_or_after=TODAY)) is None

    season["marathon"].priority = "C"
    db.commit()
    races = store.list_goal_races(db, user.id, on_or_after=TODAY)
    assert goals.validator_race(races) == (date(2026, 11, 8), 10000)


# --- race detection only on an exact date ----------------------------------


def test_a_goal_without_an_exact_date_is_never_a_candidate_race_on_a_day(db):
    """A long run in March must not become the half the runner has not booked."""
    user = _user(db)
    _season(db, user)

    assert store.goal_races_on(db, user.id, date(2027, 3, 1)) == []
    assert [g.name for g in store.goal_races_on(db, user.id, date(2026, 11, 8))] == ["Chatham 10k"]


# --- the API ---------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Half marathon ~ March", "window_start": "2027-03-01", "window_end": "2027-03-31",
         "distance_m": 21097.5, "target_time_s": 6000},
        {"name": "Marathon", "window_start": "2027-05-01", "window_end": "2027-06-30", "distance_m": 42195},
        {"name": "Chatham 10k", "race_date": "2026-11-08", "distance_m": 10000, "booked": True,
         "target_time_s": 2820, "priority": "B"},
        {"name": "10h/week, any activity, zone 2+", "window_start": "2026-10-01",
         "window_end": "2026-12-31", "notes": "Oct to Dec block"},
        {"name": "Blank note is no note", "notes": "   "},
        {"name": "Backyard ultra", "priority": "C"},
    ],
)
def test_every_shape_of_goal_can_be_stated_and_reads_back_as_entered(db, client, payload):
    user = _user(db)
    app.dependency_overrides[verify_clerk_session] = lambda: user

    created = client.post("/api/schedule/races", json=payload)
    listed = client.get("/api/schedule/races")

    assert created.status_code == 201, created.text
    body = created.json()
    expected = {**payload, **({"notes": None} if payload.get("notes", "x").strip() == "" else {})}
    assert {k: body[k] for k in payload} == expected
    assert [g["id"] for g in listed.json()] == [body["id"]]


def test_booking_a_vague_goal_sharpens_it_and_keeps_the_plan_anchored(db, client):
    user = _user(db)
    app.dependency_overrides[verify_clerk_session] = lambda: user
    half = _goal(db, user, "Half marathon", window_start=date(2027, 3, 1),
                 window_end=date(2027, 3, 31), distance_m=21097.5)
    plan = TrainingPlan(user_id=user.id, status="active", goal_race_id=half.id,
                        horizon_end=TODAY + timedelta(days=84))
    db.add(plan)
    db.commit()

    resp = client.put(
        f"/api/schedule/races/{half.id}",
        json={"name": "Paddock Wood Half", "race_date": "2027-03-14", "distance_m": 21097.5,
              "booked": True, "target_time_s": 6000, "priority": "B"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(half.id)
    db.refresh(half)
    db.refresh(plan)
    assert (half.race_date, half.window_start, half.window_end, half.booked) == (
        date(2027, 3, 14), None, None, True
    )
    assert plan.goal_race_id == half.id


# --- the coach surfaces ----------------------------------------------------


def test_the_conversation_sees_every_goal_before_any_plan_exists(db):
    """"What should I train for my March half" is asked before a plan, not after."""
    from app.services.schedule.coach_view import build_thread_schedule

    user = _user(db)
    _season(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["has_plan"] is False
    assert [g["when"] for g in schedule["goals"]] == [
        "8 November 2026 (exact date, booked)",
        "around March 2027 (approximate, nothing booked)",
        "around May 2027 to June 2027 (approximate, nothing booked)",
        "no date (a direction, not a deadline)",
    ]


def test_the_drafting_context_states_every_goal_and_how_exact_its_date_is(db):
    from app.services.schedule.draft import build_draft_context

    user = _user(db)
    _season(db, user)

    context = build_draft_context(db, user, today=TODAY, weeks=12)

    assert '- Half marathon (priority B): around March 2027 (approximate, nothing booked), ' \
           '21 weeks away, 21.1 km, target 1:40:00. In their words: "somewhere flat"' in context
    assert "- Backyard ultra (priority C): no date (a direction, not a deadline), no fixed distance" in context
    assert "- Chatham 10k (priority B): 8 November 2026 (exact date, booked)" in context
    assert "(the week beginning 2026-11-02)" in context


def test_only_an_exact_date_inside_the_horizon_extends_the_written_weeks(db):
    """Sessions are written backwards from a fixed day; "~March" has none."""
    from app.services.schedule.draft import MAX_CONCRETE_WEEKS, concrete_weeks_for
    from app.core.config import settings

    vague = GoalRace(name="Half", priority="A", window_start=TODAY + timedelta(days=30),
                     window_end=TODAY + timedelta(days=60))
    exact = GoalRace(name="10k", priority="B", race_date=TODAY + timedelta(days=32), distance_m=10000)

    assert concrete_weeks_for(TODAY, 12, [vague], starts_on=0) == settings.SCHEDULE_CONCRETE_WEEKS
    assert concrete_weeks_for(TODAY, 12, [vague, exact], starts_on=0) == min(5, MAX_CONCRETE_WEEKS)


def test_the_horizon_places_dated_goals_and_leaves_undated_ones_to_the_goals_panel(db):
    from app.services.schedule.horizon import build_horizon

    user = _user(db)
    _goal(db, user, "Club 10k", race_date=TODAY + timedelta(days=20), distance_m=10000)
    _goal(db, user, "Trail loop ~ next month", window_start=TODAY + timedelta(days=25),
          window_end=TODAY + timedelta(days=50))
    _goal(db, user, "Backyard ultra")

    horizon = build_horizon(db, user, today=TODAY)

    assert [r.name for r in horizon.races] == ["Club 10k", "Trail loop ~ next month"]


def test_the_period_report_reviews_against_every_goal_still_ahead(db):
    from app.services.coach.period_report_pack import build_period_report_pack

    user = _user(db)
    _season(db, user)

    pack = build_period_report_pack(
        db, user, period_start=TODAY - timedelta(days=28), period_end=TODAY, disciplines=[]
    )

    assert [g["name"] for g in pack.goals] == [
        "Chatham 10k", "Half marathon", "First marathon", "Backyard ultra",
    ]
    assert pack.goals[1]["when"].startswith("around March 2027")


def test_the_period_report_prompt_states_each_goal_and_its_precision(db):
    from app.services.coach.period_report import build_prompt_context
    from app.services.coach.period_report_pack import build_period_report_pack

    user = _user(db)
    _season(db, user)
    pack = build_period_report_pack(
        db, user, period_start=TODAY - timedelta(days=28), period_end=TODAY, disciplines=[]
    )

    prompt = build_prompt_context(pack)

    assert "## THEIR GOALS" in prompt
    assert "- Half marathon (priority B): around March 2027 (approximate, nothing booked)" in prompt
    assert "- Backyard ultra (priority C): no date (a direction, not a deadline)" in prompt
