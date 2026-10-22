"""#1128: a period report whose job never finished reads as failed on its own.

The staleness sweep ran only when a new request for the same period arrived, so
a row left `generating` by a lost enqueue or a worker killed mid-job (a deploy)
said "Your coach is reviewing this stretch" on the list and the detail forever.
Reading it now settles it.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import User
from app.services.coach import period_report_store as store
from app.services.coach.period_report import PROMPT_ID, SCHEMA_VERSION


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _runner(db) -> User:
    user = User(email=f"period-1128-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    app.dependency_overrides[verify_clerk_session] = lambda: user
    return user


def _report(db, user, *, asked_ago: timedelta):
    report = store.create_generating_report(
        db, user.id, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
        disciplines=[], prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    report.created_at = datetime.now(timezone.utc) - asked_ago
    db.commit()
    return report


def _as_utc(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def test_a_report_abandoned_three_days_ago_reads_as_failed_with_no_new_request(db, client):
    report = _report(db, _runner(db), asked_ago=timedelta(days=3))

    read = client.get(f"/api/coach/period-reports/{report.id}").json()
    listed = client.get("/api/coach/period-reports").json()[0]

    assert read["status"] == listed["status"] == "failed"
    assert "reviewing" not in read["message"]
    # It failed when it went stale, not when someone looked.
    assert _as_utc(read["failed_at"]) == _as_utc(read["created_at"]) + store.STALE_AFTER
    assert listed["failed_at"] == read["failed_at"]


def test_the_list_alone_settles_it(db, client):
    report = _report(db, _runner(db), asked_ago=timedelta(days=3))

    listed = client.get("/api/coach/period-reports").json()[0]

    assert listed["status"] == "failed"
    db.refresh(report)
    assert report.status == store.FAILED


def test_a_report_still_inside_its_window_stays_in_progress(db, client):
    report = _report(db, _runner(db), asked_ago=timedelta(minutes=2))

    read = client.get(f"/api/coach/period-reports/{report.id}").json()

    assert read["status"] == "generating"
    assert read["failed_at"] is None


def test_a_failure_is_stamped_when_it_happened_not_when_it_was_asked(db, client):
    """Pins `failed_at` itself: with the stamp gone the read falls back to
    `created_at`, two days earlier."""
    report = _report(db, _runner(db), asked_ago=timedelta(minutes=10))
    report.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()
    before = datetime.now(timezone.utc)

    store.mark_failed(db, report, "upstream 529", kind=store.FAILURE_UNREACHABLE)
    read = client.get(f"/api/coach/period-reports/{report.id}").json()

    assert _as_utc(read["failed_at"]) >= before - timedelta(seconds=1)
