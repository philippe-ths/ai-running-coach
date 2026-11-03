"""#830: marking a planned session done — three ways in, one write.

The matcher is the interesting half. Strava never sees the gym and a 2 km jog is
not the 18 km long run it happened to fall inside the window of, so auto-matching
only fires when it is CONFIDENT. This file pins that conservatism, the ranking
that decides between two plausible sessions, the single writer every route ends
at, the idempotency that stops one activity ticking two sessions, and the delete
path's refusal to unsay something the runner said.

The pipeline and the conversational route in have their own files:
`test_schedule_completion_pipeline.py`, `test_schedule_completion_conversation.py`.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.planned_session import PlannedSession
from app.models.week_recommendation import WeekRecommendation
from app.services.schedule import completion
from tests._schedule_completion_fixtures import (
    MON,
    SUN,
    TUE,
    WED,
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)


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


@pytest.mark.parametrize("shown_first", [True, False])
def test_between_identical_sessions_the_one_shown_for_the_day_wins(db, shown_first):
    """#1155: two identical floating walks, one recommended for Tuesday and one
    for Thursday. A walk done on Tuesday ticks Tuesday's, or Tuesday keeps a
    second walk the runner has already done. Seeded in both orders, since a tie
    otherwise falls to whichever row the query returns first."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    first = _seed_session(db, plan, start=MON, end=SUN, discipline="walk", title="Brisk walk")
    second = _seed_session(db, plan, start=MON, end=SUN, discipline="walk", title="Brisk walk")
    today, later = (first, second) if shown_first else (second, first)
    db.add(
        WeekRecommendation(
            user_id=user.id,
            week_start=MON,
            plan_id=plan.id,
            assignment={str(today.id): TUE.isoformat(), str(later.id): (TUE + timedelta(days=2)).isoformat()},
            changes=[],
        )
    )
    db.commit()
    walk = _seed_activity(db, user, day=TUE, activity_type="Walk", distance_m=5000)

    assert completion.find_matching_session(db, walk) == today


def test_a_recommendation_from_another_plan_is_not_read(db):
    """A stored week from a replaced plan does not steer the tick, and reading
    it does not rewrite it."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    a = _seed_session(db, plan, start=MON, end=SUN, discipline="walk", title="Brisk walk")
    b = _seed_session(db, plan, start=MON, end=SUN, discipline="walk", title="Brisk walk")
    walk = _seed_activity(db, user, day=TUE, activity_type="Walk", distance_m=5000)
    unsteered = completion.find_matching_session(db, walk)
    other = b if unsteered == a else a
    stale = {str(other.id): TUE.isoformat()}
    db.add(
        WeekRecommendation(
            user_id=user.id, week_start=MON, plan_id=uuid4(), assignment=dict(stale), changes=[]
        )
    )
    db.commit()

    assert completion.complete_from_activity(db, walk) == unsteered
    row = db.query(WeekRecommendation).filter(WeekRecommendation.user_id == user.id).one()
    db.refresh(row)
    assert row.assignment == stale


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
