"""#974: a failed period report says when it failed.

Observed 25 Aug 15:20: the runner opened the Jul 25 to Aug 24 report and read
"Your coach could not be reached ... try again in a few minutes". No request had
been made; the row had failed 17 hours earlier, from a cause since fixed. A
failed row persists, so the read now carries when it failed, and no failure
sentence implies the failure is recent.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.api.period_reports import _FAILURE_MESSAGES
from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import User
from app.services.coach import period_report_store as store
from app.services.coach.period_report import PROMPT_ID, SCHEMA_VERSION

JUL_25 = date(2026, 7, 25)
AUG_24 = date(2026, 8, 24)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _runner(db) -> User:
    user = User(email=f"period-974-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    app.dependency_overrides[verify_clerk_session] = lambda: user
    return user


def _report(db, user):
    return store.create_generating_report(
        db, user.id, period_start=JUL_25, period_end=AUG_24, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )


def _as_utc(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def test_a_failed_report_says_when_it_failed_on_the_read_and_the_list(db, client):
    user = _runner(db)
    report = _report(db, user)
    before = datetime.now(timezone.utc)
    store.mark_failed(db, report, "upstream 529", kind=store.FAILURE_UNREACHABLE)

    read = client.get(f"/api/coach/period-reports/{report.id}").json()
    listed = client.get("/api/coach/period-reports").json()[0]

    assert read["failed_at"] is not None
    assert _as_utc(read["failed_at"]) >= before - timedelta(seconds=1)
    assert listed["failed_at"] == read["failed_at"]


def test_a_row_that_failed_before_the_stamp_existed_falls_back_to_when_it_was_asked(
    db, client
):
    user = _runner(db)
    report = _report(db, user)
    store.mark_failed(db, report, "upstream 529", kind=store.FAILURE_UNREACHABLE)
    report.meta = {k: v for k, v in report.meta.items() if k != "failed_at"}
    db.commit()

    read = client.get(f"/api/coach/period-reports/{report.id}").json()

    assert _as_utc(read["failed_at"]) == _as_utc(read["created_at"])


def test_a_report_that_did_not_fail_carries_no_failure_time(db, client):
    user = _runner(db)
    report = _report(db, user)

    read = client.get(f"/api/coach/period-reports/{report.id}").json()

    assert read["status"] == "generating"
    assert read["failed_at"] is None


@pytest.mark.parametrize("phrase", ["few minutes", "just now", "a moment"])
def test_no_failure_sentence_claims_the_failure_is_recent(phrase):
    for sentence in _FAILURE_MESSAGES.values():
        assert phrase not in sentence
