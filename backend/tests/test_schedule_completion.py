"""#830: marking a planned session done — three ways in, one write.

The matcher is the interesting half. Strava never sees the gym and a 2 km jog is
not the 18 km long run it happened to fall inside the window of, so auto-matching
only fires when it is CONFIDENT. This file pins that conservatism, the ranking
that decides between two plausible sessions, the single writer every route ends
at, the idempotency that stops one activity ticking two sessions, the guard that
keeps a schedule fault out of the ingestion pipeline, the projection that finally
feeds the interval matcher, the conversational route in, and the delete path's
refusal to unsay something the runner said.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models import Activity, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.analysis import _orchestrator
from app.services.schedule import completion

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
SUN = MON + timedelta(days=6)


# --- setup -----------------------------------------------------------------


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


# --- the matcher is deliberately conservative ------------------------------


def test_a_two_km_jog_does_not_complete_an_eighteen_km_long_run(db):
    """The headline case.

    A missed auto-match costs one tap. A wrong one tells the runner they did a
    session they did not do, and quietly feeds that into the plan-versus-actual
    read — so the fraction is the whole point of the matcher existing.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    long_run = _seed_session(
        db, plan, intent="long", title="18 km long run", target_distance_m=18000
    )
    jog = _seed_activity(db, user, distance_m=2000)

    assert completion.find_matching_session(db, jog) is None
    assert completion.complete_from_activity(db, jog) is None

    db.refresh(long_run)
    assert long_run.completed_at is None


def test_the_fraction_is_a_floor_not_a_target(db):
    """A coach's "8 km easy" is satisfied by 7 km and the runner should not have
    to tap for that. Exactly at the floor still counts; a metre under does not."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, target_distance_m=18000, title="Long run")

    at_the_floor = _seed_activity(db, user, distance_m=18000 * 0.5)
    under_it = _seed_activity(db, user, distance_m=18000 * 0.5 - 1)

    assert completion.find_matching_session(db, at_the_floor) is not None
    assert completion.find_matching_session(db, under_it) is None


def test_a_gym_session_completes_a_planned_strength_session(db):
    """Strava never sees the gym as a run, and the schedule plans the whole of
    the runner's training rather than only the part that has a distance."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    strength = _seed_session(
        db,
        plan,
        intent="strength",
        discipline="strength",
        title="Lower body",
        target_duration_s=3600,
    )
    gym = _seed_activity(
        db,
        user,
        activity_type="WeightTraining",
        distance_m=0,
        moving_time_s=3000,
    )

    assert completion.find_matching_session(db, gym) == strength


