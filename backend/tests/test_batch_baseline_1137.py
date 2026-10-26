"""#1137: the bulk analysis loops recompute the runner baseline ONCE per batch.

`/api/sync`, the historical Strava import and the stream backfill used to call
`analyze()` with its default per-activity baseline recompute, redoing the
182-day windowed scan for every activity. They now pass `skip_baseline=True`
and recompute once after the loop (as `reanalyze_history` already did, #366).

Oracle: the per-activity path itself. After each batch path runs, the baseline
it left is compared column for column with the one the old path produces on the
same data: drop the row, re-run `analyze()` per activity with its default
recompute, and read the row again. Analysis never reads `RunnerBaseline`, so the
re-run re-derives identical metrics and only the recompute cadence differs.
"""

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models import Activity, RunnerBaseline, StravaAccount, StravaImport, User
from app.models.user_profile import UserProfile
from app.services.analysis import analyze
from app.services.analysis.baseline import recompute_runner_baseline
from app.services.strava_ingestion import InMemoryStravaAdapter, set_strava_port

_N = 10
_COLUMNS = (
    "typical_easy_pace_s_per_km",
    "typical_easy_hr",
    "typical_long_run_duration_s",
    "typical_weekly_distance_m",
    "typical_weekly_run_count",
    "typical_efficiency",
    "bucketed_trends",
    "sample_count",
)


@pytest.fixture
def strava_adapter():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


