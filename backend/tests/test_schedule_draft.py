"""#830: the coach drafts the plan — generate, validate, and store only if it survives.

The three decisions this slice rests on are the three things pinned here:

1. The model never estimates load. It says what each session IS; `effort.py`
   prices it from the runner's own history. So the stored `target_effort_score`
   must be the COMPUTED number and never anything the model returned.
2. A failed draft does not degrade, it fails. One retry with the failures fed
   back, then nothing is stored — a plan does not half-persist, because a week
   with an unsatisfiable rule is worse than no week at all.
3. Drafting spends tokens only when it is allowed to: an over-budget runner makes
   no LLM call whatsoever.

The plan lifecycle writers (`create_drafting_plan`/`activate_plan`/`fail_plan`)
are covered at the bottom, since they are what "the plan flips to active" means.

NO TEST HERE MAY REACH THE NETWORK: every generation goes through a fake client
injected at `turn.build_client`, the seam the whole coaching envelope is built on.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.activity_facts import query_facts
from app.services.schedule import draft as draft_mod
from app.services.schedule import store
from app.services.schedule.draft import draft_plan
from app.services.schedule.effort import build_load_model, estimate_effort

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 8, 10)          # a Monday
TUE = TODAY + timedelta(days=1)
WED = TODAY + timedelta(days=2)
THU = TODAY + timedelta(days=3)
SUN = TODAY + timedelta(days=6)
NEXT_MON = TODAY + timedelta(days=7)

FAKE_MODEL = "claude-fake-schedule-1"


# --- the fake generation seam ----------------------------------------------


class _FakeClient:
    """Stands in for the `MeteredClient` `turn.build_client` hands a coaching turn.

    Answers each call with the next scripted result (a dict, or an exception to
    raise), and records what it was asked so the retry feedback is observable.
    """

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.model = FAKE_MODEL

    async def generate_structured(self, *, system, user, tool, max_tokens=1024):
        self.calls.append({"system": system, "user": user, "tool": tool})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _inject(monkeypatch, client, *, over_budget: bool = False):
    """Replace the ONE seam a coaching turn gets its client from."""
    built = []

    def _build_client(kind, user_id):
        built.append((kind, user_id))
        return client

    monkeypatch.setattr(draft_mod.turn, "build_client", _build_client)
    monkeypatch.setattr(draft_mod.turn, "over_budget", lambda user_id: over_budget)
    return built


# --- synthetic history ------------------------------------------------------


def _seed_user(db) -> User:
    user = User(email=f"sched-draft-{uuid4()}@example.com")
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


def _seed_activity(
    db,
    user: User,
    *,
    day: date,
    activity_type: str = "Run",
    distance_m: float = 8000,
    moving_time_s: int = 2400,
    effort_score: float = 30.0,
) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(day.year, day.month, day.day, 9, 0),
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
    db.add(
        DerivedMetric(
            activity_id=activity.id, effort_score=effort_score, confidence="high"
        )
    )
    db.commit()
    return activity


def _seed_history(db, user: User) -> None:
    """Twelve weeks of identical training, so every rate is exact.

    Runs every other day at 30 load / 2400 s / 8 km, plus a weekly gym session at
    45 load / 3600 s, which is enough for BOTH disciplines to clear
    `MIN_SESSIONS_FOR_RATE` and for the volume norm to exist.
    """
    for offset in range(8, 92, 2):
        _seed_activity(db, user, day=TODAY - timedelta(days=offset))
    for offset in range(9, 92, 7):
        _seed_activity(
            db,
            user,
            day=TODAY - timedelta(days=offset),
            activity_type="WeightTraining",
            distance_m=0,
            moving_time_s=3600,
            effort_score=45.0,
        )


def _load_model(db, user):
    facts = query_facts(
        db, TODAY - timedelta(days=200), TODAY + timedelta(days=1), user_id=user.id
    )
    return build_load_model(facts, TODAY)


# --- the plans the fake coach returns ---------------------------------------


def _good_plan() -> dict:
    return {
        "rules": [
            {
                "kind": "rest_day_after",
                "label": "A full rest day after the long run",
                "intent": "long",
            }
        ],
        "weeks": [
            {
                "week_start": TODAY.isoformat(),
                "phase": "base",
                "sessions": [
                    {
                        "window_start": TUE.isoformat(),
                        "window_end": WED.isoformat(),
                        "intent": "easy",
                        "discipline": "run",
                        "title": "Easy hour",
                        "target_duration_s": 3600,
                    },
                    {
                        "window_start": THU.isoformat(),
                        "window_end": THU.isoformat(),
                        "intent": "quality",
                        "discipline": "run",
                        "title": "8x400m",
                        "target_distance_m": 9000,
                        "reps_planned": 8,
                        "rep_distance_m": 400,
                        "rest_s": 90,
                    },
                ],
            }
        ],
        "sketch_weeks": [],
        "summary": "Two weeks of steady base work.",
    }


# --- the happy path ---------------------------------------------------------


async def test_the_stored_load_is_computed_from_the_runners_history_not_returned_by_the_model(
    db, monkeypatch
):
    """THE decision of this slice.

    The coach chose a 60-minute easy run; the app priced it. The runner's own
    history says an hour of running costs 45 (0.0125 load/second x 3600), and
    that is what lands on the row — not 3600, not 9000, not any other number the
    model put in its answer.
    """
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([_good_plan()])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    sessions = {s.title: s for s in db.query(PlannedSession).all()}
    easy = sessions["Easy hour"]

    # The independent oracle: identical seeded sessions make the median exact.
    assert easy.target_effort_score == 45.0
    # And it is what the load model itself says, recomputed here from the same facts.
    assert easy.target_effort_score == estimate_effort(
        _load_model(db, user), "run", duration_s=3600
    )
    # It is none of the numbers the model supplied.
    model_numbers = {3600, 9000, 400, 90, 8}
    assert easy.target_effort_score not in model_numbers
    assert sessions["8x400m"].target_effort_score not in model_numbers


async def test_the_session_rows_carry_what_the_coach_actually_said(db, monkeypatch):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan()]))

    await draft_plan(db, user, plan, today=TODAY)

    sessions = {s.title: s for s in db.query(PlannedSession).all()}
    easy = sessions["Easy hour"]
    assert (easy.window_start, easy.window_end) == (TUE, WED)
    assert (easy.intent, easy.discipline, easy.commitment) == ("easy", "run", "committed")
    assert easy.target_duration_s == 3600
    assert easy.user_id == user.id and easy.plan_id == plan.id
    # No rep structure on a session that has none.
    assert easy.structure is None


async def test_the_interval_structure_lands_on_the_row_in_the_matchers_own_shape(
    db, monkeypatch
):
    """`workout_matching.match_planned_to_detected` has waited for this dict since
    the beginning; this is the first writer that produces one."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan()]))

    await draft_plan(db, user, plan, today=TODAY)

    quality = db.query(PlannedSession).filter(PlannedSession.title == "8x400m").one()
    assert quality.structure == {
        "reps_planned": 8,
        "rep_distance_m": 400,
        "rest_s": 90,
    }


