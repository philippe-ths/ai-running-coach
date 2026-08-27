"""#830: asking the coach for a plan — the two draft endpoints and the RQ job.

Drafting is runner-TRIGGERED: the endpoint mints the `drafting` row, hands the
slow generation to the worker and returns immediately, and a second tap while one
is in flight joins the draft already running rather than starting a second. The
row exists before the generation precisely so a crashed worker leaves something
visible; the job's whole contract is that it never crashes the worker and never
leaves a runner polling a row that will never move.

The status the runner reads is deliberately plain: the validator's own failure
text ("week 2026-08-17 cannot satisfy its own rule ...") is internal and stays in
the log.

NO TEST HERE MAY REACH THE NETWORK OR REDIS: the enqueue seam and the generation
are both replaced.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.jobs import generate_schedule as job_mod
from app.main import app
from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule import store
from app.services.schedule.draft import DraftOutcome


def _act_as(user):
    """Inject the acting user (the resolution itself is covered elsewhere)."""
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


@pytest.fixture(autouse=True)
def _no_real_enqueue(monkeypatch):
    """Nothing in this file may touch Redis. Tests that care about the enqueue
    swap in their own recorder over the top of this."""
    monkeypatch.setattr("app.api.schedule.enqueue_draft", lambda user_id, plan_id: None)


def _seed_user(db) -> User:
    user = User(email=f"sched-draft-{uuid4()}@example.com")
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


def _seed_plan(
    db,
    user: User,
    *,
    status: str,
    made_at: datetime,
    failure_kind: str | None = None,
) -> TrainingPlan:
    """`created_at` is explicit because `latest_plan` orders on it alone and the
    column's default has one-second resolution on the test database."""
    plan = TrainingPlan(
        user_id=user.id,
        status=status,
        rules=[],
        week_shapes=[],
        created_at=made_at,
        failure_kind=failure_kind,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


# --- the kill switch --------------------------------------------------------


@pytest.mark.parametrize("method, path", [("post", "/api/schedule/draft"),
                                          ("get", "/api/schedule/draft")])
def test_the_draft_routes_refuse_while_the_schedule_is_switched_off(
    db, client, monkeypatch, method, path
):
    """Both new routes inherit the ROUTER-level switch, so neither can forget it."""
    _act_as(_seed_user(db))
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)

    resp = getattr(client, method)(path)

    assert resp.status_code == 503
    assert "schedule" in resp.json()["detail"].lower()
    assert db.query(TrainingPlan).count() == 0


# --- asking for a plan ------------------------------------------------------


def test_asking_for_a_plan_returns_at_once_and_leaves_a_row_to_poll(db, client, monkeypatch):
    """The generation is a slow LLM call on the worker; the request must not wait
    for it, and the row exists up front so a crashed worker is visible."""
    user = _seed_user(db)
    _act_as(user)
    enqueued = []
    monkeypatch.setattr(
        "app.api.schedule.enqueue_draft",
        lambda user_id, plan_id: enqueued.append((user_id, plan_id)),
    )

    resp = client.post("/api/schedule/draft")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "drafting"
    assert "writing your plan" in body["message"]
    stored = db.query(TrainingPlan).one()
    assert stored.id == UUID(body["plan_id"])
    assert stored.user_id == user.id
    assert stored.status == store.DRAFTING
    assert stored.source == "coach"
    assert enqueued == [(user.id, stored.id)]


def test_a_second_tap_joins_the_draft_already_running(db, client, monkeypatch):
    """Idempotent while one is in flight (the `POST /api/strava/import` precedent):
    a second tap must not start a second generation and spend twice."""
    _act_as(_seed_user(db))
    enqueued = []
    monkeypatch.setattr(
        "app.api.schedule.enqueue_draft",
        lambda user_id, plan_id: enqueued.append((user_id, plan_id)),
    )

    first = client.post("/api/schedule/draft")
    second = client.post("/api/schedule/draft")

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json()["plan_id"] == second.json()["plan_id"]
    assert db.query(TrainingPlan).count() == 1
    assert len(enqueued) == 1