def _seed_user(db):
    user = User(email=f"bl-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=5,
                       max_hr=190, resting_hr=50))
    account = StravaAccount(
        user_id=user.id, strava_athlete_id=int(uuid4().int % 10**9),
        access_token="t", refresh_token="r", expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return user, account


def _raws(base: datetime, first_id: int = 7_000_000) -> list[dict]:
    raws = []
    for i in range(_N):
        start = base - timedelta(days=2 * i, hours=i % 3)
        raws.append({
            "id": first_id + i,
            "name": f"Run {i}",
            "type": "Run",
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "distance": 6000 + 900 * (i % 4),
            "moving_time": 1900 + 270 * (i % 4) + 7 * i,
            "elapsed_time": 2000 + 270 * (i % 4) + 7 * i,
            "total_elevation_gain": 20.0 + i,
            "average_heartrate": 138 + (i % 5) * 3,
            "max_heartrate": 165 + i % 4,
            "average_speed": 3.0 + (i % 4) * 0.12,
        })
    return raws


def _seed_streams(adapter, raws) -> None:
    for i, raw in enumerate(raws):
        n = 120
        adapter.seed_streams(raw["id"], {
            "time": {"data": [t * 15 for t in range(n)]},
            "heartrate": {"data": [130 + i % 5 + t * 0.15 for t in range(n)]},
            "distance": {"data": [t * 15 * raw["average_speed"] for t in range(n)]},
            "velocity_smooth": {"data": [raw["average_speed"] + (t % 7) * 0.01 for t in range(n)]},
        })


def _snapshot(db, user) -> dict | None:
    db.expire_all()
    row = db.query(RunnerBaseline).filter(RunnerBaseline.user_id == user.id).first()
    if row is None:
        return None
    return {c: getattr(row, c) for c in _COLUMNS}


def _per_activity_oracle(db, user) -> dict | None:
    db.query(RunnerBaseline).filter(RunnerBaseline.user_id == user.id).delete()
    db.commit()
    ids = [a.id for a in db.query(Activity).filter(Activity.user_id == user.id)
           .order_by(Activity.start_date.desc())]
    for activity_id in ids:
        analyze(db, str(activity_id))  # the old default: baseline per activity
    return _snapshot(db, user)


def _assert_same_as_oracle(db, user):
    batch = _snapshot(db, user)
    assert batch is not None, "the batch left no baseline: the once-per-batch recompute did not run"
    assert batch["sample_count"] and batch["sample_count"] >= 4
    assert batch == _per_activity_oracle(db, user)


def test_sync_endpoint_baseline_matches_per_activity_path(db, strava_adapter):
    from app.api.activities import sync_activities
    from app.jobs import backfill_streams as bf

    # A plain def, so FastAPI runs the blocking ingest + analysis in its
    # threadpool rather than on the event loop.
    assert not inspect.iscoroutinefunction(sync_activities)

    user, account = _seed_user(db)
    strava_adapter.seed_activities(_raws(datetime.now(timezone.utc) - timedelta(hours=1)))
    with patch.object(bf, "queue", MagicMock()), \
            patch("app.services.analysis._orchestrator.recompute_runner_baseline",
                  wraps=recompute_runner_baseline) as spy:
        stats = sync_activities(db=db, account=account)

    assert stats.analyzed == _N
    assert spy.call_count == 1
    _assert_same_as_oracle(db, user)


@pytest.mark.asyncio
async def test_import_batch_baseline_matches_per_activity_path(db, strava_adapter):
    from app.jobs.strava_import import run_import_batch

    user, _account = _seed_user(db)
    strava_adapter.seed_activities(_raws(datetime(2026, 3, 1, 9, tzinfo=timezone.utc)))
    imp = StravaImport(user_id=user.id, since_date=datetime(2025, 1, 1).date(),
                       status="running", cursor_page=1, activities_imported=0)
    db.add(imp)
    db.commit()

    with patch("app.services.analysis._orchestrator.recompute_runner_baseline",
               wraps=recompute_runner_baseline) as spy:
        result = await run_import_batch(db, imp, limit=50)

    assert result.imported == _N
    assert spy.call_count == 1  # once per batch, not once per activity
    _assert_same_as_oracle(db, user)


@pytest.mark.asyncio
async def test_backfill_batch_baseline_matches_per_activity_path(db, strava_adapter, monkeypatch):
    from app.jobs import backfill_streams as bf
    from app.services.strava_ingestion import strava_budget
    from app.services.strava_ingestion.persistence import upsert_activity

    monkeypatch.setattr(strava_budget, "over_budget", lambda: False)
    user, _account = _seed_user(db)
    raws = _raws(datetime(2026, 3, 1, 9, tzinfo=timezone.utc))
    for raw in raws:  # imported summary-only, streams not yet fetched
        upsert_activity(db, raw, user.id)
    db.commit()
    _seed_streams(strava_adapter, raws)

    with patch("app.services.analysis._orchestrator.recompute_runner_baseline",
               wraps=recompute_runner_baseline) as spy:
        result = await bf.backfill_streams_batch(db, limit=50, user_id=str(user.id))

    assert len(result.processed) == _N
    assert spy.call_count == 1  # once per batch, not once per activity
    _assert_same_as_oracle(db, user)


@pytest.mark.asyncio
async def test_unscoped_backfill_recomputes_every_owner_once(db, strava_adapter, monkeypatch):
    """Production backfill runs unscoped (user_id=None) across runners: each owner
    in the batch gets exactly one recompute, and each matches the old path."""
    from app.jobs import backfill_streams as bf
    from app.services.strava_ingestion import strava_budget
    from app.services.strava_ingestion.persistence import upsert_activity

    monkeypatch.setattr(strava_budget, "over_budget", lambda: False)
    owners = []
    for first_id in (7_000_000, 8_000_000):
        user, _account = _seed_user(db)
        raws = _raws(datetime(2026, 3, 1, 9, tzinfo=timezone.utc), first_id)
        for raw in raws:
            upsert_activity(db, raw, user.id)
        db.commit()
        _seed_streams(strava_adapter, raws)
        owners.append(user)

    with patch("app.services.analysis._orchestrator.recompute_runner_baseline",
               wraps=recompute_runner_baseline) as spy:
        result = await bf.backfill_streams_batch(db, limit=50, user_id=None)

    assert len(result.processed) == 2 * _N
    assert spy.call_count == 2
    for user in owners:
        _assert_same_as_oracle(db, user)