async def test_an_accepted_plan_becomes_active_and_supersedes_its_predecessor(
    db, monkeypatch
):
    """One transaction: at no instant does the runner have two active plans."""
    user = _seed_user(db)
    _seed_history(db, user)
    previous = TrainingPlan(
        user_id=user.id, status="active", rules=[], week_shapes=[]
    )
    db.add(previous)
    db.commit()
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan()]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    db.expire_all()
    assert outcome.ok is True and outcome.plan_id == plan.id
    assert outcome.summary == "Two weeks of steady base work."
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one().status == (
        "active"
    )
    assert db.query(TrainingPlan).filter(
        TrainingPlan.id == previous.id
    ).one().status == "superseded"
    assert store.get_active_plan(db, user.id).id == plan.id


async def test_the_plan_records_the_model_it_actually_ran_on_and_its_rules(
    db, monkeypatch
):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan()]))

    await draft_plan(db, user, plan, today=TODAY)

    db.refresh(plan)
    assert plan.model_id == FAKE_MODEL
    assert plan.generated_at is not None
    assert [rule["kind"] for rule in plan.rules] == ["rest_day_after"]
    assert store.plan_rules(plan)[0].intent == "long"
    # The horizon ends with the last week the plan says anything about.
    assert plan.horizon_end == TODAY + timedelta(days=6)