def test_a_runner_with_a_finished_plan_can_ask_for_a_new_one(db, client):
    """Only a draft IN FLIGHT blocks a new one. An active plan does not — that is
    how a runner asks for a rewrite."""
    user = _seed_user(db)
    existing = _seed_plan(db, user, status="active", made_at=datetime(2026, 8, 1, 9, 0))
    _act_as(user)

    resp = client.post("/api/schedule/draft")

    assert resp.status_code == 202
    assert UUID(resp.json()["plan_id"]) != existing.id
    assert db.query(TrainingPlan).count() == 2
    # The old plan keeps serving the week until the new one is accepted.
    assert store.get_active_plan(db, user.id).id == existing.id


def test_another_runners_draft_in_flight_does_not_block_mine(db, client):
    stranger = _seed_user(db)
    _seed_plan(db, stranger, status="drafting", made_at=datetime(2026, 8, 9, 9, 0))
    mine = _seed_user(db)
    _act_as(mine)

    resp = client.post("/api/schedule/draft")

    assert resp.status_code == 202
    plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == mine.id).one()
    assert UUID(resp.json()["plan_id"]) == plan.id


# --- polling ----------------------------------------------------------------


def test_a_runner_who_has_never_had_a_plan_is_told_so_plainly(db, client):
    _act_as(_seed_user(db))

    resp = client.get("/api/schedule/draft")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": None,
        "plan_id": None,
        "generated_at": None,
        "message": "You have no plan yet.",
    }


@pytest.mark.parametrize(
    "status, expected",
    [
        ("drafting", "Your coach is writing your plan. This usually takes a minute."),
        ("active", "Your plan is ready."),
        ("superseded", "This plan has been replaced by a newer one."),
        (
            # No "just now" (#879): this is now shown on the Schedule screen for
            # as long as the last attempt is a failure, which can be days, so a
            # message that reads as news goes stale in front of the runner.
            "failed",
            "Your coach could not write a plan. Nothing has changed — ask again, "
            "or talk it through in a conversation.",
        ),
    ],
)
def test_each_status_reports_its_own_runner_facing_message(db, client, status, expected):
    user = _seed_user(db)
    plan = _seed_plan(db, user, status=status, made_at=datetime(2026, 8, 9, 9, 0))
    _act_as(user)

    body = client.get("/api/schedule/draft").json()

    assert body["status"] == status
    assert UUID(body["plan_id"]) == plan.id
    assert body["message"] == expected


@pytest.mark.parametrize(
    "failure_kind",
    # Every category, plus the null a row written before the column existed
    # carries. The property has to hold for all of them, not just the default:
    # each new category is a new sentence, and a new sentence is a new chance to
    # put the machinery in front of the runner.
    sorted(store.FAILURE_KINDS) + [None],
)
def test_the_failure_the_runner_reads_carries_none_of_the_validators_machinery(
    db, client, failure_kind
):
    """The reason a draft was rejected is internal ("week 2026-08-17 cannot satisfy
    its own rule ..."). What the runner is owed is a plain sentence, and the fact
    that nothing they had has changed."""
    user = _seed_user(db)
    _seed_plan(
        db,
        user,
        status="failed",
        made_at=datetime(2026, 8, 9, 9, 0),
        failure_kind=failure_kind,
    )
    _act_as(user)

    message = client.get("/api/schedule/draft").json()["message"]

    for internal in (
        "rule",
        "satisfy",
        "validator",
        "week_start",
        "km of running",
        "schema",
        "Traceback",
    ):
        assert internal not in message
    assert "Nothing has changed" in message


def test_a_plan_that_ramped_too_hard_says_so_and_names_the_next_move(db, client):
    """#859. Every failure arrived as one sentence, so a runner whose coach had
    ramped far past their history was told to "ask again" — which produces the
    same block and the same rejection. They are owed the move that actually
    changes the outcome."""
    user = _seed_user(db)
    _seed_plan(
        db,
        user,
        status="failed",
        made_at=datetime(2026, 8, 9, 9, 0),
        failure_kind=store.FAILURE_TOO_BIG_A_JUMP,
    )
    _act_as(user)

    message = client.get("/api/schedule/draft").json()["message"]

    assert message != _generic_failure_message()
    assert "gentler" in message
    # In the runner's own terms: what happened, and what they can do about it.
    assert "climbs much faster than your recent weeks" in message
    assert "spread over more weeks" in message