def test_a_ride_completes_a_planned_bike_session(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    bike = _seed_session(
        db, plan, discipline="bike", title="Turbo", target_duration_s=3600
    )
    ride = _seed_activity(
        db, user, activity_type="Ride", distance_m=30000, moving_time_s=3600
    )

    assert completion.find_matching_session(db, ride) == bike


def test_a_run_never_completes_a_session_of_another_discipline(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, discipline="bike", title="Turbo")
    run = _seed_activity(db, user, activity_type="Run")

    assert completion.find_matching_session(db, run) is None


def test_a_prescribed_rest_is_never_auto_matched(db):
    """A rest day is finished by not training. Nothing the runner logged can be
    the evidence that they rested."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    rest = _seed_session(db, plan, intent="rest", discipline="run", title="Rest")
    run = _seed_activity(db, user)

    assert completion.find_matching_session(db, run) is None
    db.refresh(rest)
    assert rest.completed_at is None


def test_an_activity_outside_the_window_matches_nothing(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, end=WED, title="Tue-Wed")
    on_sunday = _seed_activity(db, user, day=SUN)

    assert completion.find_matching_session(db, on_sunday) is None


def test_the_window_is_tested_against_the_runners_local_day(db):
    """A late-evening run stored as tomorrow in UTC still belongs to the day the
    runner ran it, which is the day their plan named."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(db, plan, start=TUE, end=TUE, title="Tuesday easy")
    late_run = _seed_activity(db, user, day=TUE, utc_day=WED)

    assert completion.find_matching_session(db, late_run) == session


def test_a_session_already_finished_or_declined_is_not_matched_again(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        title="Already done",
        completed_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
        completion_source=completion.MANUAL,
    )
    _seed_session(
        db,
        plan,
        title="Declined",
        commitment="suggested",
        dismissed_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
    )
    run = _seed_activity(db, user)

    assert completion.open_sessions_for(db, run) == []
    assert completion.find_matching_session(db, run) is None


def test_a_superseded_plans_sessions_are_never_matched(db):
    """A replaced plan is history. It must not absorb today's run."""
    user = _seed_user(db)
    old = _seed_plan(db, user, status="superseded")
    stale = _seed_session(db, old, title="From the replaced plan")
    run = _seed_activity(db, user)

    assert completion.find_matching_session(db, run) is None
    db.refresh(stale)
    assert stale.completed_at is None


def test_the_active_plan_is_the_one_that_absorbs_the_run(db):
    user = _seed_user(db)
    old = _seed_plan(db, user, status="superseded")
    _seed_session(db, old, title="From the replaced plan")
    current = _seed_plan(db, user, status="active")
    live = _seed_session(db, current, title="From the live plan")
    run = _seed_activity(db, user)

    assert completion.find_matching_session(db, run) == live


def test_another_runners_planned_session_is_never_matched(db):
    """Tenant scoping, at the layer that decides what gets written."""
    mine = _seed_user(db)
    theirs = _seed_user(db)
    their_plan = _seed_plan(db, theirs)
    their_session = _seed_session(db, their_plan, title="Not mine")
    my_run = _seed_activity(db, mine)

    assert completion.find_matching_session(db, my_run) is None
    assert completion.complete_from_activity(db, my_run) is None
    db.refresh(their_session)
    assert their_session.completed_at is None


def test_a_session_with_no_target_at_all_is_matchable(db):
    """There is nothing to be far from, so closeness is 1.0 and the discipline
    plus the window carry the whole decision."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(db, plan, title="Easy, as long as you like")
    short_jog = _seed_activity(db, user, distance_m=2000, moving_time_s=600)

    assert completion.find_matching_session(db, short_jog) == session


def test_a_runner_with_no_plan_at_all_matches_nothing(db):
    """Free mode. Most runners are here, and nothing about the pipeline changes
    for them."""
    user = _seed_user(db)
    run = _seed_activity(db, user)

    assert completion.open_sessions_for(db, run) == []
    assert completion.find_matching_session(db, run) is None


# --- ranking: which of two plausible sessions this run WAS ------------------


def test_a_commitment_outranks_a_suggestion_even_when_it_fits_worse(db):
    """The suggestion here is the better fit on every other key — pinned to the
    day and filled exactly. It still loses, because a session the runner agreed
    to is the stronger claim on the run they actually did."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    suggestion = _seed_session(
        db,
        plan,
        start=TUE,
        end=TUE,
        commitment="suggested",
        title="Suggested, exact",
        target_distance_m=10000,
    )
    commitment = _seed_session(
        db,
        plan,
        start=MON,
        end=SUN,
        commitment="committed",
        title="Committed, loose",
        target_distance_m=20000,
    )
    run = _seed_activity(db, user, day=TUE, distance_m=10000)

    assert completion.find_matching_session(db, run) == commitment
    assert suggestion.id != commitment.id


def test_between_two_commitments_the_one_the_run_fills_most_exactly_wins(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    exact = _seed_session(
        db, plan, start=TUE, end=TUE, title="10 km", target_distance_m=10000
    )
    _seed_session(
        db, plan, start=TUE, end=TUE, title="20 km", target_distance_m=20000
    )
    run = _seed_activity(db, user, day=TUE, distance_m=10000)

    assert completion.find_matching_session(db, run) == exact


def test_between_two_equal_fits_the_narrower_window_wins(db):
    """A session the coach placed narrowly is the more specific claim on the
    day, so it is the one the run is credited to."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=MON, end=SUN, title="Anywhere this week")
    pinned = _seed_session(db, plan, start=TUE, end=TUE, title="Pinned to Tuesday")
    run = _seed_activity(db, user, day=TUE)

    assert completion.find_matching_session(db, run) == pinned


# --- one write, three sources ----------------------------------------------


def test_the_one_write_sets_every_completion_column_and_settles_the_session(db):
    """Finishing something settles it: a suggestion the runner acted on is no
    longer a suggestion they might dismiss."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(
        db,
        plan,
        commitment="suggested",
        dismissed_at=datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc),
    )
    activity = _seed_activity(db, user)

    completion.complete_planned_session(
        db, session, source=completion.MANUAL, activity=activity
    )

    db.refresh(session)
    assert session.completed_at is not None
    assert session.completion_source == completion.MANUAL
    assert session.completed_activity_id == activity.id
    assert session.dismissed_at is None


