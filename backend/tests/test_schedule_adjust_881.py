"""Correcting ONE planned session (#881).

The defect was observed in normal use. A runner's interval session read 2.5 km
when it should have read 4.5, and the coach — correctly — said it had no way to
change it. Its only available suggestion was to redraft the whole seven-week
block, which supersedes the plan the runner is training to and which failed
anyway. It ended by telling them to run the session as agreed and ignore the app.

Three things are load-bearing here and each has its own tests.

**The write is narrow.** A correction changes how far or how long, and nothing
else. Placement is what the spacing rules constrain; intent and discipline make
it a different session. Keeping it to one axis is what makes a write reachable
from a conversation safe: the widest thing it can do is restate a number the
runner is already looking at.

**The load is repriced, never carried over.** `target_effort_score` was priced
from the ORIGINAL prescription, so a corrected distance with the old load
attached puts a number nothing produced into the runner's week.

**Refusals happen before the card, not after it.** A runner who taps and is then
told no has already agreed to something that was never going to happen.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.coach import proposed_actions
from app.services.schedule.adjust import (
    AdjustRefused,
    adjust_planned_session,
    describe_adjustment,
)

# RELATIVE to the real today, not a fixed calendar date. A correction is refused
# on a session whose window has passed, and the offer path resolves "passed"
# against `date.today()` rather than any date a test can inject — so a session
# pinned to a fixed tomorrow is a future session until that date arrives and a
# past one every day after. These tests were written on 2026-08-12, were green
# that day and the next, and failed on the third: the suite rots at midnight
# rather than on a change, which is the worst way for it to fail.
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
_NOON = datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _user(db) -> User:
    user = User(email=f"adj-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id, goal_type="half", experience_level="intermediate",
            weekly_days_available=5, max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _history(db, user: User) -> None:
    """Enough running for the load model to have a rate to price against."""
    for offset in range(2, 60, 2):
        activity = Activity(
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            start_date=_NOON - timedelta(days=offset),
            type="Run", name="Run", distance_m=10000, moving_time_s=3000,
            elapsed_time_s=3000, elev_gain_m=0.0, raw_summary={},
        )
        db.add(activity)
        db.commit()
        db.add(
            DerivedMetric(
                activity_id=activity.id, effort_score=50.0, confidence="high"
            )
        )
        db.commit()


def _plan(db, user: User, *, status: str = "active") -> TrainingPlan:
    plan = TrainingPlan(user_id=user.id, status=status, rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _session(db, plan, **kw) -> PlannedSession:
    payload = {
        "plan_id": plan.id,
        "user_id": plan.user_id,
        "window_start": TOMORROW,
        "window_end": TOMORROW,
        "intent": "quality",
        "discipline": "run",
        "commitment": "committed",
        "title": "Intervals: 6x400m",
        "target_distance_m": None,
        "target_effort_score": 89.3,
        "structure": {"reps_planned": 6, "rep_distance_m": 400, "rest_s": 90},
    }
    payload.update(kw)
    session = PlannedSession(**payload)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# --- the correction itself ---------------------------------------------------


def test_the_runners_own_case_one_number_without_redrafting_the_block(db):
    """2.4 km stored, 4.5 km agreed. The correction is one session, and the plan
    it belongs to is untouched."""
    user = _user(db)
    _history(db, user)
    plan = _plan(db, user)
    session = _session(db, plan)

    adjust_planned_session(db, session, distance_m=4500, today=TODAY)

    assert session.target_distance_m == 4500
    assert plan.status == "active"
    assert db.query(TrainingPlan).count() == 1
    # The prescription survives the correction: it is what they go out and do.
    assert session.structure == {"reps_planned": 6, "rep_distance_m": 400, "rest_s": 90}


def test_the_load_is_repriced_from_the_runners_history_not_left_stale(db):
    """`target_effort_score` was priced from the ORIGINAL prescription. Carried
    over, a corrected session would sit in the week's mix bar at a cost nothing
    produced — and the runner never states load, because a bare load figure reads
    as an intensity verdict (the north star's hard case)."""
    user = _user(db)
    _history(db, user)
    session = _session(db, _plan(db, user), target_effort_score=89.3)

    adjust_planned_session(db, session, distance_m=20000, today=TODAY)

    assert session.target_effort_score != 89.3
    assert session.target_effort_score > 89.3  # twenty km costs more than four


def test_a_discipline_with_no_history_abstains_rather_than_inventing_a_load(db):
    """The draft abstains where it has no rate; so does a correction. A load
    number invented for a discipline the runner has never trained is worse than
    none."""
    user = _user(db)
    session = _session(
        db, _plan(db, user), discipline="row", intent="easy",
        title="Erg", structure=None, target_distance_m=2000,
    )

    adjust_planned_session(db, session, distance_m=5000, today=TODAY)

    assert session.target_effort_score is None


def test_correcting_to_a_duration_clears_the_distance_and_the_reverse(db):
    """One prescription per session. Leaving the old axis behind would give the
    session two answers to "how big is this", which is where #876 started."""
    user = _user(db)
    _history(db, user)
    session = _session(db, _plan(db, user), target_distance_m=8000, structure=None)

    adjust_planned_session(db, session, duration_s=2400, today=TODAY)
    assert (session.target_distance_m, session.target_duration_s) == (None, 2400)

    adjust_planned_session(db, session, distance_m=8000, today=TODAY)
    assert (session.target_distance_m, session.target_duration_s) == (8000, None)


# --- what it refuses ---------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, because",
    [
        ({}, "neither"),
        ({"distance_m": 5000, "duration_s": 2400}, "both"),
        ({"distance_m": 0}, "zero distance"),
        ({"distance_m": -5000}, "negative distance"),
        ({"distance_m": 200_001}, "past the ceiling"),
        ({"duration_s": 0}, "zero duration"),
        ({"duration_s": -60}, "negative duration"),
        ({"duration_s": 86_401}, "past the ceiling"),
    ],
)
def test_a_correction_that_is_not_a_prescription_is_refused(db, kwargs, because):
    """The edges, where validators are usually wrong. A session sized at zero is
    a deletion wearing a correction's clothes, and a negative one is nothing."""
    user = _user(db)
    session = _session(db, _plan(db, user))

    with pytest.raises(AdjustRefused):
        adjust_planned_session(db, session, today=TODAY, **kwargs)

    db.refresh(session)
    assert session.target_distance_m is None