def _generic_failure_message() -> str:
    from app.api.schedule import _DRAFT_FAILURE_MESSAGES

    return _DRAFT_FAILURE_MESSAGES[store.FAILURE_UNKNOWN]


def test_every_category_the_writer_can_store_has_a_sentence_of_its_own(db, client):
    """A category with no message is a lookup miss, which would serve the generic
    sentence and quietly undo the fix. The vocabulary is closed precisely so this
    can be checked."""
    from app.api.schedule import _DRAFT_FAILURE_MESSAGES

    assert set(_DRAFT_FAILURE_MESSAGES) == set(store.FAILURE_KINDS)
    # And no two categories share wording, or the distinction buys nothing.
    assert len(set(_DRAFT_FAILURE_MESSAGES.values())) == len(store.FAILURE_KINDS)


def test_the_runner_facing_read_gains_no_channel_for_the_reason(db, client):
    """The message stays the WHOLE runner-facing surface. The category chooses
    which sentence and never travels itself: a client handed a code would
    eventually render it, and `DraftStatusRead` forbids extras so this is a
    property of the schema rather than of this endpoint's care."""
    user = _seed_user(db)
    _seed_plan(
        db,
        user,
        status="failed",
        made_at=datetime(2026, 8, 9, 9, 0),
        failure_kind=store.FAILURE_TOO_BIG_A_JUMP,
    )
    _act_as(user)

    body = client.get("/api/schedule/draft").json()

    assert set(body) == {"status", "plan_id", "generated_at", "message"}
    assert store.FAILURE_TOO_BIG_A_JUMP not in str(body)


def test_the_poll_reports_the_newest_plan_not_the_one_still_serving_the_week(
    db, client
):
    user = _seed_user(db)
    _seed_plan(db, user, status="active", made_at=datetime(2026, 8, 1, 9, 0))
    drafting = _seed_plan(db, user, status="drafting", made_at=datetime(2026, 8, 9, 9, 0))
    _act_as(user)

    body = client.get("/api/schedule/draft").json()

    assert body["status"] == "drafting"
    assert UUID(body["plan_id"]) == drafting.id


def test_another_runners_plan_is_never_polled(db, client):
    stranger = _seed_user(db)
    _seed_plan(db, stranger, status="active", made_at=datetime(2026, 8, 9, 9, 0))
    _act_as(_seed_user(db))

    body = client.get("/api/schedule/draft").json()

    assert body["status"] is None
    assert body["plan_id"] is None


# --- the enqueue seam -------------------------------------------------------


def test_the_enqueue_hands_the_worker_the_job_it_expects(monkeypatch):
    """The job path is a deploy contract — RQ stores a deferred job as its
    `module.function` string — so what is enqueued is asserted, not assumed."""
    from app.services.schedule.draft import enqueue_draft

    enqueued = {}

    class _FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            enqueued["func"] = func
            enqueued["args"] = args

    monkeypatch.setattr("app.core.queue.queue", _FakeQueue())
    user_id, plan_id = uuid4(), uuid4()

    enqueue_draft(user_id, plan_id)

    assert enqueued["func"] is job_mod.generate_schedule_job
    # The screen's own button seeds no conversation, so the third argument (#856)
    # is explicitly None rather than absent — the job signature is one contract,
    # whichever route reached it.
    # The trailing `description` is the confirm card's wording, carried so the
    # ledger entry is written when the plan exists rather than when it was
    # asked for (#778). The screen's own button has no card, so it is None.
    assert enqueued["args"] == (str(user_id), str(plan_id), None, None)


def test_the_enqueue_carries_the_conversation_that_settled_the_plan():
    """#856: a plan agreed in a thread must tell the worker WHICH thread, or the
    draft it produces is a fresh plan rather than the one the runner confirmed."""
    from app.services.schedule.draft import enqueue_draft

    enqueued = {}

    class _FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            enqueued["args"] = args

    import app.core.queue as queue_mod

    original = queue_mod.queue
    queue_mod.queue = _FakeQueue()
    try:
        user_id, plan_id, thread_id = uuid4(), uuid4(), uuid4()
        enqueue_draft(user_id, plan_id, thread_id)
    finally:
        queue_mod.queue = original

    assert enqueued["args"] == (str(user_id), str(plan_id), str(thread_id), None)


