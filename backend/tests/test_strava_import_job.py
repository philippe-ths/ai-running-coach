"""Historical Strava import: resumable, paced, raw-data-only backfill (#239).

Covers the backward time-walk via the `before` cursor, dedup re-runnability,
deterministic analysis without the coach pipeline, the no-notification
guarantee, completion on a short page, the self-pacing re-enqueue, and the
idempotent enqueue.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models import Activity, StravaAccount, StravaImport, User
from app.services.strava_ingestion import InMemoryStravaAdapter, set_strava_port


@pytest.fixture
def strava_adapter():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


def _seed_user_and_account(db, athlete_id: int = 555):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=athlete_id,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return user, account


def _raw(strava_id: int, start: datetime, name: str = "Old run") -> dict:
    return {
        "id": strava_id,
        "name": name,
        "type": "Run",
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "distance": 8000,
        "moving_time": 2400,
        "elapsed_time": 2400,
        "total_elevation_gain": 30.0,
        "average_heartrate": 145,
        "max_heartrate": 165,
        "average_speed": 3.3,
    }


def _make_import(db, user, since_date=date(2025, 1, 1), cursor_page=1) -> StravaImport:
    imp = StravaImport(
        user_id=user.id,
        since_date=since_date,
        status="running",
        cursor_page=cursor_page,
        activities_imported=0,
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)
    return imp


@pytest.mark.asyncio
async def test_batch_imports_summaries_and_completes_on_short_page(db, strava_adapter):
    from app.jobs.strava_import import run_import_batch

    user, _account = _seed_user_and_account(db)
    strava_adapter.seed_activities(
        [
            _raw(101, datetime(2025, 3, 1, 9, tzinfo=timezone.utc)),
            _raw(102, datetime(2025, 2, 1, 9, tzinfo=timezone.utc)),
        ]
    )
    imp = _make_import(db, user)

    result = await run_import_batch(db, imp, limit=50)

    assert result.imported == 2
    assert result.done is True
    db.refresh(imp)
    assert imp.status == "completed"
    assert imp.activities_imported == 2
    # Both activities were persisted (summary-only ingest).
    assert db.query(Activity).filter(Activity.user_id == user.id).count() == 2


@pytest.mark.asyncio
async def test_full_page_advances_page_cursor_and_is_not_done(db, strava_adapter):
    from app.jobs.strava_import import run_import_batch

    user, _account = _seed_user_and_account(db)
    older = datetime(2025, 2, 1, 9, tzinfo=timezone.utc)
    newer = datetime(2025, 3, 1, 9, tzinfo=timezone.utc)
    strava_adapter.seed_activities([_raw(201, newer), _raw(202, older)])
    imp = _make_import(db, user)

    # limit=2 == page size, so the page is "full" -> more history assumed.
    result = await run_import_batch(db, imp, limit=2)

    assert result.done is False
    db.refresh(imp)
    assert imp.status == "running"
    # Cursor advanced to the next page.
    assert imp.cursor_page == 2


@pytest.mark.asyncio
async def test_page_walk_covers_whole_window_without_repeating(db, strava_adapter):
    """Walking page by page imports every activity exactly once and completes.

    Regression for the one-page stop: with `after` set, Strava returns
    oldest-first, so a `before = oldest` cursor would have ended after page 1.
    Page-number pagination must keep going.
    """
    from app.jobs.strava_import import run_import_batch

    user, _account = _seed_user_and_account(db)
    a = datetime(2025, 3, 1, 9, tzinfo=timezone.utc)
    b = datetime(2025, 2, 1, 9, tzinfo=timezone.utc)
    c = datetime(2025, 1, 15, 9, tzinfo=timezone.utc)
    strava_adapter.seed_activities([_raw(301, a), _raw(302, b), _raw(303, c)])
    imp = _make_import(db, user, since_date=date(2025, 1, 1))

    first = await run_import_batch(db, imp, limit=2)
    assert first.imported == 2
    assert first.done is False
    db.refresh(imp)
    assert imp.cursor_page == 2

    second = await run_import_batch(db, imp, limit=2)
    # Page 2 holds the remaining one activity; the short page completes the import.
    assert second.imported == 1
    assert second.done is True
    db.refresh(imp)
    assert imp.status == "completed"
    assert imp.activities_imported == 3
    assert db.query(Activity).filter(Activity.user_id == user.id).count() == 3


@pytest.mark.asyncio
async def test_import_is_rerunnable_via_dedup(db, strava_adapter):
    """Re-importing the same window upserts rather than duplicating activities."""
    from app.jobs.strava_import import run_import_batch

    user, _account = _seed_user_and_account(db)
    strava_adapter.seed_activities(
        [_raw(401, datetime(2025, 3, 1, 9, tzinfo=timezone.utc), name="First")]
    )

    imp1 = _make_import(db, user)
    await run_import_batch(db, imp1, limit=50)

    # Same activity, edited name; a fresh import should update, not duplicate.
    strava_adapter.seed_activities(
        [_raw(401, datetime(2025, 3, 1, 9, tzinfo=timezone.utc), name="Renamed")]
    )
    imp2 = _make_import(db, user)
    await run_import_batch(db, imp2, limit=50)

    rows = db.query(Activity).filter(Activity.strava_activity_id == 401).all()
    assert len(rows) == 1
    assert rows[0].name == "Renamed"


@pytest.mark.asyncio
async def test_import_never_notifies_and_writes_no_coach_report(db, strava_adapter):
    from app.jobs.strava_import import run_import_batch
    from app.services.notifications import InMemoryNotifier, set_notifier

    notifier = InMemoryNotifier()
    set_notifier(notifier)
    try:
        user, _account = _seed_user_and_account(db)
        strava_adapter.seed_activities(
            [_raw(501, datetime(2025, 3, 1, 9, tzinfo=timezone.utc))]
        )
        imp = _make_import(db, user)

        await run_import_batch(db, imp, limit=50)

        assert notifier.sent == []
        activity = db.query(Activity).filter(Activity.strava_activity_id == 501).first()
        assert activity is not None
        assert activity.coach_notification_sent_at is None
        assert activity.opener_notification_sent_at is None
    finally:
        set_notifier(None)


@pytest.mark.asyncio
async def test_batch_fails_gracefully_without_account(db, strava_adapter):
    from app.jobs.strava_import import run_import_batch

    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    imp = _make_import(db, user)

    result = await run_import_batch(db, imp, limit=50)

    assert result.done is True
    db.refresh(imp)
    assert imp.status == "failed"
    assert "No linked Strava account" in (imp.error or "")


def test_job_schedules_next_batch_when_not_done():
    from app.jobs import strava_import as mod

    fake_db = MagicMock()
    running = MagicMock()
    running.status = "running"
    fake_db.get.return_value = running
    result = mod.ImportBatchResult(imported=50, done=False)
    with patch.object(mod, "SessionLocal", return_value=fake_db), patch.object(
        mod, "run_import_batch", new=AsyncMock(return_value=result)
    ), patch.object(mod, "_schedule_next_batch") as sched:
        mod.strava_import_job(str(uuid4()))

    sched.assert_called_once()


def test_job_does_not_reschedule_when_done():
    from app.jobs import strava_import as mod

    fake_db = MagicMock()
    running = MagicMock()
    running.status = "running"
    fake_db.get.return_value = running
    result = mod.ImportBatchResult(imported=3, done=True)
    with patch.object(mod, "SessionLocal", return_value=fake_db), patch.object(
        mod, "run_import_batch", new=AsyncMock(return_value=result)
    ), patch.object(mod, "_schedule_next_batch") as sched:
        mod.strava_import_job(str(uuid4()))

    sched.assert_not_called()


def test_enqueue_import_creates_row_and_enqueues(db):
    from app.jobs import strava_import as mod

    _user, account = _seed_user_and_account(db)
    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        imp = mod.enqueue_import(db, account, date(2025, 1, 1))

    assert imp.status == "running"
    assert imp.since_date == date(2025, 1, 1)
    fake_queue.enqueue.assert_called_once()
    _args, kwargs = fake_queue.enqueue.call_args
    assert kwargs["job_id"] == f"strava_import_{imp.id}"


def test_enqueue_import_is_idempotent_while_running(db):
    from app.jobs import strava_import as mod

    _user, account = _seed_user_and_account(db)
    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        first = mod.enqueue_import(db, account, date(2025, 1, 1))
        second = mod.enqueue_import(db, account, date(2024, 1, 1))

    assert first.id == second.id
    # Only the first call enqueued a job.
    fake_queue.enqueue.assert_called_once()


def test_endpoint_starts_import_and_status_reports_progress(client, db):
    from app.jobs import strava_import as mod

    _user, _account = _seed_user_and_account(db)
    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        resp = client.post("/api/strava/import", json={"since_date": "2025-01-01"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["since_date"] == "2025-01-01"
    fake_queue.enqueue.assert_called_once()

    status = client.get("/api/strava/import/status")
    assert status.status_code == 200
    assert status.json()["id"] == body["id"]


def test_endpoint_rejects_future_date(client, db):
    _user, _account = _seed_user_and_account(db)
    resp = client.post("/api/strava/import", json={"since_date": "2999-01-01"})
    assert resp.status_code == 400


def test_status_is_null_when_no_import(client, db):
    _seed_user_and_account(db)
    resp = client.get("/api/strava/import/status")
    assert resp.status_code == 200
    assert resp.json() is None