def test_a_rest_day_cannot_be_given_a_target_this_way(db):
    """The drafting gate refuses a rest day with a target on it outright. A
    correction must not become the way around a rule the front door enforces."""
    user = _user(db)
    session = _session(db, _plan(db, user), intent="rest", title="Rest", structure=None)

    with pytest.raises(AdjustRefused, match="rest day"):
        adjust_planned_session(db, session, distance_m=5000, today=TODAY)


def test_a_finished_session_cannot_have_its_prescription_rewritten(db):
    """Changing what a DONE session asked for rewrites the runner's record of
    what they agreed to, after they have already done it."""
    user = _user(db)
    session = _session(
        db, _plan(db, user), completed_at=datetime.now(timezone.utc)
    )

    with pytest.raises(AdjustRefused, match="already done"):
        adjust_planned_session(db, session, distance_m=4500, today=TODAY)


def test_a_session_that_has_passed_cannot_be_corrected(db):
    """History. Nothing reads its planned distance any more — the same reason the
    #876 backfill declines to touch one."""
    user = _user(db)
    session = _session(
        db, _plan(db, user),
        window_start=TODAY - timedelta(days=3), window_end=TODAY - timedelta(days=3),
    )

    with pytest.raises(AdjustRefused, match="already passed"):
        adjust_planned_session(db, session, distance_m=4500, today=TODAY)


def test_a_superseded_plans_session_cannot_be_corrected(db):
    """Not the week the runner is training to, so the correction would change
    nothing they can see and would silently edit a replaced plan."""
    user = _user(db)
    session = _session(db, _plan(db, user, status="superseded"))

    with pytest.raises(AdjustRefused, match="current plan"):
        adjust_planned_session(db, session, distance_m=4500, today=TODAY)


# --- the card ----------------------------------------------------------------