def test_a_queue_that_is_down_leaves_a_row_the_runner_can_retry_rather_than_a_500(
    db, client, monkeypatch
):
    """Fire-and-forget: the request has already written the row, so a Redis hiccup
    must not turn a recorded draft into an error the runner cannot act on."""
    _act_as(_seed_user(db))

    attempts = []

    class _DeadQueue:
        def enqueue(self, *args, **kwargs):
            attempts.append(args)
            raise RuntimeError("redis is down")

    monkeypatch.setattr("app.core.queue.queue", _DeadQueue())
    # The autouse stub is replaced with the REAL enqueue for this test, so the
    # swallowing happens where it actually lives.
    from app.services.schedule import draft as draft_mod

    monkeypatch.setattr("app.api.schedule.enqueue_draft", draft_mod.enqueue_draft)

    resp = client.post("/api/schedule/draft")

    assert len(attempts) == 1
    assert resp.status_code == 202
    assert db.query(TrainingPlan).one().status == store.DRAFTING


# --- the job ----------------------------------------------------------------


def _run_job(db, user_id, plan_id):
    with patch.object(job_mod, "SessionLocal", return_value=db):
        job_mod.generate_schedule_job(str(user_id), str(plan_id))


def test_the_job_drafts_the_plan_and_leaves_it_alone_when_it_succeeds(db):
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _ok(session, acting_user, target_plan, **kwargs):
        target_plan.status = store.ACTIVE
        session.commit()
        return DraftOutcome(ok=True, plan_id=target_plan.id)

    with patch.object(job_mod, "draft_plan", _ok):
        _run_job(db, user.id, plan.id)

    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one().status == (
        store.ACTIVE
    )


def test_a_rejected_draft_leaves_a_failed_row_rather_than_a_runner_polling_forever(db):
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _rejected(session, acting_user, target_plan, **kwargs):
        return DraftOutcome(ok=False, failures=["week 2026-08-17 is in the past"])

    with patch.object(job_mod, "draft_plan", _rejected):
        _run_job(db, user.id, plan.id)

    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one().status == (
        store.FAILED
    )
    assert db.query(PlannedSession).count() == 0


def test_a_plan_that_is_gone_leaves_the_worker_alive(db):
    """Nothing to do is not an error: the row may have been deleted between the
    enqueue and the worker picking the job up."""
    user = _seed_user(db)

    with patch.object(job_mod, "draft_plan", None):
        _run_job(db, user.id, uuid4())          # no such plan
        _run_job(db, uuid4(), uuid4())          # no such user either


def test_a_drafting_run_that_throws_leaves_the_worker_alive_and_the_plan_failed(db):
    """The job's contract: it never crashes the worker, and it never leaves a
    runner polling a `drafting` row that will never move."""
    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _boom(session, acting_user, target_plan, **kwargs):
        raise RuntimeError("the generation exploded")

    with patch.object(job_mod, "draft_plan", _boom):
        _run_job(db, user.id, plan.id)

    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one().status == (
        store.FAILED
    )


def test_a_plan_that_does_not_belong_to_the_named_runner_is_refused(db):
    """The one place a plan id and a user id arrive as separate arguments."""
    owner = _seed_user(db)
    stranger = _seed_user(db)
    plan = store.create_drafting_plan(db, owner.id)
    calls = []

    async def _record(session, acting_user, target_plan, **kwargs):
        calls.append(target_plan.id)
        return DraftOutcome(ok=True, plan_id=target_plan.id)

    with patch.object(job_mod, "draft_plan", _record):
        _run_job(db, stranger.id, plan.id)

    assert calls == []
    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one().status == (
        store.DRAFTING
    )


