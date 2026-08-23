"""#946: `period_report_store` — identity, staleness, and the async lifecycle
writes.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
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
    r1 = store.create_generating_report(
        db, a.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    r1.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    r2 = store.create_generating_report(
        db, a.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )
    store.create_generating_report(
        db, b.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id="p", schema_version="1",
    )

    rows = store.list_reports(db, a.id)
    assert [r.id for r in rows] == [r2.id, r1.id]