def test_the_card_names_the_old_value_as_well_as_the_new(db):
    """This action does not undo with a tap, so the card is where the previous
    prescription is written down. A runner who wants it back reads it off their
    own transcript and asks — stating only the new value would make the change
    unrecoverable in exactly the way a confirm step exists to prevent."""
    user = _user(db)
    session = _session(
        db, _plan(db, user),
        structure={
            "reps_planned": 6, "rep_distance_m": 400,
            "warmup_distance_m": 1050, "cooldown_distance_m": 1050,
        },
    )

    described = describe_adjustment(session, distance_m=6000, today=TODAY)

    assert described == "Change “Intervals: 6x400m” from 4.50 km to 6.00 km"


def test_a_session_with_no_stated_distance_says_so_on_the_card(db):
    """"from no stated distance to 4.50 km" is the honest line. "from 0.00 km"
    would read as a session that asked for nothing."""
    user = _user(db)
    session = _session(db, _plan(db, user), structure={"reps_planned": 4, "rest_s": 90})

    described = describe_adjustment(session, distance_m=4500, today=TODAY)

    assert "from no stated distance to 4.50 km" in described


def test_a_refusal_happens_before_the_card_goes_up(db):
    """A card the runner taps only to be told no is worse than no card: they have
    already agreed to something that was never going to happen."""
    user = _user(db)
    session = _session(
        db, _plan(db, user), completed_at=datetime.now(timezone.utc)
    )

    with pytest.raises(AdjustRefused):
        describe_adjustment(session, distance_m=4500, today=TODAY)


# --- what an adversarial pass found -----------------------------------------
#
# Every test below is a hole an agent that did not write this change went looking
# for and found. The worst of them made the feature unreachable in production
# while its own suite was green.


def test_the_coach_can_actually_name_a_session_to_act_on(db):
    """The one that made all of this dead on arrival.

    Nothing the coach is shown carried a session id: the upcoming list, the
    planned-for section, the screen pointer and the query tools all describe
    sessions by title and never identify them. The tool says "use only an id
    already in front of you", so an obedient model could never offer this action
    at all, and a disobedient one invents an id that resolves to nothing.

    `complete_session` (#830) shipped with the same gap, so this unblocks both.
    """
    from app.services.schedule.coach_view import build_schedule_context
    from app.services.weeks import week_start

    user = _user(db)
    plan = _plan(db, user)
    # The window runs from today to the end of today's week: the only shape that
    # is BOTH still upcoming and inside the week this section reads, whatever day
    # the suite is run on. A session pinned to "tomorrow" is neither on the last
    # day of a week, when tomorrow belongs to the next one.
    week_end = week_start(TODAY, 0) + timedelta(days=6)
    session = _session(db, plan, window_start=TODAY, window_end=week_end)

    def _activity(kind: str) -> Activity:
        row = Activity(
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            start_date=_NOON,
            type=kind, name=kind, distance_m=9000, moving_time_s=2700,
            elapsed_time_s=2700, elev_gain_m=0.0, raw_summary={},
        )
        db.add(row)
        db.commit()
        return row

    # A ride cannot be the run session, so the session stays in the upcoming list.
    upcoming = build_schedule_context(db, _activity("Ride"))
    assert str(session.id) in [i.session_id for i in upcoming.still_to_come_this_week]

    # A run on the same day IS matched to it, which moves the session out of the
    # upcoming list and into `planned_for_this_activity`. The id has to be
    # reachable there too, or the coach can act on every session except the one
    # the report it is writing is about.
    matched = build_schedule_context(db, _activity("Run"))
    assert matched.planned_for_this_activity.session_id == str(session.id)


def test_the_schedule_kill_switch_closes_this_write_path_too(db, monkeypatch):
    """`SCHEDULE_ENABLED` off answers every schedule route with 503 and hides the
    tab. It is the fast lever when the schedule misbehaves, so a write into
    schedule rows that stays open through it is not a kill switch — its sibling
    `draft_plan` already refuses for exactly this reason."""
    from app.core.config import settings

    user = _user(db)
    session = _session(db, _plan(db, user))
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(session.id),
                "target_distance_m": 4500,
            },
        )

    assert result["ok"] is False
    assert frame is None
    db.refresh(session)
    assert session.target_distance_m is None


def test_a_declined_suggestion_cannot_be_corrected(db):
    """A dismissed suggestion is dropped from the week read entirely, so
    correcting it changes nothing the runner can see — the same reasoning that
    refuses a superseded plan's session, applied consistently."""
    user = _user(db)
    session = _session(
        db, _plan(db, user), commitment="suggested",
        dismissed_at=datetime.now(timezone.utc),
    )

    with pytest.raises(AdjustRefused, match="declined"):
        adjust_planned_session(db, session, distance_m=4500, today=TODAY)