async def test_another_runners_active_plan_is_never_superseded(db, monkeypatch):
    user = _seed_user(db)
    _seed_history(db, user)
    stranger = _seed_user(db)
    theirs = TrainingPlan(
        user_id=stranger.id, status="active", rules=[], week_shapes=[]
    )
    db.add(theirs)
    db.commit()
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan()]))

    await draft_plan(db, user, plan, today=TODAY)

    db.expire_all()
    assert db.query(TrainingPlan).filter(
        TrainingPlan.id == theirs.id
    ).one().status == "active"


# --- sketched weeks ---------------------------------------------------------


async def test_a_sketched_week_is_stored_as_shares_of_a_load_total(db, monkeypatch):
    """The model gives COUNTS; the mixes are shares of load, computed here from
    the same load model that prices concrete sessions. One number, one owner."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    answer = _good_plan()
    answer["sketch_weeks"] = [
        {
            "week_start": NEXT_MON.isoformat(),
            "phase": "build",
            "target_running_distance_m": 40000,
            "sessions_by_discipline": {"run": 4, "strength": 2},
            "intent_counts": {"easy": 3, "long": 1, "strength": 2},
        }
    ]
    _inject(monkeypatch, _FakeClient([answer]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    db.refresh(plan)
    shapes = store.plan_week_shapes(plan)
    assert [shape.week_start for shape in shapes] == [NEXT_MON]
    shape = shapes[0]

    assert shape.phase == "build"
    assert set(shape.discipline_mix) == {"run", "strength"}
    assert sum(shape.discipline_mix.values()) == pytest.approx(1.0, abs=0.001)
    assert set(shape.intent_mix) == {"easy", "long", "strength"}
    assert sum(shape.intent_mix.values()) == pytest.approx(1.0, abs=0.001)
    # Running is priced from the DISTANCE the week names (40 km at the seeded
    # per-metre median), not from a session count; the gym sessions still price
    # at 2 x the per-session median (45).
    assert shape.target_effort_score == pytest.approx(150.0 + 2 * 45.0)
    assert shape.discipline_mix["run"] == pytest.approx(150 / 240, abs=0.001)
    # No session rows for a week that was only sketched.
    assert (
        db.query(PlannedSession)
        .filter(PlannedSession.window_start >= NEXT_MON)
        .count()
        == 0
    )
    assert plan.horizon_end == NEXT_MON + timedelta(days=6)


async def test_a_sketched_weeks_load_follows_the_running_distance_it_names(db, monkeypatch):
    """A sketched week's bar tracks the running target it names.

    It did not: `_shape_for` priced every discipline from the per-SESSION median,
    passing neither a duration nor a distance, so "4 runs, 20 km" and "4 runs,
    40 km" stored the same load and drew the same horizon bar. The runner reads
    that bar as the ramp, so the one thing the horizon exists to show was the one
    thing it could not. The running share is now priced per metre.
    """
    user = _seed_user(db)
    _seed_history(db, user)

    async def _shape_for_target(distance_m: float):
        plan = store.create_drafting_plan(db, user.id)
        answer = _good_plan()
        answer["sketch_weeks"] = [
            {
                "week_start": NEXT_MON.isoformat(),
                "sessions_by_discipline": {"run": 4},
                "target_running_distance_m": distance_m,
            }
        ]
        _inject(monkeypatch, _FakeClient([answer]))
        await draft_plan(db, user, plan, today=TODAY)
        db.refresh(plan)
        return store.plan_week_shapes(plan)[0]

    modest = await _shape_for_target(20000)
    big = await _shape_for_target(40000)

    # Twice the running distance is twice the running load. Sizing a sketch by
    # session count alone made "4 runs, 20 km" and "4 runs, 40 km" draw the same
    # horizon bar, which hid the ramp the horizon exists to show.
    assert modest.target_running_distance_m != big.target_running_distance_m
    assert big.target_effort_score == pytest.approx(2 * modest.target_effort_score)
    assert modest.target_effort_score == pytest.approx(75.0)


async def test_a_sketched_week_for_a_discipline_the_runner_has_never_trained_abstains(
    db, monkeypatch
):
    """No history, no price — and therefore no share, rather than a made-up one."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    answer = _good_plan()
    answer["sketch_weeks"] = [
        {
            "week_start": NEXT_MON.isoformat(),
            "sessions_by_discipline": {"run": 4, "row": 2},
            "intent_counts": {"easy": 4},
        }
    ]
    _inject(monkeypatch, _FakeClient([answer]))

    await draft_plan(db, user, plan, today=TODAY)

    db.refresh(plan)
    shape = store.plan_week_shapes(plan)[0]
    assert set(shape.discipline_mix) == {"run"}
    assert shape.discipline_mix["run"] == pytest.approx(1.0)


