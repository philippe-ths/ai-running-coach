"""#1043: a plan reaches the runner's main goal, months away, at falling resolution.

A plan could not see past the 12-week horizon, so a marathon seven months out was
a sentence in the prompt rather than a shape the plan held, and only the target
race's week was exempt from the volume ceiling, so a booked B race on the way
counted as training.

Pinned here: the plan's reach (to the target goal, bounded), the three
resolutions the drafter is told about and the gate enforces (concrete, sketched,
outline blocks), every dated goal's week treated as a race week, the far blocks
stored and read back as blocks by the screen and the coach, and the draft and
amendment paths actually handing the races to the gate.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.models.goal_race import GoalRace
from app.models.training_plan import TrainingPlan
from app.services.coach import query_tools
from app.services.schedule import amend, store
from app.services.schedule.draft import (
    MAX_REACH_WEEKS,
    build_draft_context,
    draft_plan,
    plan_reach_weeks,
)
from app.services.schedule.draft_contract import (
    QUALITY_FOCUS_MAX_LENGTH,
    RECORD_TRAINING_PLAN_TOOL,
    DraftedPlan,
    OutlineBlock,
    SketchedWeek,
)
from app.services.schedule.horizon import MAX_HORIZON_WEEKS, build_horizon
from app.services.schedule.plan_validator import VOLUME_CEILING, validate_drafted_plan
from app.services.weeks import MONDAY
from tests.test_schedule_draft import (
    TODAY,
    _FakeClient,
    _inject,
    _seed_history,
    _seed_user,
)

# TODAY is Monday 10 Aug 2026. The goals below sit 5, 23 and 30 weeks out, the
# shape the issue's acceptance criterion names.
WEEK = timedelta(days=7)
TUE, THU, SUN = (TODAY + timedelta(days=d) for d in (1, 3, 6))
NEXT_MON = TODAY + WEEK
TEN_K = TODAY + 5 * WEEK - timedelta(days=1)            # Sun, week 5
HALF = TODAY + 23 * WEEK - timedelta(days=1)            # Sun, week 23
MARATHON_FROM = TODAY + 30 * WEEK                        # window start, week 31
MARATHON_TO = MARATHON_FROM + 8 * WEEK


def _goal(db, user, name, **fields) -> GoalRace:
    goal = GoalRace(user_id=user.id, name=name, **fields)
    db.add(goal)
    db.commit()
    return goal


def _season(db, user) -> None:
    _goal(db, user, "Town 10k", priority="B", race_date=TEN_K, distance_m=10000, booked=True)
    _goal(db, user, "Spring Half", priority="B", race_date=HALF, distance_m=21097, booked=True)
    _goal(db, user, "First marathon", priority="A", window_start=MARATHON_FROM,
          window_end=MARATHON_TO, distance_m=42195)
    _goal(db, user, "Backyard ultra", priority="C")


def _run(day, metres, seconds, *, intent="easy", title=None):
    return {
        "window_start": day.isoformat(),
        "window_end": day.isoformat(),
        "intent": intent,
        "discipline": "run",
        "title": title or f"Run {metres / 1000:g} km",
        "target_distance_m": metres,
        "target_duration_s": seconds,
    }


def _sketch(week_start, running_m, seconds, **kw):
    return {
        "week_start": week_start.isoformat(),
        "phase": kw.pop("phase", "Base"),
        "target_running_distance_m": running_m,
        "target_duration_s": seconds,
        "target_walking_distance_m": 0,
        **kw,
    }


def _block(first, last, phase, running_m, seconds, long_run_m):
    return {
        "week_start": first.isoformat(),
        "through_week_start": last.isoformat(),
        "phase": phase,
        "target_running_distance_m": running_m,
        "target_duration_s": seconds,
        "long_run_distance_m": long_run_m,
        "sessions_by_discipline": {"run": 4},
    }


# --- how far the plan reaches --------------------------------------------------


def test_the_plan_reaches_the_a_goal_and_names_every_goal_on_the_way(db):
    user = _seed_user(db)
    _season(db, user)
    races = store.list_goal_races(db, user.id, on_or_after=TODAY)

    reach = plan_reach_weeks(TODAY, races, starts_on=MONDAY)
    context = build_draft_context(db, user, today=TODAY, weeks=12)

    # A window reaches its END: the marathon falls somewhere inside it.
    assert reach == (MARATHON_TO - TODAY).days // 7 + 1
    assert f"HORIZON: {reach} weeks" in context
    assert "so the plan reaches First marathon" in context
    assert f"outline blocks from the week beginning {(TODAY + 12 * WEEK).isoformat()}" in context
    for name in ("Town 10k", "Spring Half", "First marathon", "Backyard ultra"):
        assert name in context


def test_the_reach_is_bounded_and_says_so_when_the_goal_is_further(db):
    user = _seed_user(db)
    _goal(db, user, "Far ultra", priority="A", race_date=TODAY + 60 * WEEK, distance_m=100000)
    races = store.list_goal_races(db, user.id, on_or_after=TODAY)

    assert plan_reach_weeks(TODAY, races, starts_on=MONDAY) == MAX_REACH_WEEKS
    assert "Far ultra is beyond it" in build_draft_context(db, user, today=TODAY, weeks=12)


# --- the gate ---------------------------------------------------------------------


def _plan(**kw) -> DraftedPlan:
    return DraftedPlan.model_validate({"rules": [], "weeks": [], "sketch_weeks": [], **kw})


def test_a_race_in_a_sketched_week_is_not_judged_as_training():
    # 15 km typical: the sketched ceiling is 45 km, the week holds 60 with a marathon.
    plan = _plan(sketch_weeks=[_sketch(NEXT_MON, 60000, 6 * 3600)])
    kwargs = dict(today=TODAY, norm_weekly_running_m=15000)

    assert validate_drafted_plan(plan, races=[(NEXT_MON + timedelta(days=6), 42195)], **kwargs).ok
    assert VOLUME_CEILING in validate_drafted_plan(plan, **kwargs).codes


@pytest.mark.parametrize(
    "blocks, sketches, failure",
    [
        # A race inside a block would be averaged into a typical week.
        ([_block(NEXT_MON, NEXT_MON + 3 * WEEK, "Build", 20000, 3 * 3600, 15000)], [],
         "swallows the race"),
        # A block may not restate a week already given.
        ([_block(NEXT_MON + 4 * WEEK, NEXT_MON + 6 * WEEK, "Build", 20000, 3 * 3600, 15000)],
         [_sketch(NEXT_MON + 5 * WEEK, 20000, 3 * 3600)], "overlaps week"),
        # A block states a typical week, held to the sketched ceiling (45 km here).
        ([_block(NEXT_MON + 4 * WEEK, NEXT_MON + 6 * WEEK, "Build", 50000, 3 * 3600, 15000)],
         [], "km of running a week"),
        # And no further than the plan's reach.
        ([_block(NEXT_MON + 4 * WEEK, NEXT_MON + 20 * WEEK, "Build", 20000, 3 * 3600, 15000)],
         [], "past the 12-week horizon"),
    ],
)
def test_an_outline_block_is_held_to_the_same_gate(blocks, sketches, failure):
    plan = _plan(outline_blocks=blocks, sketch_weeks=sketches)

    check = validate_drafted_plan(
        plan, today=TODAY, norm_weekly_running_m=15000, horizon_weeks=12,
        races=[(NEXT_MON + 2 * WEEK + timedelta(days=6), 10000)],
    )

    assert any(failure in f for f in check.failures), check.failures


def test_a_plan_that_stops_before_its_goal_is_refused():
    plan = _plan(sketch_weeks=[_sketch(NEXT_MON, 20000, 3 * 3600)])

    check = validate_drafted_plan(
        plan, today=TODAY, reach_goal=("First marathon", MARATHON_FROM)
    )

    assert not check.ok
    assert "before First marathon" in check.failures[0]
    assert validate_drafted_plan(plan, today=TODAY).ok


def test_the_tool_states_the_focus_limit_it_enforces():
    """A live draft lost an attempt to a focus over a limit the schema never said."""
    props = RECORD_TRAINING_PLAN_TOOL["input_schema"]["properties"]
    for key in ("sketch_weeks", "outline_blocks"):
        focus = props[key]["items"]["properties"]["quality_focus"]
        assert focus["maxLength"] == QUALITY_FOCUS_MAX_LENGTH
        assert str(QUALITY_FOCUS_MAX_LENGTH) in focus["description"]
    for model in (SketchedWeek, OutlineBlock):
        with pytest.raises(ValueError):
            model.model_validate({
                "week_start": TODAY, "through_week_start": TODAY, "phase": "Base",
                "target_duration_s": 0, "quality_focus": "x" * (QUALITY_FOCUS_MAX_LENGTH + 1),
            } if model is OutlineBlock else {
                "week_start": TODAY, "quality_focus": "x" * (QUALITY_FOCUS_MAX_LENGTH + 1),
            })


# --- a far block, stored and read back as a block ---------------------------------


@pytest.mark.asyncio
async def test_a_season_plan_is_stored_and_read_back_to_the_goal(db, monkeypatch):
    user = _seed_user(db)
    _seed_history(db, user)
    _season(db, user)
    plan = store.create_drafting_plan(db, user.id)
    half_week = HALF - timedelta(days=6)
    drafted = {
        "rules": [],
        "weeks": [{"week_start": TODAY.isoformat(), "phase": "Base",
                   "sessions": [_run(TUE, 8000, 2700)]}],
        "sketch_weeks": [
            _sketch(NEXT_MON, 30000, 4 * 3600),
            _sketch(half_week, 35000, 4 * 3600, phase="Half race week"),
        ],
        "outline_blocks": [
            _block(TODAY + 12 * WEEK, half_week - WEEK, "Base", 32000, 4 * 3600, 20000),
            # Dated a Tuesday, the slip a live draft made thirty weeks out: the
            # block is read as starting in that day's week, not refused.
            _block(half_week + WEEK + timedelta(days=1), MARATHON_FROM, "Build",
                   40000, 5 * 3600, 30000),
        ],
    }
    _inject(monkeypatch, _FakeClient([drafted]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok, outcome.failures
    horizon = build_horizon(db, user, weeks=MAX_HORIZON_WEEKS, today=TODAY)
    outlined = [w for w in horizon.weeks if w.coverage == "outlined"]
    assert len(outlined) == 10 + 8
    assert horizon.plan_reach_weeks == 31
    # The long run is stated where the block says it gets to, not on every week.
    assert [w.long_run_distance_m for w in outlined if w.long_run_distance_m] == [20000, 30000]

    # The coach reads the far stretch as two blocks, never as eighteen weeks
    # that would each seem to set 32 or 40 km.
    read = query_tools.get_training_plan(db, user.id, today=TODAY)
    assert [(b["phase"], b["weeks"]) for b in read["outline_blocks"]] == [("Base", 10), ("Build", 8)]
    assert read["outline_blocks"][1]["long_run_reaches_km_by_block_end"] == 30.0
    assert read["plan_covers_through_week_starting"] == MARATHON_FROM.isoformat()
    outlined_starts = {w.week_start.isoformat() for w in outlined}
    assert not outlined_starts & {w["week_start"] for w in read["weeks"]}


# --- the races reach the gate from both paths --------------------------------------


def _race_week(week_start, race_day):
    """28 km of training plus a 30 km B race: 58 km against a 56 km ceiling
    (`_seed_history`'s 28 km week), so it passes only with the race left out."""
    return {
        "week_start": week_start.isoformat(),
        "phase": "Build",
        "sessions": [
            _run(week_start + timedelta(days=1), 14000, 5000),
            _run(week_start + timedelta(days=3), 14000, 5000),
            _run(race_day, 30000, 10800, intent="long", title="Trail race"),
        ],
    }


@pytest.mark.asyncio
async def test_the_draft_hands_every_race_to_the_gate_not_only_the_target(db, monkeypatch):
    user = _seed_user(db)
    _seed_history(db, user)
    _goal(db, user, "Trail race", priority="B", race_date=SUN, distance_m=30000)
    _goal(db, user, "Autumn marathon", priority="A", race_date=SUN + WEEK, distance_m=42195)
    plan = store.create_drafting_plan(db, user.id)
    drafted = {
        "rules": [],
        "weeks": [_race_week(TODAY, SUN)],
        "sketch_weeks": [_sketch(NEXT_MON, 50000, 6 * 3600, phase="Race week")],
    }
    _inject(monkeypatch, _FakeClient([drafted, drafted]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok, outcome.failures


def test_the_amendment_hands_every_race_to_the_gate_too(db):
    user = _seed_user(db)
    _seed_history(db, user)
    _goal(db, user, "Trail race", priority="B", race_date=NEXT_MON + timedelta(days=6),
          distance_m=30000)
    _goal(db, user, "First marathon", priority="A", window_start=MARATHON_FROM,
          window_end=MARATHON_TO, distance_m=42195)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()

    class _Client:
        model = "fake"

        async def generate_structured(self, *, system, user, tool, max_tokens=1024, timeout=None):
            return {"weeks": [_race_week(NEXT_MON, NEXT_MON + timedelta(days=6))]}

    with patch.object(amend.turn, "build_client", return_value=_Client()), \
         patch.object(amend.turn, "over_budget", return_value=False):
        proposal = asyncio.run(
            amend.propose_amendment(
                db, user, plan, weeks_from=1, weeks_through=1,
                instruction="put the trail race in", today=TODAY,
            )
        )

    assert proposal.ok, proposal.failures