def test_the_card_names_the_whole_prescription_it_is_replacing(db):
    """A session may carry a distance AND a duration — "16 km, cap at 2h" is a
    real prescription the drafting contract permits. Correcting the distance
    clears the cap, and a card describing only the distance would announce one
    change while silently making another. The card is the undo record; a record
    that omits half of what it replaced is not one."""
    user = _user(db)
    _history(db, user)
    session = _session(
        db, _plan(db, user), title="Long run", structure=None,
        target_distance_m=16000, target_duration_s=7200,
    )

    described = describe_adjustment(session, distance_m=18000, today=TODAY)

    assert described == "Change “Long run” from 16.00 km and 120 min to 18.00 km"


def test_the_card_names_a_duration_alongside_the_distance_it_derives(db):
    """A session prescribed in MINUTES that also carries rep distances would have
    been described by its rep distance alone, because that is what "how far does
    this go" answers. A runner reading the transcript to put it back would have
    asked for a number the session was never prescribed in. Both are named, so
    neither is the whole story and neither is lost."""
    user = _user(db)
    session = _session(
        db, _plan(db, user), target_distance_m=None, target_duration_s=2400,
        structure={"reps_planned": 6, "rep_distance_m": 400, "rest_s": 90},
    )

    described = describe_adjustment(session, distance_m=5000, today=TODAY)

    assert "from 2.40 km and 40 min to 5.00 km" in described


def test_a_rep_session_cannot_be_turned_into_a_timed_one(db):
    """Its rep structure would say one thing and its duration another, and every
    reader picks a different one — the week's headline would go on counting the
    reps' distance while the card said minutes. Changing what a session IS is a
    draft, not a correction."""
    user = _user(db)
    session = _session(db, _plan(db, user))  # carries 6 x 400 m

    with pytest.raises(AdjustRefused, match="built out of reps"):
        adjust_planned_session(db, session, duration_s=2400, today=TODAY)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"distance_m": 0.5},
        {"distance_m": 1e-9},
        {"distance_m": 99},
        {"distance_m": True},  # Python's True is 1
        {"duration_s": 1},
        {"duration_s": 59},
    ],
)
def test_a_correction_below_the_floor_is_refused(db, kwargs):
    """"Greater than zero" is not a floor. Half a metre is a deletion wearing a
    correction's clothes just as much as zero is, and the card renders it as
    "0.00 km" — the very value the code refuses. The realistic route here is a
    unit slip: "make it 4.5" meaning kilometres."""
    user = _user(db)
    session = _session(db, _plan(db, user))

    with pytest.raises(AdjustRefused):
        adjust_planned_session(db, session, today=TODAY, **kwargs)

    db.refresh(session)
    assert session.target_distance_m is None


# --- the model-facing boundary ----------------------------------------------


def test_another_runners_session_cannot_be_corrected_through_the_offer(db):
    """The threat: a model-supplied id reaching a session that is not the acting
    runner's. Ownership is server-held, and a cross-tenant id is indistinguishable
    from one that does not exist."""
    mine = _user(db)
    stranger = _user(db)
    theirs = _session(db, _plan(db, stranger))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, mine.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(theirs.id),
                "target_distance_m": 4500,
            },
        )

    assert result["ok"] is False
    assert frame is None
    db.refresh(theirs)
    assert theirs.target_distance_m is None


def test_another_runners_session_cannot_be_corrected_at_confirm_time_either(db):
    """Ownership is re-resolved when the token is spent, not trusted from the
    mint. A token is a bearer credential for half an hour."""
    mine = _user(db)
    stranger = _user(db)
    _history(db, mine)
    theirs = _session(db, _plan(db, stranger))
    mine_session = _session(db, _plan(db, mine))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()) as _r:
        _result, frame = proposed_actions.mint_proposed_action(
            db, mine.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(mine_session.id),
                "target_distance_m": 4500,
            },
        )
        # The stranger cannot spend the token minted for this runner.
        with pytest.raises(LookupError):
            proposed_actions.consume_and_execute(db, stranger.id, frame["token"])

    db.refresh(mine_session)
    db.refresh(theirs)
    assert mine_session.target_distance_m is None
    assert theirs.target_distance_m is None