# --- one retry, with the failures fed back ---------------------------------


async def test_an_off_contract_answer_is_retried_exactly_once_with_the_failure_fed_back(
    db, monkeypatch
):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([{"weeks": "not a list of weeks"}, _good_plan()])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    assert len(client.calls) == 2
    first, second = client.calls
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" not in first["user"]
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" in second["user"]
    assert "not the shape the tool requires" in second["user"]
    assert "Write the plan again" in second["user"]


async def test_a_plan_rejected_by_the_validator_is_retried_and_nothing_partial_is_written(
    db, monkeypatch
):
    """Attempt one wrote a week in the past. It must leave no rows behind, and the
    rejection must reach the coach in words it can act on."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    stale = _good_plan()
    stale["weeks"][0]["week_start"] = (TODAY - timedelta(days=7)).isoformat()
    stale["weeks"][0]["sessions"][0]["title"] = "From the rejected attempt"
    stale["weeks"][0]["sessions"][1]["title"] = "Also rejected"
    client = _FakeClient([stale, _good_plan()])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    assert len(client.calls) == 2
    assert "is in the past" in client.calls[1]["user"]
    titles = {s.title for s in db.query(PlannedSession).all()}
    assert titles == {"Easy hour", "8x400m"}


async def test_a_transport_failure_is_retried_without_reaching_the_prompt(
    db, monkeypatch
):
    """A blip is retried, but its text is not fed back to the coach.

    The provider's exception is not something the coach can act on, so putting it
    in the next prompt would be provider internals crossing into model input for
    no gain. The retry simply asks again with the same message.
    """
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([RuntimeError("upstream blew up"), _good_plan()])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    assert len(client.calls) == 2
    assert "upstream blew up" not in client.calls[1]["user"]
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" not in client.calls[1]["user"]


async def test_a_transport_blip_does_not_spend_the_coachs_rewrite(db, monkeypatch):
    """The two budgets are separate.

    Sharing them meant a 429 on the first call left a genuinely fixable plan with
    no attempt remaining: one blip, then one rejected plan, and the draft was over
    even though the coach had never been asked to fix anything.
    """
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    rejected = _good_plan()
    rejected["weeks"][0]["week_start"] = "2020-01-06"  # in the past
    client = _FakeClient([RuntimeError("429"), rejected, _good_plan()])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    assert len(client.calls) == 3
    # The rewrite carried the rejection; the blip carried nothing.
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" in client.calls[2]["user"]


# --- two failures: fail visibly, store nothing ------------------------------


async def test_two_failures_leave_no_plan_at_all_because_a_plan_cannot_half_persist(
    db, monkeypatch
):
    """A report can degrade to prose without its tail; a plan cannot degrade. A
    week whose rules cannot be satisfied is worse than no week, because the runner
    would act on it."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    unarrangeable = _good_plan()
    unarrangeable["rules"] = [
        {
            "kind": "no_intent_day_before",
            "label": "No quality run the day before the long run",
            "before_intent": "quality",
            "target_intent": "long",
        }
    ]
    unarrangeable["weeks"][0]["sessions"] = [
        {
            "window_start": SUN.isoformat(),
            "window_end": SUN.isoformat(),
            "intent": "long",
            "discipline": "run",
            "title": "Long run",
            "target_distance_m": 20000,
        },
        {
            "window_start": (SUN - timedelta(days=1)).isoformat(),
            "window_end": (SUN - timedelta(days=1)).isoformat(),
            "intent": "quality",
            "discipline": "run",
            "title": "Intervals",
            "target_distance_m": 9000,
        },
    ]
    client = _FakeClient([unarrangeable, unarrangeable])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is False
    assert len(client.calls) == 2
    assert any("cannot satisfy its own rule" in f for f in outcome.failures)
    assert db.query(PlannedSession).count() == 0
    db.refresh(plan)
    assert plan.status == store.DRAFTING  # the JOB is what marks it failed
    assert plan.week_shapes == []