def test_a_completion_with_no_activity_behind_it_records_no_activity(db):
    """The tap and the conversation both finish a session Strava never saw."""
    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user))

    completion.complete_planned_session(db, session, source=completion.CONVERSATION)

    db.refresh(session)
    assert session.completion_source == completion.CONVERSATION
    assert session.completed_activity_id is None


def test_the_auto_path_credits_one_activity_to_exactly_one_session(db):
    """Idempotent by construction: a re-analysis or a replayed webhook changes
    nothing, and one run can never tick two sessions off."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, end=TUE, title="First")
    _seed_session(db, plan, start=MON, end=SUN, title="Second")
    run = _seed_activity(db, user, day=TUE)

    first = completion.complete_from_activity(db, run)
    second = completion.complete_from_activity(db, run)

    assert first is not None
    assert second is not None
    assert second.id == first.id
    assert first.completion_source == completion.AUTO
    assert first.completed_activity_id == run.id
    completed = (
        db.query(PlannedSession)
        .filter(PlannedSession.completed_at.isnot(None))
        .all()
    )
    assert len(completed) == 1


def test_untick_undoes_all_three_completion_columns(db):
    """The runner is allowed to be wrong about their own week."""
    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user))
    activity = _seed_activity(db, user)
    completion.complete_planned_session(
        db, session, source=completion.AUTO, activity=activity
    )

    completion.clear_completion(db, session)

    db.refresh(session)
    assert session.completed_at is None
    assert session.completion_source is None
    assert session.completed_activity_id is None


def test_a_suggestion_can_be_declined(db):
    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), commitment="suggested")

    completion.dismiss_planned_session(db, session)

    db.refresh(session)
    assert session.dismissed_at is not None


def test_a_committed_session_cannot_be_declined(db):
    """Declining something you agreed to is a plan change, and plan changes go
    through the coach rather than a button — otherwise the schedule quietly
    becomes a to-do list the runner edits, which is the thing it is not."""
    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), commitment="committed")

    with pytest.raises(ValueError):
        completion.dismiss_planned_session(db, session)

    db.refresh(session)
    assert session.dismissed_at is None


# --- the pipeline: additive, never fatal -----------------------------------


def test_a_schedule_fault_does_not_break_the_auto_completion_caller(db):
    """The schedule is an addition to the ingestion pipeline, not a dependency
    of it. A runner's run must still be ingested, analysed and reported on if the
    schedule cannot answer."""
    from app.jobs import process_new_activity as job

    user = _seed_user(db)
    activity = _seed_activity(db, user)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("the schedule is down")

    with patch.object(completion, "complete_from_activity", _boom):
        job._autocomplete_planned_session(db, activity)  # must not raise


@pytest.mark.asyncio
async def test_the_ingestion_pipeline_survives_a_schedule_fault(db):
    """The same guard through its real caller: the whole pipeline runs and the
    activity is ingested and analysed while auto-completion is broken."""
    from app.core.config import settings
    from app.jobs.process_new_activity import process_new_activity
    from app.models import DerivedMetric, StravaAccount
    from app.services.notifications import InMemoryNotifier
    from app.services.strava_ingestion import InMemoryStravaAdapter

    user = _seed_user(db)
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=12345,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()

    adapter = InMemoryStravaAdapter()
    adapter.seed_activities(
        [
            {
                "id": 9301,
                "name": "Morning easy run",
                "type": "Run",
                "start_date": "2026-08-11T08:00:00Z",
                "distance": 8200,
                "moving_time": 2400,
                "elapsed_time": 2400,
                "total_elevation_gain": 40,
                "average_heartrate": 142,
                "average_speed": 3.4,
            }
        ]
    )
    adapter.seed_streams(
        9301,
        {
            "time": {"data": [0, 60, 120, 180]},
            "heartrate": {"data": [130, 140, 145, 148]},
            "distance": {"data": [0, 200, 400, 600]},
        },
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("the schedule is down")

    fake_client = AsyncMock()
    # An unusable body: the report degrades to a fallback and no notification is
    # sent, which keeps this test about the pipeline surviving rather than about
    # the coach.
    fake_client.generate_json_with_usage = AsyncMock(return_value=("nope", None))

    with patch.object(settings, "COACH_PROMPT_ID", "coach_report_v10"), patch.object(
        completion, "complete_from_activity", _boom
    ), patch("app.services.coach.turn.AnthropicClient", return_value=fake_client):
        await process_new_activity(
            db=db,
            account=account,
            strava_activity_id=9301,
            strava_port=adapter,
            notifier=InMemoryNotifier(),
        )

    stored = db.query(Activity).filter_by(strava_activity_id=9301).one()
    assert db.query(DerivedMetric).filter_by(activity_id=stored.id).count() == 1


# --- the formerly dormant stub ---------------------------------------------
#
# `_extract_planned_workout` returned None from the beginning of the project.
# `tests/test_analysis_stages.py` covers the end-to-end reachability it now has;
# these cover the PROJECTION itself, including the shapes that must still read
# as "no plan" so every runner without prescribed reps matches as they always did.


def test_the_planned_workout_projection_reads_a_session_as_a_plan(db):
    assert _orchestrator._extract_planned_workout(None) is None
    assert _orchestrator._extract_planned_workout(PlannedSession(structure=None)) is None
    # An empty structure is not a plan — it would otherwise reach the matcher as
    # a prescription of nothing and score the run against it.
    assert _orchestrator._extract_planned_workout(PlannedSession(structure={})) is None
    assert _orchestrator._extract_planned_workout(
        PlannedSession(structure={"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60})
    ) == {"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60}


def test_analysis_survives_a_schedule_that_cannot_answer(db):
    """Analysis is a lower layer than the schedule. A schedule fault must leave a
    run analysed with no plan, not unanalysed."""
    user = _seed_user(db)
    activity = _seed_activity(db, user)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("the schedule is down")

    with patch.object(completion, "find_matching_session", _boom):
        assert _orchestrator._resolve_planned_session(db, activity) is None


def test_a_reanalysis_after_the_auto_tick_keeps_the_plan_it_was_judged_against(
    db, monkeypatch
):
    """KNOWN DEFECT, left failing deliberately (see the report for this slice).

    The pipeline analyses the run and THEN ticks the session off, so the first
    analysis sees the plan. But `find_matching_session` excludes completed
    sessions, so every LATER analysis of the same activity finds nothing — and
    re-analysis is routine: `write_checkin` re-analyses (so does the Telegram RPE
    tap), as do the intent write, the stream backfill and the bulk re-analysis.

    The `DerivedMetric` upsert writes all columns unconditionally, so the second
    pass silently overwrites the plan comparison this slice just made possible:
    `match_score` goes 0.5 -> 1.0 and the CRITICAL `interval_structure_mismatch`
    is replaced by `no_planned_workout`. The runner taps RPE on their
    notification and the run stops disagreeing with the plan it disagreed with.

    Correct behaviour is not in doubt — a re-analysis must not change the read —
    but WHERE the fix belongs is a design call (a credited-session lookup ahead
    of the open-session match, in `_resolve_planned_session` or in
    `completion.planned_structure_for`, which exists for exactly this question
    and is currently unused), so this is reported rather than patched.
    """
    from app.services.analysis._orchestrator import analyze
    from tests.test_analysis_composition import _seed_run

    detected = {
        "source": "recorded_laps",
        "summary": {
            "rep_count": 3,
            "total_work_time_s": 300,
            "work_duration_cv": 3.0,
            "consistency_score": 0.9,
        },
        "work_segments": [{"duration_s": 100, "distance_m": 400}] * 3,
    }

    activity = _seed_run(db)
    day = activity.local_start.date()
    plan = _seed_plan(db, db.get(User, activity.user_id))
    _seed_session(
        db,
        plan,
        start=day,
        end=day,
        intent="quality",
        title="12x400m",
        target_distance_m=10000,
        structure={"reps_planned": 12, "rep_distance_m": 400, "rest_s": 60},
    )

    monkeypatch.setattr(
        _orchestrator, "detect_intervals_from_laps", lambda *a, **k: detected
    )
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)
    real_classify = _orchestrator.classify_activity

    def _force_intervals(*args, **kwargs):
        classified = real_classify(*args, **kwargs)
        classified.structure = "intervals"
        return classified

    monkeypatch.setattr(_orchestrator, "classify_activity", _force_intervals)

    first = analyze(db, activity.id)
    assert "interval_structure_mismatch" in (first.confidence_reasons or [])

    completion.complete_from_activity(db, activity)
    second = analyze(db, activity.id)

    assert (second.workout_match or {}).get("match_score") == (
        first.workout_match or {}
    ).get("match_score")
    assert "interval_structure_mismatch" in (second.confidence_reasons or [])


def test_the_planned_session_resolved_for_analysis_is_the_matched_one(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(
        db,
        plan,
        intent="quality",
        title="8x400m",
        target_distance_m=10000,
        structure={"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60},
    )
    run = _seed_activity(db, user, distance_m=10000)

    resolved = _orchestrator._resolve_planned_session(db, run)

    assert resolved == session
    assert _orchestrator._extract_planned_workout(resolved) == session.structure


# --- the conversational route in -------------------------------------------


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def test_an_offer_with_no_session_named_is_rejected_by_the_validator(db):
    """The model supplies the argument; the contract decides whether there is an
    offer at all. No id, no card, nothing minted."""
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "complete_session"}
        )

    assert result["ok"] is False
    assert result["error"] == "invalid_action"
    assert frame is None
    assert fake._store == {}


def test_a_confirmed_conversation_completes_the_session_through_the_same_writer(db):
    """The gym and the turbo never reach Strava, so a session the runner mentions
    is often the only record there will be — and it lands on the same columns the
    tap and the auto-match write."""
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    session = _seed_session(
        db, _seed_plan(db, user), title="Lower body", discipline="strength",
        intent="strength",
    )
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(session.id),
            },
        )
        assert offer["ok"] is True
        assert "Lower body" in frame["description"]
        # Offering is not doing.
        db.refresh(session)
        assert session.completed_at is None

        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert result["action_type"] == "complete_session"
    db.refresh(session)
    assert session.completed_at is not None
    assert session.completion_source == completion.CONVERSATION
    assert session.completed_activity_id is None


def test_an_offer_cannot_reach_another_runners_planned_session(db):
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    other = _seed_user(db)
    theirs = _seed_session(db, _seed_plan(db, other), title="Not mine")
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(theirs.id),
            },
        )

    assert result["ok"] is False
    assert result["error"] == "not_found"
    assert frame is None
    assert fake._store == {}, "no token may be minted against another runner's data"


def test_ownership_is_re_resolved_when_the_runner_confirms(db):
    """A token is not a capability over a row. Between the offer and the tap the
    session may have stopped being theirs, and the write must ask again."""
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    other = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), title="Lower body")
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        _offer, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(session.id),
            },
        )
        session.user_id = other.id
        db.commit()

        with pytest.raises(LookupError):
            proposed_actions.consume_and_execute(db, user.id, frame["token"])

    db.refresh(session)
    assert session.completed_at is None


# --- the delete path -------------------------------------------------------


def test_deleting_an_activity_clears_only_the_tick_it_earned(db):
    """A session left ticked by an activity that no longer exists is a lie the
    plan-versus-actual read would carry forward. A manual or conversational tick
    is left alone: the runner said they did it, and deleting a Strava upload does
    not unsay that."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    activity = _seed_activity(db, user)
    when = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
    auto = _seed_session(
        db,
        plan,
        title="Auto",
        completed_at=when,
        completion_source=completion.AUTO,
        completed_activity_id=activity.id,
    )
    manual = _seed_session(
        db,
        plan,
        title="Manual",
        completed_at=when,
        completion_source=completion.MANUAL,
        completed_activity_id=activity.id,
    )
    conversational = _seed_session(
        db,
        plan,
        title="Conversation",
        completed_at=when,
        completion_source=completion.CONVERSATION,
        completed_activity_id=activity.id,
    )

    cleared = completion.clear_completions_for_activity(db, activity)

    assert cleared == 1
    for session in (auto, manual, conversational):
        db.refresh(session)
    assert auto.completed_at is None
    assert auto.completion_source is None
    assert auto.completed_activity_id is None
    assert manual.completed_at is not None
    assert manual.completion_source == completion.MANUAL
    assert manual.completed_activity_id == activity.id
    assert conversational.completed_at is not None
    assert conversational.completion_source == completion.CONVERSATION


def test_deleting_an_activity_that_ticked_nothing_clears_nothing(db):
    user = _seed_user(db)
    _seed_session(db, _seed_plan(db, user))
    activity = _seed_activity(db, user)

    assert completion.clear_completions_for_activity(db, activity) == 0