def test_a_correction_offered_without_a_session_is_refused(db):
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, _user(db).id,
            {"action_type": "adjust_session", "target_distance_m": 4500},
        )

    assert result["ok"] is False
    assert frame is None


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"target_distance_m": 4500, "target_duration_s": 2400},
        {"target_distance_m": 0},
        {"target_distance_m": -1},
        {"target_distance_m": 200_001},
        {"target_duration_s": 86_401},
    ],
)
def test_an_off_shape_correction_never_reaches_a_card(db, extra):
    """Bounded at the offer as well as in the writer, so an absurd value is never
    something the runner can be shown and tap."""
    user = _user(db)
    session = _session(db, _plan(db, user))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(session.id),
                **extra,
            },
        )

    assert result["ok"] is False
    assert frame is None


@pytest.mark.parametrize(
    "payload",
    [
        {"target_distance_m": 0},
        {"target_distance_m": -1},
        {"target_distance_m": 200_001},
        {"target_duration_s": 0},
        {"target_duration_s": -1},
        {"target_duration_s": 86_401},
        {},  # neither
        {"target_distance_m": 4500, "target_duration_s": 2400},  # both
    ],
)
def test_the_model_facing_contract_refuses_an_off_shape_correction(payload):
    """Asserted at the CONTRACT, not through the mint.

    Going through `mint_proposed_action` these all fail anyway, because the
    writer's own refusals run while the card text is built — so a test at that
    level passes whether or not this contract bounds anything, which is a guard
    that proves nothing and rots quietly. `ProposedActionRequest` is the boundary
    the model's arguments cross, and it is bounded there for the same reason
    `rpe` is.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        proposed_actions.ProposedActionRequest(
            action_type="adjust_session",
            planned_session_id=uuid.uuid4(),
            **payload,
        )


def test_the_model_facing_contract_accepts_a_well_formed_correction():
    """The positive control for the test above: without it, a contract that
    refused everything would look identical to one that refuses the right things."""
    request = proposed_actions.ProposedActionRequest(
        action_type="adjust_session",
        planned_session_id=uuid.uuid4(),
        target_distance_m=4500,
    )

    assert request.target_distance_m == 4500
    assert request.target_duration_s is None


def test_a_corrected_target_cannot_ride_along_on_another_action(db):
    """It would be accepted and silently dropped — an instruction the coach wrote
    and nothing stored, which is the shape #878 was raised for."""
    user = _user(db)
    session = _session(db, _plan(db, user))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(session.id),
                "target_distance_m": 4500,
            },
        )

    assert result["ok"] is False
    assert frame is None


def test_the_confirmed_correction_writes_and_the_token_is_spent(db):
    """End to end through the runner's tap: offer, confirm, one write, and the
    token cannot be used twice."""
    user = _user(db)
    _history(db, user)
    session = _session(db, _plan(db, user))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(session.id),
                "target_distance_m": 4500,
            },
        )
        outcome = proposed_actions.consume_and_execute(db, user.id, frame["token"])
        with pytest.raises(LookupError):
            proposed_actions.consume_and_execute(db, user.id, frame["token"])

    db.refresh(session)
    assert outcome["action_type"] == "adjust_session"
    assert session.target_distance_m == 4500


def test_nothing_is_written_until_the_runner_confirms(db):
    """Offering is not doing, for this action as for every other."""
    user = _user(db)
    session = _session(db, _plan(db, user))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, _frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(session.id),
                "target_distance_m": 4500,
            },
        )

    assert result["ok"] is True
    db.refresh(session)
    assert session.target_distance_m is None


def test_a_session_finished_between_the_card_and_the_tap_is_still_refused(db):
    """The token lives half an hour. Every refusal is re-asked at write time, not
    trusted from when the card went up."""
    user = _user(db)
    _history(db, user)
    session = _session(db, _plan(db, user))

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(session.id),
                "target_distance_m": 4500,
            },
        )
        session.completed_at = datetime.now(timezone.utc)
        db.commit()
        with pytest.raises(AdjustRefused):
            proposed_actions.consume_and_execute(db, user.id, frame["token"])

    db.refresh(session)
    assert session.target_distance_m is None
