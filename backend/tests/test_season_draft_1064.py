"""#1064: the weekly plan is written INSIDE the season and checked against every goal.

The model writes real sessions for the weeks it is given a frame for; code writes
every later week as a shape; one check holds each written week to the challenge,
the usual walking and each dated goal; a numeric shortfall that survives the one
retry is closed by repair where the ceilings allow, and otherwise stored and
shown. NO TEST HERE MAY REACH THE NETWORK: the model is a scripted fake injected at
`turn.build_client`.

All data is synthetic test setup (exercises code paths; represents no real runner).
"""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.season import Season
from app.models.training_plan import TrainingPlan
from app.schemas.season import DraftLog
from app.services.coach import turn
from app.services.coach.llm import ReasonedCallFailed, Usage
from app.services.schedule import draft as draft_mod
from app.services.schedule import season_store, store
from app.services.schedule.draft import draft_plan
from app.services.schedule.frames import build_frames
from app.services.schedule.week_check import planned_metrics, rule_value
from tests._season_fixtures_1064 import THIS_WEEK, TODAY
from tests._week_fixtures_1064 import challenge_season, fact, owner_facts

FAKE_MODEL = "claude-fake-schedule-1064"
RACE_DAY = date(2026, 11, 8)


class FakeClient:
    model = FAKE_MODEL

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    async def generate_structured_reasoned(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, SimpleNamespace(
            input_tokens=12_000, output_tokens=6_000, web_search_requests=0
        )


@pytest.fixture
def user(db):
    row = User(email=f"draft-{uuid4()}@example.com")
    db.add(row)
    db.commit()
    db.add(UserProfile(user_id=row.id, goal_type="general", experience_level="intermediate",
                       weekly_days_available=6, max_hr=190))
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def goals(db, user):
    challenge = store.create_goal_race(
        db, user.id, name="10h a week", window_start=date(2026, 10, 1),
        window_end=date(2026, 12, 31), priority="C",
    )
    race = store.create_goal_race(
        db, user.id, name="Chatham 10k", race_date=RACE_DAY, distance_m=10_000,
        booked=True, priority="B",
    )
    return {"challenge": challenge, "race": race}


@pytest.fixture(autouse=True)
def _runner(monkeypatch):
    monkeypatch.setattr(draft_mod, "fetch_draft_facts", lambda db, user, today: owner_facts())
    monkeypatch.setattr(turn, "over_budget", lambda user_id: False)


def active_season(db, user, goals, **kw):
    row = season_store.create_drafting_season(db, user.id)
    row.plan = challenge_season(
        goals["challenge"], race=goals["race"], start=date(2026, 10, 12), **kw
    ).model_dump(mode="json")
    row.goals_fingerprint = season_store.stamp(
        store.list_goal_races(db, user.id, on_or_after=TODAY), TODAY
    )
    row.model_id = "claude-opus-5-5"
    return season_store.activate_season(db, row)


def inject(monkeypatch, client):
    monkeypatch.setattr(turn, "build_client", lambda kind, user_id: client)
    return client


def go(db, user, plan, **kw):
    return asyncio.run(draft_plan(db, user, plan, today=TODAY, **kw))


# --- an answer that holds the week ---------------------------------------------------


def _sess(day, discipline, hours, *, intent="easy", title=None, km=None):
    out = {
        "window_start": day.isoformat(), "window_end": day.isoformat(),
        "intent": intent, "discipline": discipline,
        "title": title or f"{intent} {discipline}",
        "target_duration_s": int(hours * 3600),
    }
    if km is not None:
        out["target_distance_m"] = km * 1000
    return out


def good_week(frame, *, race=None, skip_walks=False, bike_hours=2.0):
    """A compliant week for `frame`: walks to the floor, four rides and two runs
    (enough zone 2+ for a 10 h challenge), the race on its day when it has one."""
    days = [frame.week_start + timedelta(days=n) for n in range(7)]
    days = [d for d in days if d >= frame.today]
    sessions = []
    if not skip_walks:
        for d in days[:6]:
            sessions.append(_sess(d, "walk", 0.9, km=5.0))
    if frame.challenges:
        for d in days[:4]:
            sessions.append(_sess(d, "bike", bike_hours))
        sessions.append(_sess(days[-2], "run", 1.5))
        sessions.append(_sess(days[-3], "run", 1.5))
    else:
        sessions.append(_sess(days[-2], "run", 1.0, km=10))
    if frame.dated:
        sessions = [s for s in sessions if s["window_start"] != frame.dated[0].day.isoformat()]
        sessions.append(
            _sess(frame.dated[0].day, "run", 0.8, intent="quality",
                  title="Chatham 10k race", km=10)
        )
    return {"week_start": frame.week_start.isoformat(), "sessions": sessions}


def frames_of(db, user, season_plan=None):
    return build_frames(
        season=season_plan, goals=store.list_goal_races(db, user.id), facts=owner_facts(),
        starts_on=0, today=TODAY, horizon_weeks=settings.SCHEDULE_HORIZON_WEEKS,
    )


def concrete_frames(db, user, season_row):
    plan = season_store.season_plan(season_row)
    frames = frames_of(db, user, plan)
    return [f for f in frames if f.week_start <= date(2026, 11, 2)]


def answer(frames, **kw):
    return {
        "rules": [],
        "weeks": [good_week(f, **kw) for f in frames],
        "summary": "Hold the challenge through the race.",
    }


# --- the prompt ------------------------------------------------------------------------


def test_the_prompt_states_the_season_and_each_weeks_frame_plainly(db, user, goals, monkeypatch):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    client = inject(monkeypatch, FakeClient(answer(frames)))

    outcome = go(db, user, store.create_drafting_plan(db, user.id))

    assert outcome.ok, outcome.failures
    call = client.calls[0]
    text = call["user"]
    assert "## THE SEASON" in text and "The challenge first." in text
    assert "## THE WEEKS" in text
    assert 'Challenge "10h a week", week 1 of 10' in text
    assert "Chatham 10k is on Sun 2026-11-08, 10 km (booked)" in text
    assert "Commit at least" in text and "of walking" in text
    assert "A week holds at most 21 sessions" in text
    # Five concrete weeks: this week through the race's week.
    assert text.count("### Week of") == 5
    # The sums the model used to type are not asked for.
    assert "sketch" not in call["system"].lower() and "SHAPE ONLY" not in call["system"]
    assert "sketch_weeks" not in call["tool"]["input_schema"]["properties"]
    week_sessions = call["tool"]["input_schema"]["properties"]["weeks"]["items"][
        "properties"]["sessions"]
    assert week_sessions["maxItems"] == 21
    assert call["effort"] == "high" and call["web_search_max_uses"] == 0
    assert call["max_tokens"] == 2500 * 5 + 1500


def test_output_room_is_sized_to_the_weeks_asked_for_and_clamped():
    assert draft_mod._draft_max_tokens(1) == 6000
    assert draft_mod._draft_max_tokens(3) == 9000
    assert draft_mod._draft_max_tokens(6) == 16500
    assert draft_mod._draft_max_tokens(30) == 20000


# --- a plan that holds first time ------------------------------------------------------------


def test_a_plan_that_holds_first_time_is_stored_under_its_season_with_a_run_log(
    db, user, goals, monkeypatch
):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    inject(monkeypatch, FakeClient(answer(frames)))
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    assert outcome.ok, outcome.failures
    db.refresh(plan)
    assert plan.status == store.ACTIVE
    assert plan.season_id == season.id
    assert plan.model_id == FAKE_MODEL
    log = DraftLog.model_validate(plan.draft_log)
    assert (log.first_try_passed, log.checked) == (5, 5)
    assert len(log.attempts) == 1 and log.attempts[0].failures == []
    assert (log.attempts[0].input_tokens, log.attempts[0].output_tokens) == (12_000, 6_000)
    assert log.attempts[0].cost_usd > 0
    assert log.repairs == [] and log.shortfalls == []
    # The race is a committed session on its day.
    race = db.query(PlannedSession).filter(PlannedSession.plan_id == plan.id,
                                           PlannedSession.window_start == RACE_DAY).one()
    assert (race.intent, race.commitment) == ("quality", "committed")
    # And the challenge holds in every written challenge week, by the same metric
    # code the check uses, read back off the stored rows.
    for frame in frames:
        rows = db.query(PlannedSession).filter(
            PlannedSession.plan_id == plan.id,
            PlannedSession.window_start >= frame.week_start,
            PlannedSession.window_start <= frame.week_end,
        ).all()
        for c in frame.challenges:
            assert rule_value(c.rule, planned_metrics(rows, frame)) >= c.threshold - 60


def test_the_weeks_beyond_the_written_ones_are_shapes_written_by_code(
    db, user, goals, monkeypatch
):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    inject(monkeypatch, FakeClient(answer(frames)))
    plan = store.create_drafting_plan(db, user.id)

    assert go(db, user, plan).ok

    db.refresh(plan)
    shapes = {s.week_start: s for s in store.plan_week_shapes(plan)}
    written = {f.week_start for f in frames}
    later = [w for w in shapes if w not in written]
    assert len(later) == 7  # 12 weeks in all
    after_race = shapes[date(2026, 11, 9)]
    assert after_race.phase == "Base"
    # The season's own targets, interpolated, never a figure the model typed.
    assert after_race.target_running_distance_m == pytest.approx(40_000, abs=1)
    assert after_race.target_walking_distance_m == pytest.approx(30_000, abs=1)
    # The challenge week is topped up to reach its threshold at this runner's shares.
    all_frames = {f.week_start: f for f in frames_of(db, user, season_store.season_plan(season))}
    frame = all_frames[date(2026, 11, 9)]
    assert after_race.target_duration_s >= frame.targets.weekly_hours_s - 1
    assert sum(after_race.discipline_mix.values()) == pytest.approx(1.0, abs=0.01)
    # The challenge is counted from the seconds the shape states, not from the
    # load shares of its mix.
    zone = sum(seconds * frame.share(2, d)
               for d, seconds in after_race.duration_by_discipline_s.items())
    assert zone >= 36_000 - 60
    # Concrete weeks carry only their phase, from the frame.
    assert store.is_phase_only(shapes[date(2026, 11, 2)])
    assert shapes[date(2026, 11, 2)].phase == "Base"
    assert plan.horizon_end == date(2026, 12, 27)


def test_a_runner_with_no_season_still_gets_their_later_weeks_sketched_from_the_usual_week(
    db, user, monkeypatch
):
    """No goal, no season: the plan covers the concrete weeks with sessions and
    the rest of the horizon from the runner's usual week (phase Base, no
    challenge), so the 12-week view is not empty."""
    frames = frames_of(db, user, None)
    concrete = frames[: settings.SCHEDULE_CONCRETE_WEEKS]
    inject(monkeypatch, FakeClient(answer(concrete)))
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    assert outcome.ok, outcome.failures
    db.refresh(plan)
    shapes = {s.week_start: s for s in store.plan_week_shapes(plan)}
    later = [f for f in frames if f.week_start not in {c.week_start for c in concrete}]
    assert len(later) == 12 - settings.SCHEDULE_CONCRETE_WEEKS
    for frame in later:
        shape = shapes[frame.week_start]
        assert shape.phase == "Base"
        assert shape.target_duration_s == pytest.approx(frame.usual_total_s, rel=0.01)
        assert shape.target_walking_distance_m == pytest.approx(30_000, abs=1)
        assert shape.target_running_distance_m == pytest.approx(28_000, rel=0.02)
    assert plan.horizon_end == frames[-1].week_end
    assert plan.season_id is None


# --- the retry carries every failure, with its exact gap ---------------------------------------


def test_a_failing_plan_is_retried_once_with_every_failure_and_the_attempt(
    db, user, goals, monkeypatch
):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    bad = answer(frames)
    race_week = next(w for w in bad["weeks"] if w["week_start"] == "2026-11-02")
    race_week["sessions"] = [s for s in race_week["sessions"] if s["window_start"] != "2026-11-08"]
    challenge_week = next(w for w in bad["weeks"] if w["week_start"] == "2026-10-19")
    challenge_week["sessions"] = [s for s in challenge_week["sessions"] if s["discipline"] != "bike"]
    walk_week = next(w for w in bad["weeks"] if w["week_start"] == "2026-10-26")
    walk_week["sessions"] = [s for s in walk_week["sessions"] if s["discipline"] != "walk"]
    client = inject(monkeypatch, FakeClient(bad, answer(frames)))
    plan = store.create_drafting_plan(db, user.id)

    assert go(db, user, plan).ok

    retry = client.calls[1]["user"]
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" in retry
    # One of each kind of failure, in ONE retry.
    assert '"Chatham 10k" is on 8 Nov' in retry
    assert 'the challenge "10h a week" (week 2 of 10) needs 10.0 h' in retry
    assert "Week of 26 Oct: this runner usually walks 30 km a week" in retry
    assert "The attempt, for reference:" in retry
    db.refresh(plan)
    log = DraftLog.model_validate(plan.draft_log)
    assert len(log.attempts) == 2
    assert len(log.attempts[0].failures) >= 3 and log.attempts[1].failures == []
    # Only the three failing weeks missed first time.
    assert (log.first_try_passed, log.checked) == (2, 5)


def test_a_week_the_model_leaves_out_is_a_failure_not_a_blank_week(db, user, goals, monkeypatch):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    short = answer(frames)
    short["weeks"] = short["weeks"][:-1]
    client = inject(monkeypatch, FakeClient(short, answer(frames)))

    assert go(db, user, store.create_drafting_plan(db, user.id)).ok

    assert "one of the weeks to write as real sessions but the plan says nothing" in (
        client.calls[1]["user"]
    )


# --- a call that ran and gave no answer is a failed ANSWER, not an unreachable coach ---


def test_a_cut_off_plan_is_retried_and_its_cost_is_in_the_run_log(db, user, goals, monkeypatch):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    cut = ReasonedCallFailed(
        "truncated", Usage(input_tokens=15_000, output_tokens=20_000), truncated=True
    )
    client = inject(monkeypatch, FakeClient(cut, answer(frames)))
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    assert outcome.ok, outcome.failures
    db.refresh(plan)
    log = DraftLog.model_validate(plan.draft_log)
    assert len(log.attempts) == 2
    assert (log.attempts[0].input_tokens, log.attempts[0].output_tokens) == (15_000, 20_000)
    assert log.attempts[0].cost_usd > 0 and "cut off" in log.attempts[0].failures[0]
    assert "cut off" in client.calls[1]["user"]


def test_two_unusable_answers_fail_the_plan_without_calling_the_coach_unreachable(
    db, user, goals, monkeypatch
):
    active_season(db, user, goals)
    silent = ReasonedCallFailed("none", Usage(input_tokens=1, output_tokens=1), truncated=False)
    inject(monkeypatch, FakeClient(silent, silent))

    outcome = go(db, user, store.create_drafting_plan(db, user.id))

    assert not outcome.ok
    assert outcome.failure_kind != store.FAILURE_UNREACHABLE
    assert "could not be reached" not in " ".join(outcome.failures)
    assert "did not call" in " ".join(outcome.failures)


# --- repair, shortfalls, and what fails ------------------------------------------------------------


def test_a_numeric_shortfall_that_survives_the_retry_is_repaired_and_said(
    db, user, goals, monkeypatch
):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    thin = answer(frames, bike_hours=1.5)  # a quarter hour short of the threshold, every week
    client = inject(monkeypatch, FakeClient(thin, thin))
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    assert outcome.ok, outcome.failures
    assert len(client.calls) == 2  # one retry, then code
    db.refresh(plan)
    log = DraftLog.model_validate(plan.draft_log)
    assert log.repairs and all("zone 2+" in note or "walking" in note for note in log.repairs)
    assert log.shortfalls == []
    rows = db.query(PlannedSession).filter(PlannedSession.plan_id == plan.id).all()
    assert any("Added to hold the week's target" in (r.detail or "") or r.target_duration_s > 5400
               for r in rows)
    for frame in frames:
        week_rows = [r for r in rows if frame.week_start <= r.window_start <= frame.week_end]
        for c in frame.challenges:
            assert rule_value(c.rule, planned_metrics(week_rows, frame)) >= c.threshold - 60


def test_what_repair_cannot_close_is_stored_and_shown_never_hidden_never_a_failure(
    db, user, goals, monkeypatch
):
    # A runner whose whole usual week is 1.5 h of running has an hours ceiling of
    # 3 h: no legal week can reach 10 h of zone 2+, so the shortfall is real.
    tiny = [
        fact(THIS_WEEK - timedelta(weeks=n, days=-1 - 2 * k), kind="Run", seconds=1800,
             distance_m=5000, zones={"Z1": 0, "Z2": 1800, "Z3": 0, "Z4": 0, "Z5": 0})
        for n in range(1, 15) for k in range(3)
    ]
    monkeypatch.setattr(draft_mod, "fetch_draft_facts", lambda db, user, today: tiny)
    season = active_season(db, user, goals)
    frames = [f for f in build_frames(
        season=season_store.season_plan(season), goals=store.list_goal_races(db, user.id),
        facts=tiny, starts_on=0, today=TODAY, horizon_weeks=12,
    ) if f.week_start <= date(2026, 11, 2)]
    legal = {
        "rules": [],
        "weeks": [
            {"week_start": f.week_start.isoformat(),
             "sessions": [_sess(max(f.week_start, TODAY), "run", 1.0)]
             + ([_sess(f.dated[0].day, "run", 0.8, intent="quality",
                       title="Chatham 10k race", km=10)] if f.dated else [])}
            for f in frames
        ],
    }
    client = inject(monkeypatch, FakeClient(legal, legal))
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    assert outcome.ok, outcome.failures
    db.refresh(plan)
    log = DraftLog.model_validate(plan.draft_log)
    assert log.shortfalls
    assert any('under the 10.0 h "10h a week" needs' in line for line in log.shortfalls)
    assert any("estimated from your heart-rate history" in line for line in log.shortfalls)
    # Repair never ran past the ceiling it was given.
    ceiling = frames[0].hours_ceiling_s
    for frame in frames:
        rows = db.query(PlannedSession).filter(
            PlannedSession.plan_id == plan.id,
            PlannedSession.window_start >= frame.week_start,
            PlannedSession.window_start <= frame.week_end).all()
        assert sum(r.target_duration_s or 0 for r in rows) <= ceiling + 1


def test_a_structural_failure_after_the_retry_still_fails_the_draft(db, user, goals, monkeypatch):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    no_race = answer(frames)
    race_week = next(w for w in no_race["weeks"] if w["week_start"] == "2026-11-02")
    race_week["sessions"] = [s for s in race_week["sessions"] if s["window_start"] != "2026-11-08"]
    inject(monkeypatch, FakeClient(no_race, no_race))
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    assert outcome.ok is False
    assert '"Chatham 10k" is on 8 Nov' in " ".join(outcome.failures)
    assert db.query(PlannedSession).filter(PlannedSession.plan_id == plan.id).count() == 0
    db.refresh(plan)
    assert plan.status != store.ACTIVE


def test_a_numeric_and_a_structural_failure_together_are_not_repaired_into_a_plan(
    db, user, goals, monkeypatch
):
    season = active_season(db, user, goals)
    frames = concrete_frames(db, user, season)
    both = answer(frames, bike_hours=1.5)
    race_week = next(w for w in both["weeks"] if w["week_start"] == "2026-11-02")
    race_week["sessions"] = [s for s in race_week["sessions"] if s["window_start"] != "2026-11-08"]
    inject(monkeypatch, FakeClient(both, both))

    outcome = go(db, user, store.create_drafting_plan(db, user.id))

    assert outcome.ok is False


def test_with_no_season_the_draft_is_still_checked_for_walking_and_ceilings(
    db, user, monkeypatch
):
    frames = frames_of(db, user, None)[:3]
    no_walks = {"rules": [], "weeks": [good_week(f, skip_walks=True) for f in frames]}
    client = inject(monkeypatch, FakeClient(no_walks, no_walks))
    monkeypatch.setattr(draft_mod.settings, "SCHEDULE_CONCRETE_WEEKS", 3)
    plan = store.create_drafting_plan(db, user.id)

    outcome = go(db, user, plan)

    # Walking was the only fault, so repair adds the walks and the plan is stored.
    assert outcome.ok, outcome.failures
    assert "usually walks 30 km a week" in client.calls[1]["user"]
    db.refresh(plan)
    assert plan.season_id is None
    assert DraftLog.model_validate(plan.draft_log).repairs
