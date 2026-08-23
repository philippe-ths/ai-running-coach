"""A block settled in conversation reaches the schedule (#856).

The defect these cover was observed in normal use: the coach worked out a
seven-week half-marathon build in a thread, then declined to put it in the
schedule, sent the runner to "your schedule app", and offered to dictate the
block a week at a time instead.

Three things had to be true for that reply, and each has its own tests here:

1. There was no route from a conversation to the plan writer at all.
2. The coach could not see the schedule, so it did not know the screen the
   runner was standing on belonged to this app.
3. Its instructions told it that handing the week back as prose was the job.

All row data is synthetic test setup. The oracle for what a written plan must
satisfy is the EXISTING gate — `validate_drafted_plan` and `DraftedPlan` — which
is the point: a plan that arrives through a conversation is held to the same bar
as one the Schedule button asked for.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models.coach_chat_message import CoachChatMessage
from app.models.planned_session import PlannedSession
from app.models.thread import Thread
from app.models.user import User
from app.services.coach import proposed_actions
from app.services.schedule import store
from app.services.weeks import MONDAY, is_last_day_of_week, resolve_week_start, week_start

TODAY = date(2026, 8, 12)


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _user(db) -> User:
    user = User(email=f"u-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _thread_with_settled_block(db, user: User) -> Thread:
    thread = Thread(user_id=user.id)
    db.add(thread)
    db.commit()
    db.refresh(thread)
    turns = [
        ("user", "Can you build me up for a half in seven weeks?"),
        (
            "assistant",
            "Here's the block: weeks 1-3 build to 42 km with a Thursday tempo, "
            "weeks 4-5 peak at 50 km, then two weeks of taper into the race.",
        ),
        ("user", "Are you going to enter this into my schedule?"),
    ]
    for role, content in turns:
        db.add(CoachChatMessage(thread_id=thread.id, role=role, content=content))
    db.commit()
    return thread


def _active_plan(db, user: User):
    plan = store.create_drafting_plan(db, user.id)
    plan.horizon_end = TODAY + timedelta(days=60)
    store.activate_plan(db, plan)
    return plan


# --- 1. the route from a conversation to the plan writer ---------------------


def test_the_offer_writes_nothing_until_the_runner_confirms(db):
    """Offering is not doing. The whole point of the card is that the plan the
    coach talked through does not touch the schedule until the runner taps."""
    user = _user(db)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "draft_plan"}, thread_id=uuid.uuid4()
        )

    assert result["ok"] is True
    assert frame["action_type"] == "draft_plan"
    assert store.latest_plan(db, user.id) is None


def test_confirming_starts_a_draft_seeded_with_that_conversation(db):
    """The confirmed write: a drafting row plus a job that knows WHICH thread
    settled the plan. Without the thread the worker writes a fresh plan, which is
    not the one the runner just agreed to."""
    user = _user(db)
    thread = _thread_with_settled_block(db, user)
    enqueued = {}

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()), patch(
        "app.services.schedule.draft.enqueue_draft",
        side_effect=lambda u, p, t=None: enqueued.update(user=u, plan=p, thread=t),
    ):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "draft_plan"}, thread_id=thread.id
        )
        outcome = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    plan = store.latest_plan(db, user.id)
    assert plan is not None and plan.status == store.DRAFTING
    assert outcome["plan_id"] == str(plan.id)
    assert enqueued["thread"] == thread.id
    # The runner is told where the write landed: it happens on the worker, so the
    # card disappearing is otherwise the only feedback there is.
    assert "Schedule" in outcome["message"]


def test_a_second_confirm_joins_the_draft_already_running(db):
    """Two devices, a double tap, a stale card. A second draft would race the
    first to supersede it, so the second confirm joins rather than starts one —
    the `POST /api/schedule/draft` precedent."""
    user = _user(db)
    enqueues = []

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()), patch(
        "app.services.schedule.draft.enqueue_draft",
        side_effect=lambda u, p, t=None: enqueues.append(p),
    ):
        tokens = []
        for _ in range(2):
            _result, frame = proposed_actions.mint_proposed_action(
                db, user.id, {"action_type": "draft_plan"}
            )
            tokens.append(frame["token"])
        first = proposed_actions.consume_and_execute(db, user.id, tokens[0])
        second = proposed_actions.consume_and_execute(db, user.id, tokens[1])

    assert first["plan_id"] == second["plan_id"]
    assert len(enqueues) == 1


def test_the_card_names_the_plan_it_would_replace_and_how_recent_it_is(db):
    """Writing a plan supersedes the one the runner is training to, and that is
    the part of this action they most need to see BEFORE it happens. A confirm
    step is only worth having if the card says what will be written over.

    With its AGE (#883). A runner asked "Is it added?", was offered a second
    card, and confirmed it — replacing the plan they had accepted ninety seconds
    earlier with a differently generated one, because drafting is not
    deterministic. "Your current one" was true and silent about the only part
    that would have stopped them: that it was the plan they had just agreed.
    """
    user = _user(db)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        _r, without = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "draft_plan"}
        )
        _active_plan(db, user)
        _r, with_plan = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "draft_plan"}
        )

    assert "replacing" not in without["description"]
    assert with_plan["description"] == (
        "Write this plan into your schedule, replacing the one written just now"
    )


def test_the_card_names_an_older_plans_age_in_its_own_terms(db):
    """The same fact, when the plan is genuinely old. A runner who has been
    training to a plan for a fortnight is not making the mistake this exists to
    catch, and telling them "just now" would be a lie in the other direction."""
    user = _user(db)
    plan = _active_plan(db, user)
    plan.generated_at = datetime.now(timezone.utc) - timedelta(days=14)
    db.commit()

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        _r, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "draft_plan"}
        )

    assert frame["description"].endswith("replacing the one written 14 days ago")


@pytest.mark.parametrize(
    "minutes_ago, expected",
    [
        (0, "just now"),
        (1, "1 minute ago"),
        (5, "5 minutes ago"),
        (60, "1 hour ago"),
        (200, "3 hours ago"),
        (60 * 24, "1 day ago"),
        (60 * 24 * 14, "14 days ago"),
    ],
)
def test_a_plans_age_is_said_the_way_a_person_would_say_it(minutes_ago, expected):
    """One phrasing, two audiences: the runner's card and the coach's own read.
    A plan cannot be two ages, so neither may invent its own wording."""
    from app.services.schedule.coach_view import describe_written_ago

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    class _Plan:
        generated_at = None
        created_at = now - timedelta(minutes=minutes_ago)

    assert describe_written_ago(_Plan(), now=now) == expected


def test_a_plan_with_no_timestamp_yields_no_age_rather_than_raising():
    """The card must still describe what it replaces when the age is unknown.
    Unreachable through the app today — every stored plan carries `created_at` —
    so it is exercised here rather than left as an untested claim in the code."""
    from app.services.coach.proposed_actions import _replace_description
    from app.services.schedule.coach_view import describe_written_ago

    class _Timeless:
        generated_at = None
        created_at = None

    assert describe_written_ago(_Timeless()) is None
    assert _replace_description(_Timeless(), describe_written_ago) == (
        "Write this plan into your schedule, replacing your current one"
    )


def test_a_naive_timestamp_is_read_as_utc_rather_than_raising():
    """SQLite hands back naive datetimes where Postgres does not, and subtracting
    across the two raises instead of being quietly wrong."""
    from app.services.schedule.coach_view import describe_written_ago

    class _Plan:
        generated_at = None
        created_at = datetime(2026, 8, 13, 11, 55)  # naive

    assert describe_written_ago(
        _Plan(), now=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    ) == "5 minutes ago"


def test_the_coach_can_see_when_the_current_plan_was_written(db):
    """So "Is it added?" is answered from the record rather than re-offered.

    The offer that caused this was a reasonable reply to a question the coach
    could not check: it could see a plan but not that the plan WAS the one it had
    written ninety seconds before.
    """
    from app.services.coach import thread_turn

    user = _user(db)
    _active_plan(db, user)

    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert schedule["written"] == "just now"


def test_the_offer_is_refused_while_the_schedule_is_switched_off(db, monkeypatch):
    """`SCHEDULE_ENABLED` off answers every schedule route with 503. A card
    offering to write to a screen that is down is a promise the app cannot keep."""
    user = _user(db)
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "draft_plan"}
        )
    assert result["ok"] is False
    assert frame is None


def test_the_action_takes_no_arguments_from_the_model(db):
    """The block lives in the conversation and the thread id comes from the
    server, so there is no field here a model could get wrong — and no route by
    which a model-supplied id could reach another runner's conversation."""
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        result, frame = proposed_actions.mint_proposed_action(
            db,
            _user(db).id,
            {"action_type": "draft_plan", "thread_id": str(uuid.uuid4())},
        )
    assert result["ok"] is False
    assert frame is None


# --- the drafting side: transcription, not a fresh plan ----------------------


def test_the_settled_conversation_reaches_the_drafting_prompt(db):
    """What the runner confirmed is what the drafting call is asked to write."""
    from app.services.schedule import draft

    user = _user(db)
    thread = _thread_with_settled_block(db, user)

    block = draft._conversation_block(db, user, str(thread.id))

    assert "weeks 4-5 peak at 50 km" in block
    assert "Runner:" in block and "You:" in block


def test_another_runners_thread_is_never_read_into_a_draft(db):
    """The thread id arrives as a job argument rather than from an authenticated
    request, so it is re-checked against the plan's owner rather than trusted."""
    from app.services.schedule import draft

    owner = _user(db)
    stranger = _user(db)
    thread = _thread_with_settled_block(db, stranger)

    assert draft._conversation_block(db, owner, str(thread.id)) == ""


def test_a_seeded_draft_is_told_to_transcribe_rather_than_re_plan(db):
    """The coaching decisions were made in front of the runner and they confirmed
    THOSE. A model that plans again here hands back something they never agreed
    to — so a seeded draft carries the transcription clause and an unseeded one
    does not."""
    import asyncio

    from app.services.schedule import draft

    user = _user(db)
    thread = _thread_with_settled_block(db, user)
    seen = {}

    class _Client:
        model = "test-model"

        async def generate_structured(self, *, system, user, tool, max_tokens):
            seen.setdefault("systems", []).append(system)
            seen.setdefault("users", []).append(user)
            raise RuntimeError("stop after capturing the prompt")

    with patch.object(draft.turn, "over_budget", return_value=False), patch.object(
        draft.turn, "build_client", return_value=_Client()
    ):
        plan = store.create_drafting_plan(db, user.id)
        asyncio.run(draft.draft_plan(db, user, plan, today=TODAY, thread_id=str(thread.id)))
        seeded_system, seeded_user = seen["systems"][0], seen["users"][0]

        seen.clear()
        plan2 = store.create_drafting_plan(db, user.id)
        asyncio.run(draft.draft_plan(db, user, plan2, today=TODAY))
        plain_system = seen["systems"][0]

    assert "THIS PLAN IS ALREADY SETTLED" in seeded_system
    assert "TRANSCRIBE" in seeded_system
    assert "weeks 4-5 peak at 50 km" in seeded_user
    assert "THIS PLAN IS ALREADY SETTLED" not in plain_system
    # The base instructions still bind: placement, commitment, rules and the
    # things a plan may never do are not relaxed because a runner agreed to it.
    assert plain_system in seeded_system


def test_a_seeded_draft_that_fails_the_gate_stores_nothing(db):
    """The acceptance criterion that matters most. A plan settled in conversation
    goes through the same coherence gate as any other, and a plan cannot degrade
    the way a report can — half a schedule is worse than none when the runner
    would act on it."""
    import asyncio

    from app.services.schedule import draft

    user = _user(db)
    thread = _thread_with_settled_block(db, user)

    class _Client:
        model = "test-model"

        async def generate_structured(self, *, system, user, tool, max_tokens):
            # Well-shaped and incoherent: a session in a week that has already
            # gone. `validate_drafted_plan` is what must catch this, not a test.
            return {
                "summary": "a plan aimed at last month",
                "rules": [],
                "weeks": [
                    {
                        "week_start": (TODAY - timedelta(days=28)).isoformat(),
                        "phase": "base",
                        "sessions": [
                            {
                                "window_start": (TODAY - timedelta(days=28)).isoformat(),
                                "window_end": (TODAY - timedelta(days=28)).isoformat(),
                                "intent": "easy",
                                "discipline": "run",
                                "commitment": "committed",
                                "title": "Easy run",
                                "target_distance_m": 5000,
                            }
                        ],
                    }
                ],
                "sketch_weeks": [],
            }

    with patch.object(draft.turn, "over_budget", return_value=False), patch.object(
        draft.turn, "build_client", return_value=_Client()
    ):
        plan = store.create_drafting_plan(db, user.id)
        outcome = asyncio.run(
            draft.draft_plan(db, user, plan, today=TODAY, thread_id=str(thread.id))
        )

    assert outcome.ok is False
    assert plan.status != store.ACTIVE
    assert db.query(PlannedSession).filter(PlannedSession.plan_id == plan.id).count() == 0


# --- 2. the coach can see the schedule ---------------------------------------


def test_the_conversation_is_told_the_runner_has_no_plan_rather_than_left_silent(db):
    """Absence of a plan and absence of a schedule are different facts, and only
    one of them is true. Silence was read as the second."""
    from app.services.coach import thread_turn

    user = _user(db)
    sections = thread_turn._build_baseline_sections(db, user)

    assert "schedule" in sections and sections["schedule"] is None
    rendered = thread_turn._render_baseline_block(sections)
    assert "no active plan" in rendered
    assert "this app's Schedule screen" in rendered


def _in_current_week_and_not_past(today: date, starts_on: int) -> date:
    """A day guaranteed both inside `today`'s week and not before `today` (#949).

    The original fixture placed the session at `today + 1 day` — "tomorrow" — to
    stand in for a session still to come. That coincidentally lands inside the
    current Mon-Sun week on every day but Sunday, where "tomorrow" is Monday of
    NEXT week: `committed_this_week` (scoped to `[week_start, week_start + 6]`)
    then reads 0 instead of 1. The fix is by construction rather than luck: fall
    back to `today` itself on the week's last day, since that is the only day
    left that is both still-to-come and inside the current week.
    """
    if is_last_day_of_week(today, starts_on):
        return today
    return today + timedelta(days=1)


def test_the_conversation_carries_the_week_the_runner_is_actually_training(db):
    """Not the twelve-week horizon — that is the runner's screen. What the plan
    still asks of this week, so the coach's advice does not contradict the plan
    it wrote itself."""
    from app.services.coach import thread_turn

    user = _user(db)
    plan = _active_plan(db, user)
    today = date.today()
    # The test user has no profile, so the week starts on Monday (#949; see
    # test_resolve_week_start_with_no_profile_is_monday below for the pin).
    starts_on = resolve_week_start(None)
    session_day = _in_current_week_and_not_past(today, starts_on)
    db.add(
        PlannedSession(
            plan_id=plan.id,
            user_id=user.id,
            window_start=session_day,
            window_end=session_day,
            intent="quality",
            discipline="run",
            commitment="committed",
            title="Thursday tempo",
            target_distance_m=8000,
        )
    )
    db.commit()

    sections = thread_turn._build_baseline_sections(db, user)
    schedule = sections["schedule"]

    assert schedule["has_plan"] is True
    assert schedule["committed_this_week"] == 1
    titles = [s["title"] for s in schedule["still_to_come_this_week"]]
    assert "Thursday tempo" in titles
    # Intent, never a scorecard: handed a plan and a result, a model reaches for
    # a compliance verdict, which is the nagging ADR 0025 removed.
    assert all("done" not in s for s in schedule["still_to_come_this_week"])


def test_resolve_week_start_with_no_profile_is_monday():
    """Pins the assumption the fixture above relies on: a user with no profile
    (the `_user` helper here never sets one) resolves to a Monday-start week."""
    assert resolve_week_start(None) == MONDAY


@pytest.mark.parametrize(
    "today",
    [date(2026, 8, 10) + timedelta(days=offset) for offset in range(7)],
    ids=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
)
def test_the_conversation_carries_the_week_the_runner_is_actually_training_on_every_weekday(
    db, monkeypatch, today
):
    """Day-independence proof for #949: the fixture-construction fix above must
    hold no matter which real-world weekday the suite happens to run on.

    `build_thread_schedule` reads `date.today()` (via `app.services.schedule
    .coach_view`'s module-level `date` import) when no `today` is passed, and
    `_build_baseline_sections`/`_schedule_section` never pass one through — so
    the only injection seam available without adding a dependency (e.g.
    freezegun, which is not in this project) is patching that module's `date`
    name to a subclass whose `today()` returns the fixed value under test. This
    exercises the REAL call chain the app uses (`thread_turn
    ._build_baseline_sections` -> `_schedule_section` -> `build_thread_schedule`
    -> `_week_view`), not a reimplementation of it.

    2026-08-10 is a Monday, so the seven parametrized dates cover one full week
    including Sunday — the day the original bug only reproduced on.
    """
    from app.services.coach import thread_turn
    from app.services.schedule import coach_view

    class _PinnedDate(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(coach_view, "date", _PinnedDate)

    user = _user(db)
    plan = _active_plan(db, user)
    starts_on = resolve_week_start(None)
    session_day = _in_current_week_and_not_past(today, starts_on)

    # Sanity-check the construction itself before trusting the app's read of it.
    ws = week_start(today, starts_on)
    we = ws + timedelta(days=6)
    assert ws <= session_day <= we
    assert session_day >= today

    db.add(
        PlannedSession(
            plan_id=plan.id,
            user_id=user.id,
            window_start=session_day,
            window_end=session_day,
            intent="quality",
            discipline="run",
            commitment="committed",
            title="Thursday tempo",
            target_distance_m=8000,
        )
    )
    db.commit()

    sections = thread_turn._build_baseline_sections(db, user)
    schedule = sections["schedule"]

    assert schedule["has_plan"] is True
    assert schedule["committed_this_week"] == 1
    titles = [s["title"] for s in schedule["still_to_come_this_week"]]
    assert "Thursday tempo" in titles


def test_the_conversation_reads_the_rule_that_is_enforced_not_the_coach_s_label(db):
    """#844/#847, one surface further in. A `SpacingRule`'s `label` is LLM-written
    prose that nothing ties to the predicate, so it can promise what the rule does
    not enforce — a live draft invited a walk the predicate then flagged. A coach
    handed the label would plan that same violation into the next block, so the
    conversation reads the DERIVED statement, exactly as the report pack does."""
    from app.services.coach import thread_turn
    from app.services.schedule.rule_text import describe_rule

    user = _user(db)
    plan = _active_plan(db, user)
    plan.rules = [
        {
            "kind": "rest_day_after",
            "intent": "long",
            "label": "Full rest or easy walk only the day after the long run",
        }
    ]
    db.commit()

    rules = thread_turn._build_baseline_sections(db, user)["schedule"]["rules_in_play"]
    derived = describe_rule(store.plan_rules(plan)[0])

    assert rules == [derived]
    assert "easy walk" not in rules[0]


def test_the_coach_input_switch_removes_the_schedule_from_the_conversation(db, monkeypatch):
    """`COACH_SCHEDULE_ENABLED` is the input switch: off, the coach stops seeing
    the plan while the runner's Schedule screen keeps working exactly as before."""
    from app.services.coach import thread_turn

    user = _user(db)
    _active_plan(db, user)
    monkeypatch.setattr(settings, "COACH_SCHEDULE_ENABLED", False)

    sections = thread_turn._build_baseline_sections(db, user)

    assert "schedule" not in sections
    assert "Schedule screen" not in thread_turn._render_baseline_block(sections)


def test_a_schedule_fault_costs_the_section_not_the_reply(db):
    """A background read failing must never be the reason a runner gets no
    answer, the same rule the readiness read follows."""
    from app.services.coach import thread_turn

    user = _user(db)
    with patch(
        "app.services.schedule.coach_view.build_thread_schedule",
        side_effect=RuntimeError("boom"),
    ):
        sections = thread_turn._build_baseline_sections(db, user)

    assert sections["schedule"] is None


# --- 2b. a plan being written, and one that failed (#879) --------------------


def test_the_coach_is_told_when_a_plan_it_offered_is_still_being_written(db):
    """The coach cannot see the worker, so without this it reads an unchanged
    plan and concludes nothing happened.

    In production it did exactly that: it told the runner "the plan is in" while
    a draft it had offered was still running, and again after that draft had
    failed. Both answers were about a plan the coach had no way to know had been
    attempted.
    """
    from app.services.coach import thread_turn

    user = _user(db)
    _active_plan(db, user)
    store.create_drafting_plan(db, user.id)

    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert schedule["has_plan"] is True
    assert schedule["draft"] == {"state": "writing"}
    assert "writing" in thread_turn._render_schedule_block(schedule)


def test_the_coach_is_told_when_the_last_draft_failed(db):
    """The state the runner was in for four turns. The plan is the previous one,
    unchanged, and the coach would read that as success."""
    from app.services.coach import thread_turn

    user = _user(db)
    _active_plan(db, user)
    failed = store.create_drafting_plan(db, user.id)
    store.fail_plan(db, failed, "rejected by the gate")

    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert schedule["draft"] == {"state": "failed"}


def test_the_failure_is_reported_even_when_it_ties_the_plan_it_replaced(db):
    """`latest_plan` cannot answer this question, and the tie is where it shows.

    It orders by `created_at` and breaks a tie on the id, which is a UUID — fine
    for a poll, which only needs a STABLE answer. "Was the last thing that
    happened a failure" needs the RIGHT one, and here the arbitrary tiebreaker
    picks the active plan and reports success. The ids below are fixed so the
    tiebreaker deterministically picks the wrong row: without the explicit
    comparison this test reports no failure at all.
    """
    from app.models.training_plan import TrainingPlan
    from app.services.coach import thread_turn

    user = _user(db)
    same_instant = datetime(2026, 8, 13, 6, 39, tzinfo=timezone.utc)
    # The active plan sorts LAST by id, so the tiebreaker prefers it.
    db.add(
        TrainingPlan(
            id=uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            user_id=user.id,
            status=store.ACTIVE,
            rules=[],
            week_shapes=[],
            created_at=same_instant,
        )
    )
    db.add(
        TrainingPlan(
            id=uuid.UUID("00000000-0000-4000-8000-000000000000"),
            user_id=user.id,
            status=store.FAILED,
            rules=[],
            week_shapes=[],
            created_at=same_instant,
        )
    )
    db.commit()

    assert store.latest_plan(db, user.id).status == store.ACTIVE  # the wrong read
    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert schedule["draft"] == {"state": "failed"}


def test_a_runner_with_no_plan_still_learns_one_is_being_written(db):
    """The no-plan case returns None so the caller can say so in words. A draft
    in flight is a different fact and must survive that shortcut — this is the
    first plan, so there is nothing else to notice it by."""
    from app.services.coach import thread_turn

    user = _user(db)
    store.create_drafting_plan(db, user.id)

    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert schedule == {"has_plan": False, "draft": {"state": "writing"}}


def test_a_plan_that_simply_landed_carries_no_draft_note(db):
    """Only the two states the coach could get wrong. A plan that is there is its
    own evidence, and a note on every turn would be noise."""
    from app.services.coach import thread_turn

    user = _user(db)
    _active_plan(db, user)

    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert "draft" not in schedule


def test_the_coach_is_told_that_confirming_is_not_landing(db):
    """The prose half. The rule said "nothing is written unless they confirm it",
    which is true and stops one turn short: a confirmed plan is written in the
    background and can fail."""
    from app.services.coach.thread_turn import THREAD_SYSTEM_TEMPLATE

    assert "written in the background and can fail" in THREAD_SYSTEM_TEMPLATE
    assert "say that plainly rather than repeating the offer" in THREAD_SYSTEM_TEMPLATE


# --- 2c. the numbers the runner is looking at (#880) -------------------------


def test_the_conversation_carries_the_week_total_and_each_sessions_distance(db):
    """The exact turn this exists for.

    "This week was meant to be a total of 28-29 but it's listed as 25", then "it
    shows the intervals as 2.5k not 4.5k". Both numbers were on the runner's
    screen and neither was in front of the coach: the section carried "6 x 400 m
    off 90 s" and no distance at all. It answered anyway — an invented mechanism
    first, then a diagnosis of the app — about data that was simply stored short.
    """
    from app.services.coach import thread_turn

    from app.services.weeks import week_start

    user = _user(db)
    plan = _active_plan(db, user)
    # Relative to the real today, because the conversation's read is anchored to
    # now and takes no date. A fixed Monday would pass for one week and then rot.
    today = date.today()
    monday = week_start(today, 0)
    db.add(
        PlannedSession(
            plan_id=plan.id, user_id=user.id,
            window_start=monday,
            window_end=monday + timedelta(days=6),
            intent="quality", discipline="run", commitment="committed",
            title="Intervals: 6x400m", target_distance_m=None,
            structure={
                "reps_planned": 6, "rep_distance_m": 400, "rest_s": 90,
                "warmup_distance_m": 1050, "cooldown_distance_m": 1050,
            },
        )
    )
    db.commit()

    schedule = thread_turn._build_baseline_sections(db, user)["schedule"]

    assert schedule["planned_running_this_week"] == "4.50 km"
    assert schedule["still_to_come_this_week"][0]["target"] == (
        "4.50 km (6 x 400 m off 90 s)"
    )


# --- 3. what the coach is told -----------------------------------------------


def test_the_coach_is_told_a_plan_belongs_in_the_schedule(db):
    """The prose half of the defect. The reply that exposed this sent the runner
    to 'your schedule app' and offered week-by-week dictation, and both were
    downstream of instructions rather than of a missing capability."""
    from app.services.coach.thread_turn import THREAD_SYSTEM_TEMPLATE

    assert "A plan belongs in their schedule, not in the transcript" in THREAD_SYSTEM_TEMPLATE
    assert "yours to write" in THREAD_SYSTEM_TEMPLATE
    assert "never point them at another app for something this one does" in (
        THREAD_SYSTEM_TEMPLATE
    )
    assert "draft_plan" in THREAD_SYSTEM_TEMPLATE


def test_planning_a_week_ends_at_the_schedule_not_at_a_prose_list():
    """The line that CAUSED the failure said the prose list was the finished job.
    The procedure now names where a plan goes, whether it is one week or a block."""
    from app.services.coach.coaching_skills import load_skill

    # Wrapped prose: compare on collapsed whitespace so a reflow is not a failure.
    procedure = " ".join(load_skill("plan_the_week").procedure.split())

    assert "A plan belongs in their schedule, not in the transcript" in procedure
    assert "draft_plan" in procedure
    assert "each Monday for the next instalment" in procedure


@pytest.mark.parametrize("phrase", ["your schedule app", "another app"])
def test_no_instruction_sends_the_runner_out_of_the_product(phrase):
    """Whatever else changes, the coach must not be told — or shown — that the
    schedule is somewhere else."""
    from app.services.coach.coaching_skills import SKILLS
    from app.services.coach.thread_turn import THREAD_SYSTEM_TEMPLATE

    texts = [THREAD_SYSTEM_TEMPLATE] + [s.procedure for s in SKILLS]
    for text in texts:
        if phrase == "another app":
            # Named only in the rule forbidding it.
            occurrences = text.count(phrase)
            assert occurrences == text.count("never point them at another app")
        else:
            assert phrase not in text