def test_the_draft_a_runner_asked_for_is_the_one_the_job_is_handed(db, client, monkeypatch):
    """End to end across the seam: the endpoint's row id is what reaches the job."""
    user = _seed_user(db)
    _act_as(user)
    enqueued = []
    monkeypatch.setattr(
        "app.api.schedule.enqueue_draft",
        lambda user_id, plan_id: enqueued.append((user_id, plan_id)),
    )
    plan_id = UUID(client.post("/api/schedule/draft").json()["plan_id"])
    seen = []

    async def _record(session, acting_user, target_plan, **kwargs):
        seen.append((acting_user.id, target_plan.id))
        return DraftOutcome(ok=True, plan_id=target_plan.id)

    with patch.object(job_mod, "draft_plan", _record):
        _run_job(db, *enqueued[0])

    assert seen == [(user.id, plan_id)]


def test_the_week_the_runner_is_following_is_untouched_while_a_draft_fails(db, client):
    """A failed draft changes nothing the runner had."""
    user = _seed_user(db)
    active = _seed_plan(db, user, status="active", made_at=datetime(2026, 8, 1, 9, 0))
    db.add(
        PlannedSession(
            plan_id=active.id,
            user_id=user.id,
            window_start=date.today(),
            window_end=date.today() + timedelta(days=2),
            intent="easy",
            discipline="run",
            commitment="committed",
            title="Still mine",
            target_distance_m=8000,
        )
    )
    db.commit()
    plan = store.create_drafting_plan(db, user.id)

    async def _rejected(session, acting_user, target_plan, **kwargs):
        return DraftOutcome(ok=False, failures=["nope"])

    with patch.object(job_mod, "draft_plan", _rejected):
        _run_job(db, user.id, plan.id)

    db.expire_all()
    # The job owns its own session in production and closes it here, detaching
    # every instance that came from the shared test session.
    _act_as(db.merge(user))
    week = client.get("/api/schedule/week").json()
    assert week["has_plan"] is True
    assert [s["title"] for s in week["sessions"]] == ["Still mine"]
    assert client.get("/api/schedule/draft").json()["status"] == "failed"


# --- regressions found during review of this slice --------------------------


def test_a_stale_drafting_row_does_not_block_the_feature_forever(db, client):
    """A draft nothing will ever finish must not wedge the runner permanently.

    `enqueue_draft` swallows enqueue errors by design, so a Redis hiccup or a
    worker that died before picking the job up leaves a `drafting` row. The guard
    returned that row on every later tap, so the only way back was a DB edit.
    """
    user = _seed_user(db)
    _act_as(user)
    stale = store.create_drafting_plan(db, user.id)
    stale.created_at = datetime.now(timezone.utc) - (
        store.DRAFT_STALE_AFTER + timedelta(minutes=1)
    )
    db.commit()

    with patch("app.api.schedule.enqueue_draft") as enqueue:
        body = client.post("/api/schedule/draft").json()

    assert enqueue.call_count == 1
    assert body["plan_id"] != str(stale.id)
    db.refresh(stale)
    assert stale.status == store.FAILED


def test_a_draft_still_within_the_window_is_returned_rather_than_restarted(db, client):
    """The idempotency guard still holds for a draft that is genuinely running."""
    user = _seed_user(db)
    _act_as(user)
    running = store.create_drafting_plan(db, user.id)

    with patch("app.api.schedule.enqueue_draft") as enqueue:
        body = client.post("/api/schedule/draft").json()

    assert enqueue.call_count == 0
    assert body["plan_id"] == str(running.id)


def test_the_in_flight_guard_does_not_depend_on_which_row_sorted_first(db):
    """Asked as its own query, not read off `latest_plan`.

    `created_at` is a transaction timestamp on Postgres and one-second resolution
    on SQLite, so same-second rows tie. The guard is the gate on a BILLED
    operation, and a wrong answer starts a second concurrent draft.
    """
    user = _seed_user(db)
    stamp = datetime.now(timezone.utc)
    drafting = store.create_drafting_plan(db, user.id)
    other = store.create_drafting_plan(db, user.id)
    store.activate_plan(db, other)
    drafting.created_at = stamp
    other.created_at = stamp
    db.commit()

    assert store.draft_in_flight(db, user.id).id == drafting.id
