"""#946: `period_report_store` — identity, staleness, and the async lifecycle
writes.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models import User
from app.services.coach import period_report_store as store

TODAY = date(2026, 8, 10)


def _seed_user(db) -> User:
    user = User(email=f"period-report-store-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_disciplines_key_is_order_and_case_independent():
    assert store.disciplines_key(["Run", "Ride"]) == store.disciplines_key(["ride", "run"])
    assert store.disciplines_key([]) == "all"
    assert store.disciplines_key(None) == "all"
    assert store.disciplines_key(["Run", "run", " Run "]) == "run"


def test_create_generating_report_stamps_the_identity(db):
    user = _seed_user(db)
    report = store.create_generating_report(
        db,
        user.id,
        period_start=TODAY,
        period_end=TODAY + timedelta(days=6),
        disciplines=["Ride", "Run"],
        prompt_id="period_report_v1",
        schema_version="1.0",
    )
    assert report.status == store.GENERATING
    assert report.disciplines_key == "ride,run" or report.disciplines_key == store.disciplines_key(["Ride", "Run"])
    assert report.disciplines_key == store.disciplines_key(["Ride", "Run"])


def test_report_in_flight_finds_a_matching_generating_row(db):
    user = _seed_user(db)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    found = store.report_in_flight(
        db, user.id, period_start=TODAY, period_end=TODAY,
        disciplines_key="all", prompt_id="p", schema_version="1",
    )
    assert found is not None
    assert found.id == report.id


def test_report_in_flight_ignores_a_different_identity(db):
    user = _seed_user(db)
    store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    found = store.report_in_flight(
        db, user.id, period_start=TODAY, period_end=TODAY + timedelta(days=1),
        disciplines_key="all", prompt_id="p", schema_version="1",
    )
    assert found is None


def test_a_stale_generating_row_is_marked_failed_and_not_in_flight(db):
    user = _seed_user(db)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    report.created_at = datetime.now(timezone.utc) - store.STALE_AFTER - timedelta(minutes=1)
    db.commit()

    found = store.report_in_flight(
        db, user.id, period_start=TODAY, period_end=TODAY,
        disciplines_key="all", prompt_id="p", schema_version="1",
    )

    assert found is None
    db.refresh(report)
    assert report.status == store.FAILED


def test_find_ready_only_matches_ready_rows(db):
    user = _seed_user(db)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    assert store.find_ready(
        db, user.id, period_start=TODAY, period_end=TODAY,
        disciplines_key="all", prompt_id="p", schema_version="1",
    ) is None

    store.mark_ready(
        db, report, content={"message": "x"}, context_pack={}, model_id="m",
    )

    found = store.find_ready(
        db, user.id, period_start=TODAY, period_end=TODAY,
        disciplines_key="all", prompt_id="p", schema_version="1",
    )
    assert found is not None
    assert found.id == report.id


def test_mark_failed_records_the_category(db):
    user = _seed_user(db)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.mark_failed(db, report, "the coach could not be reached", kind=store.FAILURE_UNREACHABLE)
    db.refresh(report)
    assert report.status == store.FAILED
    assert report.meta["failure_kind"] == store.FAILURE_UNREACHABLE
    assert report.meta["failure_reason"] == "the coach could not be reached"


def test_get_owned_report_is_tenant_scoped(db):
    a = _seed_user(db)
    b = _seed_user(db)
    report = store.create_generating_report(
        db, a.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    assert store.get_owned_report(db, report.id, a.id) is not None
    assert store.get_owned_report(db, report.id, b.id) is None


def test_list_reports_is_newest_first_and_tenant_scoped(db):
    a = _seed_user(db)
    b = _seed_user(db)
    # Two DISTINCT identities (different period_end), so this pins ordering and
    # tenant scoping independent of the dedup behaviour pinned separately below.
    r1 = store.create_generating_report(
        db, a.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    r1.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    r2 = store.create_generating_report(
        db, a.id, period_start=TODAY, period_end=TODAY + timedelta(days=1), disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.create_generating_report(
        db, b.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )

    rows = store.list_reports(db, a.id)
    assert [r.id for r in rows] == [r2.id, r1.id]


def test_list_reports_shows_at_most_one_row_per_identity(db):
    """#946 review: a retry after a failure (or the stale-timeout resurrection
    scenario, see `test_a_late_completion_cannot_resurrect_a_reassigned_row`)
    legitimately leaves more than one physical row for the same identity. The
    list must show only the newest — an old failed attempt behind the one that
    actually landed is not a second report."""
    user = _seed_user(db)
    failed_attempt = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.mark_failed(db, failed_attempt, "the coach could not be reached", kind=store.FAILURE_UNREACHABLE)
    successful_retry = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.mark_ready(db, successful_retry, content={"message": "x"}, context_pack={}, model_id="m")
    # `created_at` is `server_default=func.now()` — one-second resolution on the
    # SQLite test database (the `TrainingPlan.latest_plan` precedent) — so two
    # rows created microseconds apart in a fast test can tie, leaving the `id`
    # tiebreaker (deliberately arbitrary-but-stable, not recency-correct) to
    # decide. Stamped explicitly here so THIS test pins ordering by genuine
    # recency rather than by accident of random UUID comparison.
    failed_attempt.created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    successful_retry.created_at = datetime.now(timezone.utc)
    db.commit()

    rows = store.list_reports(db, user.id)

    assert [r.id for r in rows] == [successful_retry.id]


# --- claim_identity (#946 review: concurrent identical POSTs) ----------------


def test_claim_identity_acquires_via_an_atomic_redis_set_nx(db):
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    with patch("app.core.queue.redis_conn", fake_redis):
        claimed = store.claim_identity(
            uuid4(), period_start=TODAY, period_end=TODAY,
            disciplines_key="all", prompt_id="p", schema_version="1",
        )
    assert claimed is True
    assert fake_redis.set.call_args.kwargs == {"nx": True, "ex": store.CLAIM_TTL_SECONDS}


def test_claim_identity_refuses_when_the_slot_is_already_held(db):
    fake_redis = MagicMock()
    fake_redis.set.return_value = False  # SET NX EX: key already exists
    with patch("app.core.queue.redis_conn", fake_redis):
        claimed = store.claim_identity(
            uuid4(), period_start=TODAY, period_end=TODAY,
            disciplines_key="all", prompt_id="p", schema_version="1",
        )
    assert claimed is False


def test_claim_identity_degrades_closed_on_a_redis_outage(db):
    """#946 review's explicit posture: an unsent report is recoverable, a
    doubled generation is not, so a coordination outage REFUSES rather than
    letting two generations through — the opposite of self_heal's
    degrade_open=True."""
    fake_redis = MagicMock()
    fake_redis.set.side_effect = ConnectionError("redis is down")
    with patch("app.core.queue.redis_conn", fake_redis):
        claimed = store.claim_identity(
            uuid4(), period_start=TODAY, period_end=TODAY,
            disciplines_key="all", prompt_id="p", schema_version="1",
        )
    assert claimed is False


def test_claim_identity_keys_on_the_full_identity_not_just_the_user(db):
    """Two DIFFERENT requests from the same runner must never contend for the
    same claim — only two requests for the SAME identity should."""
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    user_id = uuid4()
    with patch("app.core.queue.redis_conn", fake_redis):
        store.claim_identity(
            user_id, period_start=TODAY, period_end=TODAY,
            disciplines_key="all", prompt_id="p", schema_version="1",
        )
        store.claim_identity(
            user_id, period_start=TODAY, period_end=TODAY + timedelta(days=1),
            disciplines_key="all", prompt_id="p", schema_version="1",
        )
    keys = [call.args[0] for call in fake_redis.set.call_args_list]
    assert keys[0] != keys[1]
    assert str(user_id) in keys[0] and str(user_id) in keys[1]


# --- mark_ready / mark_failed compare-and-set (#946 review) -------------------


def test_mark_ready_is_a_no_op_when_the_row_is_no_longer_generating(db):
    """The row was already settled (by a stale-timeout retry, or by a concurrent
    writer) by the time this caller tries to write — the write must be
    discarded, not applied over the top."""
    user = _seed_user(db)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.mark_failed(db, report, "already decided", kind=store.FAILURE_UNKNOWN)

    result = store.mark_ready(
        db, report, content={"message": "a late, discarded answer"},
        context_pack={}, model_id="m",
    )

    assert result is None
    db.refresh(report)
    assert report.status == store.FAILED
    assert report.report is None  # the late content never landed


def test_mark_failed_is_a_no_op_when_the_row_is_no_longer_generating(db):
    user = _seed_user(db)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.mark_ready(db, report, content={"message": "the real answer"}, context_pack={}, model_id="m")

    result = store.mark_failed(db, report, "a late, spurious failure", kind=store.FAILURE_UNKNOWN)

    assert result is None
    db.refresh(report)
    assert report.status == store.READY
    assert report.report == {"message": "the real answer"}


def test_a_late_completion_cannot_resurrect_a_reassigned_row(db):
    """The exact interleaving the review proved: a genuinely slow job passes
    STALE_AFTER, a legitimate retry finds the row stale and marks it failed
    (via `report_in_flight`) and starts a second generation, and THEN the
    original (still-running) job finally completes. Without the CAS fix, that
    late completion blindly flipped the row back to `ready` behind the retry's
    back. With it, the late result is discarded and the row the retry decided
    on (failed, in this case) stays exactly as the retry left it."""
    user = _seed_user(db)
    identity = dict(
        period_start=TODAY, period_end=TODAY,
        disciplines_key="all", prompt_id="p", schema_version="1",
    )
    original = store.create_generating_report(
        db, user.id, disciplines=[],
        period_start=TODAY, period_end=TODAY, prompt_id="p", schema_version="1",
    )
    original.created_at = datetime.now(timezone.utc) - store.STALE_AFTER - timedelta(minutes=1)
    db.commit()

    # A retry's poll finds the row stale and marks it failed — the row is now
    # reassigned; a NEW row would be created for a genuine runner retry, but
    # that is orthogonal to what this test pins.
    in_flight = store.report_in_flight(db, user.id, **identity)
    assert in_flight is None  # correctly treated as not in flight any more
    db.refresh(original)
    assert original.status == store.FAILED

    # The ORIGINAL job — which had no idea any of that happened — now finishes
    # and tries to write its real (successful) result onto the SAME in-memory
    # `original` object it has held the whole time.
    result = store.mark_ready(
        db, original, content={"message": "the original, now-late answer"},
        context_pack={}, model_id="m",
    )

    assert result is None  # discarded, not stored
    db.refresh(original)
    assert original.status == store.FAILED  # left exactly as the retry decided
    assert original.report is None
