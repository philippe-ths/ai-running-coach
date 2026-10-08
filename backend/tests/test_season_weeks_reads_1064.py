"""#1064: the plan's reads and the paths around it.

The job that makes sure a season exists before the weeks are written, an
amendment held to the same frames, the horizon's challenge lines, and what the
coach is told about the season and each challenge week.

All data is synthetic test setup (exercises code paths; represents no real runner).
NO TEST HERE MAY REACH THE NETWORK OR REDIS.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.jobs import generate_schedule as job_mod
from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.coach import query_tools, turn
from app.services.schedule import amend as amend_mod
from app.services.schedule import draft as draft_mod
from app.services.schedule import season_store, store
from app.services.schedule.coach_view import build_thread_schedule, challenge_line
from app.services.schedule.draft import DraftOutcome
from app.services.schedule.horizon import build_horizon
from tests._season_fixtures_1064 import THIS_WEEK, TODAY
from tests._week_fixtures_1064 import challenge_season, fact, owner_facts

WEEK_2 = THIS_WEEK + timedelta(weeks=1)  # Mon 12 Oct
WEEK_3 = THIS_WEEK + timedelta(weeks=2)  # Mon 19 Oct
RACE_DAY = date(2026, 11, 8)


@pytest.fixture
def user(db):
    row = User(email=f"reads-{uuid4()}@example.com")
    db.add(row)
    db.commit()
    db.add(UserProfile(user_id=row.id, goal_type="general", experience_level="intermediate",
                       weekly_days_available=6, max_hr=190))
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def goals(db, user):
    return {
        "challenge": store.create_goal_race(
            db, user.id, name="10h a week", window_start=date(2026, 10, 1),
            window_end=date(2026, 12, 31), priority="C"),
        "race": store.create_goal_race(
            db, user.id, name="Chatham 10k", race_date=RACE_DAY, distance_m=10_000,
            booked=True, priority="B"),
    }


@pytest.fixture(autouse=True)
def _runner(monkeypatch):
    monkeypatch.setattr(draft_mod, "fetch_draft_facts", lambda db, user, today: owner_facts())
    monkeypatch.setattr(amend_mod, "fetch_draft_facts", lambda db, user, today: owner_facts())
    monkeypatch.setattr(turn, "over_budget", lambda user_id: False)


def active_season(db, user, goals, *, start=THIS_WEEK, weeks=10):
    row = season_store.create_drafting_season(db, user.id)
    row.plan = challenge_season(
        goals["challenge"], race=goals["race"], start=start, weeks=weeks
    ).model_dump(mode="json")
    row.goals_fingerprint = season_store.stamp(
        store.list_goal_races(db, user.id, on_or_after=TODAY), TODAY
    )
    return season_store.activate_season(db, row)


def add(db, plan, day, discipline, hours, *, intent="easy", km=None, commitment="committed", **kw):
    db.add(PlannedSession(
        plan_id=plan.id, user_id=plan.user_id, window_start=day, window_end=day,
        intent=intent, discipline=discipline, commitment=commitment,
        title=f"{intent} {discipline}", target_duration_s=int(hours * 3600),
        target_distance_m=km * 1000 if km else None, **kw))


class KeepOpen:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass

    def rollback(self):
        pass


# --- the job makes sure there is a season first ---------------------------------------


def run_job(db, user, plan, monkeypatch, *, season_outcome=None, draft_calls=None, thread_id=None):
    """Run the job with generation replaced; returns what each step was given."""
    seen = {"season": [], "draft": []}

    async def fake_season(db_, user_, season_, thread_id=None):
        seen["season"].append(thread_id)
        if season_outcome is not None and not season_outcome.ok:
            season_store.fail_season(db_, season_, season_outcome.message)
            return season_outcome
        season_.plan = {"summary": "s", "goals": [], "phases": []}
        season_.goals_fingerprint = season_store.stamp(
            store.list_goal_races(db_, user_.id, on_or_after=date.today()), date.today()
        )
        season_store.activate_season(db_, season_)
        return SimpleNamespace(ok=True, message=None)

    async def fake_draft(db_, user_, plan_, thread_id=None):
        seen["draft"].append(thread_id)
        plan_.status = store.ACTIVE
        db_.commit()
        return DraftOutcome(ok=True, plan_id=plan_.id)

    monkeypatch.setattr(job_mod, "SessionLocal", lambda: KeepOpen(db))
    monkeypatch.setattr(job_mod, "generate_season", fake_season)
    monkeypatch.setattr(job_mod, "draft_plan", fake_draft)
    job_mod.generate_schedule_job(str(user.id), str(plan.id), thread_id)
    return seen


def future_goal(db, user):
    return store.create_goal_race(
        db, user.id, name="Autumn half", race_date=date.today() + timedelta(days=60),
        distance_m=21_097.5, priority="A")


def test_a_runner_with_no_season_has_one_written_before_the_weeks(db, user, monkeypatch):
    future_goal(db, user)
    plan = store.create_drafting_plan(db, user.id)

    seen = run_job(db, user, plan, monkeypatch, thread_id=None)

    assert len(seen["season"]) == 1 and len(seen["draft"]) == 1
    assert season_store.active_season(db, user.id) is not None
    db.refresh(plan)
    assert plan.status == store.ACTIVE


def test_a_season_written_against_goals_the_runner_has_since_edited_is_rewritten(
    db, user, monkeypatch
):
    goal = future_goal(db, user)
    plan = store.create_drafting_plan(db, user.id)
    run_job(db, user, plan, monkeypatch)
    store.update_goal_race(db, goal, notes="actually a PB attempt")
    plan2 = store.create_drafting_plan(db, user.id)

    seen = run_job(db, user, plan2, monkeypatch)

    assert len(seen["season"]) == 1  # stale: the fingerprint moved


def test_a_current_season_is_not_rewritten(db, user, monkeypatch):
    future_goal(db, user)
    run_job(db, user, store.create_drafting_plan(db, user.id), monkeypatch)

    seen = run_job(db, user, store.create_drafting_plan(db, user.id), monkeypatch)

    assert seen["season"] == [] and len(seen["draft"]) == 1


def test_the_conversation_that_settled_the_plan_reaches_both_steps(db, user, monkeypatch):
    future_goal(db, user)
    tid = str(uuid4())

    seen = run_job(db, user, store.create_drafting_plan(db, user.id), monkeypatch, thread_id=tid)

    assert seen["season"] == [tid] and seen["draft"] == [tid]


@pytest.mark.parametrize(
    "message,kind",
    [
        (season_store.OVER_BUDGET_MESSAGE, store.FAILURE_OVER_BUDGET),
        (season_store.UNREACHABLE_MESSAGE, store.FAILURE_UNREACHABLE),
        (season_store.FAILURE_MESSAGE, store.FAILURE_SEASON),
    ],
)
def test_a_season_that_fails_fails_the_plan_with_the_matching_reason_and_no_weeks_are_written(
    db, user, monkeypatch, message, kind
):
    future_goal(db, user)
    plan = store.create_drafting_plan(db, user.id)

    seen = run_job(
        db, user, plan, monkeypatch,
        season_outcome=SimpleNamespace(ok=False, message=message),
    )

    assert seen["draft"] == []
    db.refresh(plan)
    assert plan.status == store.FAILED and plan.failure_kind == kind


def test_a_season_already_being_written_is_not_raced(db, user, monkeypatch):
    future_goal(db, user)
    season_store.create_drafting_season(db, user.id)
    plan = store.create_drafting_plan(db, user.id)

    seen = run_job(db, user, plan, monkeypatch)

    assert seen["season"] == [] and seen["draft"] == []
    db.refresh(plan)
    assert plan.status == store.FAILED


def test_a_runner_with_no_upcoming_goal_is_drafted_without_a_season(db, user, monkeypatch):
    plan = store.create_drafting_plan(db, user.id)

    seen = run_job(db, user, plan, monkeypatch)

    assert seen["season"] == [] and len(seen["draft"]) == 1


def test_the_drafting_job_gets_its_own_timeout_and_the_stale_rule_follows_it(monkeypatch):
    seen = {}

    class Q:
        def enqueue(self, fn, *args, **kwargs):
            seen.update(kwargs=kwargs)

    monkeypatch.setattr("app.core.queue.queue", Q())
    draft_mod.enqueue_draft(uuid4(), uuid4())

    timeout = draft_mod.settings.SCHEDULE_JOB_TIMEOUT_SECONDS
    assert seen["kwargs"]["job_timeout"] == timeout
    assert store.DRAFT_STALE_AFTER == timedelta(seconds=timeout + 180)
    # A season (thinking + searching) and the weeks, each with a retry, do not fit
    # the 600 s every other job gets.
    assert timeout > draft_mod.settings.RQ_JOB_TIMEOUT_SECONDS


def test_a_late_pickup_of_a_plan_already_failed_as_stale_does_not_draft_it(db, user, monkeypatch):
    plan = store.create_drafting_plan(db, user.id)
    store.fail_plan(db, plan, "abandoned")

    seen = run_job(db, user, plan, monkeypatch)

    assert seen["draft"] == [] and seen["season"] == []


def test_polling_a_draft_that_has_outlived_the_job_timeout_fails_it(db, user):
    plan = store.create_drafting_plan(db, user.id)
    plan.created_at = datetime.now(timezone.utc) - store.DRAFT_STALE_AFTER - timedelta(seconds=5)
    db.commit()

    assert store.draft_in_flight(db, user.id) is None
    db.refresh(plan)
    assert plan.status == store.FAILED


# --- the horizon ------------------------------------------------------------------------------


def measured_this_week(extra=()):
    ride = fact(THIS_WEEK + timedelta(days=1), kind="Ride", seconds=3 * 3600,
                distance_m=60_000, zones={"Z1": 3 * 3600 * 0.11, "Z2": 3 * 3600 * 0.89,
                                          "Z3": 0, "Z4": 0, "Z5": 0})
    return owner_facts() + [ride, *extra]


def horizon_plan(db, user):
    plan = TrainingPlan(
        user_id=user.id, status="active", rules=[], horizon_end=WEEK_3 + timedelta(days=6),
        draft_log={"shortfalls": ["The week of 19 Oct plans 9.1 h of zone 2+, under the 10.0 h needed."]},
        week_shapes=[{
            "week_start": WEEK_3.isoformat(), "phase": "Base",
            "target_duration_s": 36_000,
            "discipline_mix": {"run": 0.3, "bike": 0.5, "walk": 0.2},
        }],
    )
    db.add(plan)
    db.commit()
    for n in (1, 3):
        add(db, plan, WEEK_2 + timedelta(days=n), "run", 1.5)
    for n in (0, 2, 4, 6):
        add(db, plan, WEEK_2 + timedelta(days=n), "bike", 2.0)
    for n in range(6):
        add(db, plan, WEEK_2 + timedelta(days=n), "walk", 0.9, km=5.0)
    add(db, plan, WEEK_2 + timedelta(days=5), "bike", 5.0, commitment="suggested")
    db.commit()
    return plan


def test_the_horizon_carries_each_challenge_week_with_its_planned_and_measured_figures(
    db, user, goals, monkeypatch
):
    active_season(db, user, goals)
    monkeypatch.setattr(draft_mod, "fetch_draft_facts", lambda db, user, today: measured_this_week())
    horizon_plan(db, user)

    weeks = {w.week_start: w for w in build_horizon(db, user, weeks=4, today=TODAY).weeks}

    current = weeks[THIS_WEEK].challenges[0]
    assert (current.index, current.weeks, current.metric, current.min_zone) == (
        1, 10, "zone_time_s", 2)
    assert current.threshold == 36_000 and current.name == "10h a week"
    # Measured: the 3 h ride before today, at 89 % in zone 2+.
    assert current.actual == pytest.approx(3 * 3600 * 0.89)
    assert current.met is None  # the week is not over
    assert weeks[THIS_WEEK].challenges[0].planned is None  # nothing planned this week

    written = weeks[WEEK_2].challenges[0]
    # Hand-computed: 2 runs of 1.5 h at 98 %, 4 rides of 2 h at 89 %, 6 walks of
    # 0.9 h at 27 %, in hours; the suggestion is not a commitment.
    assert written.planned == pytest.approx(3600 * (3 * 0.98 + 8 * 0.89 + 5.4 * 0.27))
    assert written.index == 2 and written.actual is None and written.met is None

    sketched = weeks[WEEK_3].challenges[0]
    assert sketched.planned == pytest.approx(36_000 * (0.3 * 0.98 + 0.5 * 0.89 + 0.2 * 0.27))
    assert sketched.index == 3


def test_the_horizon_states_what_the_plan_is_still_short_of(db, user, goals, monkeypatch):
    active_season(db, user, goals)
    horizon_plan(db, user)

    read = build_horizon(db, user, weeks=4, today=TODAY)

    assert read.shortfalls == ["The week of 19 Oct plans 9.1 h of zone 2+, under the 10.0 h needed."]


def test_without_a_season_the_horizon_has_no_challenges_and_is_otherwise_unchanged(
    db, user, goals
):
    horizon_plan(db, user)

    read = build_horizon(db, user, weeks=4, today=TODAY)

    assert all(w.challenges == [] for w in read.weeks)
    assert read.weeks[1].coverage == "planned"


def test_a_challenge_week_the_plan_says_nothing_about_has_a_threshold_and_no_plan_figure(
    db, user, goals, monkeypatch
):
    active_season(db, user, goals)
    horizon_plan(db, user)

    weeks = {w.week_start: w for w in build_horizon(db, user, weeks=6, today=TODAY).weeks}

    empty = weeks[WEEK_3 + timedelta(weeks=2)].challenges[0]
    assert empty.planned is None and empty.threshold == 36_000


def test_the_season_read_fills_each_challenge_weeks_planned_figure(db, user, goals, client):
    from app.core.clerk_auth import verify_clerk_session
    from app.main import app

    active_season(db, user, goals, start=date.today() - timedelta(days=date.today().weekday()))
    app.dependency_overrides[verify_clerk_session] = lambda: user
    try:
        body = client.get("/api/schedule/season").json()
    finally:
        app.dependency_overrides.pop(verify_clerk_session, None)

    (status,) = body["challenges"]
    assert len(status["weeks"]) == 10
    assert all("planned" in w for w in status["weeks"])


# --- what the coach is told ---------------------------------------------------------------------


def test_a_challenge_week_is_stated_to_the_coach_with_each_figure_labelled_for_what_it_is():
    line = challenge_line(SimpleNamespace(
        name="10h a week", index=4, weeks=10, metric="zone_time_s", min_zone=2,
        threshold=36_000, planned=34_560, actual=10_000, met=False))

    assert line["week"] == "4 of 10" and line["needs"] == "10.0 h in zone 2 or above"
    assert line["planned"] == (
        "9.6 h (an estimate from the runner's own heart-rate shares, not measured)")
    assert line["done_so_far_measured"] == "2.8 h"
    assert line["week_over_and_met"] is False
    # A distance challenge needs no estimate caveat, and a week not over says no `met`.
    km = challenge_line(SimpleNamespace(
        name="x", index=1, weeks=2, metric="distance_m", min_zone=None,
        threshold=50_000, planned=40_000, actual=None, met=None))
    assert km["planned"] == "40.0 km" and "week_over_and_met" not in km


def test_the_conversation_is_told_the_season_goal_by_goal(db, user, goals):
    active_season(db, user, goals)
    horizon_plan(db, user)

    out = build_thread_schedule(db, user, today=TODAY)

    season = out["season"]
    assert season["summary"] == "The challenge first."
    by_name = {g["name"]: g for g in season["goals"]}
    assert by_name["Chatham 10k"]["date"] == "2026-11-08"
    assert by_name["Chatham 10k"]["booked"] is True
    assert by_name["10h a week"]["kind"] == "challenge"
    assert by_name["10h a week"]["challenge_rule"] == (
        "at least 10.0 h in zone 2 or above in each of 10 straight weeks from the week of "
        "2026-10-05")
    assert out["plan_is_still_short_of"] == [
        "The week of 19 Oct plans 9.1 h of zone 2+, under the 10.0 h needed."]


def test_a_season_the_goals_have_outgrown_is_flagged_to_the_coach(db, user, goals):
    active_season(db, user, goals)
    store.update_goal_race(db, goals["race"], notes="moved the date in my head")

    out = build_thread_schedule(db, user, today=TODAY)

    assert "changed their goals" in out["season"]["stale"]


def test_the_coachs_plan_tool_carries_the_challenge_line_per_week(db, user, goals):
    active_season(db, user, goals)
    horizon_plan(db, user)

    out = query_tools.get_training_plan(db, user.id, today=TODAY)

    by_week = {w["week_start"]: w for w in out["weeks"]}
    line = by_week[WEEK_2.isoformat()]["challenges"][0]
    assert line["challenge"] == "10h a week" and line["week"] == "2 of 10"
    assert "an estimate from the runner's own heart-rate shares" in line["planned"]
    assert out["season"]["summary"] == "The challenge first."
    assert out["plan_is_still_short_of"]


# --- an amendment is held to the same frames ---------------------------------------------------------


def amend_plan_row(db, user):
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    return plan


def amended_answer(*, bike_hours):
    sessions = []
    for n in (1, 3):
        sessions.append(_raw(WEEK_2 + timedelta(days=n), "run", 1.5))
    for n in (0, 2, 4, 6):
        sessions.append(_raw(WEEK_2 + timedelta(days=n), "bike", bike_hours))
    for n in range(6):
        sessions.append(_raw(WEEK_2 + timedelta(days=n), "walk", 0.9, km=5.0))
    return {"weeks": [{"week_start": WEEK_2.isoformat(), "sessions": sessions}],
            "summary": "More riding."}


def _raw(day, discipline, hours, *, km=None):
    out = {"window_start": day.isoformat(), "window_end": day.isoformat(),
           "intent": "easy", "discipline": discipline, "title": f"easy {discipline}",
           "target_duration_s": int(hours * 3600)}
    if km:
        out["target_distance_m"] = km * 1000
    return out


class AmendClient:
    model = "fake"

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    async def generate_structured(self, *, system, user, tool, max_tokens=1024, timeout=None):
        self.calls.append(user)
        return self.script.pop(0)


def propose(db, user, plan, client):
    with patch.object(amend_mod.turn, "build_client", return_value=client):
        return asyncio.run(
            amend_mod.propose_amendment(
                db, user, plan, weeks_from=1, weeks_through=1, instruction="more riding",
                today=TODAY))


def test_an_amendment_that_holds_the_challenge_passes_first_time(db, user, goals):
    active_season(db, user, goals)
    client = AmendClient(amended_answer(bike_hours=2.0))

    proposal = propose(db, user, amend_plan_row(db, user), client)

    assert proposal.ok and len(client.calls) == 1
    assert proposal.repairs == [] and proposal.shortfalls == []


def test_an_amendment_that_breaks_the_challenge_is_sent_back_with_the_exact_gap(db, user, goals):
    active_season(db, user, goals)
    client = AmendClient(amended_answer(bike_hours=1.0), amended_answer(bike_hours=2.0))

    proposal = propose(db, user, amend_plan_row(db, user), client)

    assert proposal.ok and len(client.calls) == 2
    assert 'the challenge "10h a week" (week 2 of 10) needs 10.0 h' in client.calls[1]
    assert "Add" in client.calls[1]


def test_a_numeric_shortfall_the_retry_does_not_fix_is_repaired_and_said_on_the_card(
    db, user, goals
):
    active_season(db, user, goals)
    short = amended_answer(bike_hours=1.5)
    client = AmendClient(short, short)

    proposal = propose(db, user, amend_plan_row(db, user), client)

    assert proposal.ok
    assert proposal.repairs
    assert any("Added" in line for line in proposal.changes)
    ride_hours = sum(s.target_duration_s for w in proposal.amended.weeks for s in w.sessions
                     if s.discipline in ("bike", "run")) / 3600
    assert ride_hours > 3 + 6  # the repair put real hours in


def test_an_amendment_that_cannot_be_repaired_into_coherence_is_refused(db, user, goals):
    active_season(db, user, goals)
    # Short AND over a rule the plan holds: structure outranks arithmetic.
    from app.schemas.schedule import SpacingRule

    plan = amend_plan_row(db, user)
    plan.rules = [SpacingRule(kind="max_sessions_per_day", label="one a day", count=1
                              ).model_dump(mode="json")]
    db.commit()
    short = amended_answer(bike_hours=1.5)
    client = AmendClient(short, short)

    proposal = propose(db, user, plan, client)

    assert proposal.ok is False