async def test_a_repeatedly_off_contract_answer_stores_nothing_either(db, monkeypatch):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([{"weeks": [{"nonsense": 1}]}, {"weeks": [{"nonsense": 2}]}])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is False
    assert db.query(PlannedSession).count() == 0
    assert db.query(TrainingPlan).filter(
        TrainingPlan.status == store.ACTIVE
    ).count() == 0


# --- the spend gate ---------------------------------------------------------


async def test_an_over_budget_runner_costs_nothing_because_no_call_is_made(
    db, monkeypatch
):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([_good_plan()])
    built = _inject(monkeypatch, client, over_budget=True)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is False
    assert outcome.failures == ["over the spend cap for this period"]
    assert client.calls == []
    assert built == []
    assert db.query(PlannedSession).count() == 0


async def test_the_turn_is_built_on_the_schedule_lane_for_the_runner_paying_for_it(
    db, monkeypatch
):
    """Metering is a property of the client, so which lane and whose id it is
    built with is what decides that the spend is recorded against this runner."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    built = _inject(monkeypatch, _FakeClient([_good_plan()]))

    await draft_plan(db, user, plan, today=TODAY)

    assert built == [(draft_mod.turn.TurnKind.SCHEDULE, user.id)]


# --- what the coach is asked ------------------------------------------------


async def test_the_context_carries_the_runner_and_never_asks_for_a_load_number(
    db, monkeypatch
):
    user = _seed_user(db)
    _seed_history(db, user)
    store.create_goal_race(
        db,
        user.id,
        name="Autumn half",
        race_date=TODAY + timedelta(days=70),
        distance_m=21097.5,
        priority="A",
    )
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([_good_plan()])
    _inject(monkeypatch, client)

    await draft_plan(db, user, plan, today=TODAY)

    context = client.calls[0]["user"]
    system = client.calls[0]["system"]
    assert f"TODAY: {TODAY.isoformat()}" in context
    # From TODAY, not the week start. Asking for the whole current week when it
    # is already Wednesday makes the coach prescribe Monday sessions the runner
    # cannot do, which the validator then rejects as being in the past — the two
    # instructions contradicted each other and cost a live draft.
    assert f"PLAN FROM: today, {TODAY.isoformat()}" in context
    assert "plan only the days that remain in it" in context
    assert "Autumn half" in context
    assert "run x" in context  # the disciplines they actually train
    assert "Do not estimate training load" in system
    assert "Coach the runner in front of you, not the median one" in system


async def test_a_runner_with_no_history_is_told_to_plan_conservatively(db, monkeypatch):
    """Nothing is invented for them: the context says the history is thin rather
    than filling the gap with a typical runner."""
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)
    client = _FakeClient([_good_plan()])
    _inject(monkeypatch, client)

    outcome = await draft_plan(db, user, plan, today=TODAY)

    context = client.calls[0]["user"]
    assert "Not enough history to establish what is typical" in context
    assert "No race stated" in context
    assert outcome.ok is True
    # With no history the load model abstains, so the sessions carry no price.
    assert {s.target_effort_score for s in db.query(PlannedSession).all()} == {None}


async def test_another_runners_history_never_prices_this_runners_plan(db, monkeypatch):
    user = _seed_user(db)
    stranger = _seed_user(db)
    _seed_history(db, stranger)
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan()]))

    await draft_plan(db, user, plan, today=TODAY)

    assert {s.target_effort_score for s in db.query(PlannedSession).all()} == {None}


# --- the plan lifecycle writers --------------------------------------------


async def test_a_drafting_plan_is_invisible_to_the_week_but_visible_to_the_poll(db):
    """The week keeps serving the previous plan (or free mode) while a new one is
    written: a runner asking for a new plan never loses the one they are on.

    `created_at` is set explicitly on the predecessor because `latest_plan` orders
    on that column alone and it defaults to the database's own `now()`, which on
    the SQLite test database has one-second resolution — two plans made in the
    same second tie, and the tie is broken arbitrarily.
    """
    user = _seed_user(db)
    active = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=[],
        created_at=datetime(2026, 8, 1, 9, 0),
    )
    db.add(active)
    db.commit()

    drafting = store.create_drafting_plan(db, user.id)

    assert drafting.status == store.DRAFTING
    assert drafting.source == "coach"
    assert store.get_active_plan(db, user.id).id == active.id
    assert store.latest_plan(db, user.id).id == drafting.id


async def test_activate_supersedes_only_the_callers_own_previously_active_plan(db):
    user = _seed_user(db)
    stranger = _seed_user(db)
    mine = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    theirs = TrainingPlan(
        user_id=stranger.id, status="active", rules=[], week_shapes=[]
    )
    db.add_all([mine, theirs])
    db.commit()
    successor = store.create_drafting_plan(db, user.id)

    store.activate_plan(db, successor)

    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == mine.id).one().status == (
        "superseded"
    )
    assert db.query(TrainingPlan).filter(TrainingPlan.id == theirs.id).one().status == (
        "active"
    )
    assert successor.status == store.ACTIVE
    assert successor.generated_at is not None
    assert store.get_active_plan(db, stranger.id).id == theirs.id


async def test_activating_a_plan_twice_does_not_supersede_itself(db):
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)

    store.activate_plan(db, plan)
    store.activate_plan(db, plan)

    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one().status == (
        "active"
    )


async def test_failing_a_draft_is_visible_and_never_raises(db):
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)

    store.fail_plan(db, plan, "week 2026-08-17 cannot satisfy its own rule 'x'")

    db.expire_all()
    stored = db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one()
    assert stored.status == store.FAILED
    assert store.get_active_plan(db, user.id) is None
    # The internal reason is LOGGED, never stored on the row the runner reads.
    assert "cannot satisfy" not in str(stored.__dict__)


async def test_the_prompt_states_the_constraints_the_validator_enforces():
    """Every rule the gate rejects on must be one the coach was told about.

    Each of these was enforced silently before a live draft failed on it: the
    coach wrote a Sunday-to-Monday window and a "rest / easy walk" with a
    duration on it, and both were rejected by rules it had never been given. A
    validator that polices an unstated rule is not a gate, it is a trap.
    """
    from app.services.schedule.draft import _SYSTEM_PROMPT

    assert "must stay INSIDE one week" in _SYSTEM_PROMPT
    assert "A rest day is REST" in _SYSTEM_PROMPT
    assert "a distance, a duration, or rep structure" in _SYSTEM_PROMPT
