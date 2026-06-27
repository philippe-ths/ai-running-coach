"""User-triggered self-healing replaces polling (#123, ADR 0006).

Covers the bound (rapid taps collapse to one check), the diff (only webhook-missed
activities are enqueued), tenant scoping (a user's check uses only their own
account and history), and graceful degradation (no account / Strava down).
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.jobs import self_heal
from app.jobs.self_heal import _run_self_heal, maybe_enqueue_self_heal
from app.models import Activity, StravaAccount, User


def _seed_account(db, *, email, athlete_id, known_strava_ids=()):
    user = User(email=email)
    db.add(user)
    db.commit()
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=athlete_id, access_token="t",
        refresh_token="r", expires_at=1999999999, scope="read",
    ))
    for sid in known_strava_ids:
        db.add(Activity(
            user_id=user.id, strava_activity_id=sid,
            start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="run",
            distance_m=5000, moving_time_s=1500, elapsed_time_s=1500,
            elev_gain_m=10.0, avg_hr=140, raw_summary={},
        ))
    db.commit()
    return user


def _fake_port(returned_ids):
    port = MagicMock()
    port.list_recent_activities = AsyncMock(
        return_value=[{"id": sid} for sid in returned_ids]
    )
    return port


# --- the bound -----------------------------------------------------------------

def test_maybe_enqueue_self_heal_is_bounded():
    """Two rapid calls collapse to one enqueue: the atomic SET NX acquires once,
    then is throttled until the key expires."""
    fake_redis = MagicMock()
    fake_redis.set.side_effect = [True, None]  # first acquires, second throttled
    fake_queue = MagicMock()
    with patch.object(self_heal, "redis_conn", fake_redis), \
         patch.object(self_heal, "queue", fake_queue):
        first = maybe_enqueue_self_heal("user-1")
        second = maybe_enqueue_self_heal("user-1")
    assert first is True
    assert second is False
    fake_queue.enqueue.assert_called_once()
    # The bound key carries the configured TTL.
    from app.core.config import settings
    assert fake_redis.set.call_args.kwargs.get("ex") == settings.SELF_HEAL_MIN_INTERVAL_SECONDS


def test_maybe_enqueue_self_heal_never_raises_into_request():
    """A Redis failure degrades to False (no check) rather than breaking the
    request that triggered it — it is a best-effort safety net."""
    fake_redis = MagicMock()
    fake_redis.set.side_effect = RuntimeError("redis down")
    with patch.object(self_heal, "redis_conn", fake_redis):
        assert maybe_enqueue_self_heal("user-1") is False


# --- the diff ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_self_heal_enqueues_only_missing_activities(db):
    """Strava returns known + new ids; only the webhook-missed (new) ids enqueue
    the pipeline, with the existing dedup job_id."""
    user = _seed_account(db, email="a@heal.dev", athlete_id=1, known_strava_ids=[100, 101])
    fake_queue = MagicMock()
    with patch.object(self_heal, "get_strava_port", return_value=_fake_port([100, 101, 202, 203])), \
         patch.object(self_heal, "ensure_valid_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(self_heal, "queue", fake_queue):
        new_ids = await _run_self_heal(db=db, user_id=user.id)

    assert sorted(new_ids) == [202, 203]
    enqueued = {c.kwargs["strava_activity_id"] for c in fake_queue.enqueue.call_args_list}
    assert enqueued == {202, 203}  # the two known ids are NOT re-enqueued
    # dedup: each enqueue carries the deterministic per-activity job_id.
    job_ids = {c.kwargs["job_id"] for c in fake_queue.enqueue.call_args_list}
    assert job_ids == {"pipeline_self_heal_202", "pipeline_self_heal_203"}


@pytest.mark.asyncio
async def test_self_heal_scopes_to_the_users_own_account(db):
    """Tenant boundary: A's self-heal resolves A's account and enqueues with A's
    athlete id, never B's — even though both have linked accounts."""
    user_a = _seed_account(db, email="a@heal.dev", athlete_id=1)
    _seed_account(db, email="b@heal.dev", athlete_id=2, known_strava_ids=[500])
    fake_queue = MagicMock()
    with patch.object(self_heal, "get_strava_port", return_value=_fake_port([900])), \
         patch.object(self_heal, "ensure_valid_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(self_heal, "queue", fake_queue):
        new_ids = await _run_self_heal(db=db, user_id=user_a.id)

    assert new_ids == [900]
    assert fake_queue.enqueue.call_args.kwargs["strava_athlete_id"] == 1  # A's, not B's


@pytest.mark.asyncio
async def test_self_heal_no_account_is_noop(db):
    """A user with no linked Strava account heals nothing (no crash)."""
    user = User(email="noaccount@heal.dev")
    db.add(user)
    db.commit()
    with patch.object(self_heal, "queue", MagicMock()) as fake_queue:
        new_ids = await _run_self_heal(db=db, user_id=user.id)
    assert new_ids == []
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_self_heal_degrades_when_strava_unreachable(db):
    """A Strava/token failure logs and skips rather than raising — the safety net
    must not become a failure source."""
    user = _seed_account(db, email="a@heal.dev", athlete_id=1)
    bad_port = MagicMock()
    bad_port.list_recent_activities = AsyncMock(side_effect=RuntimeError("strava 500"))
    with patch.object(self_heal, "get_strava_port", return_value=bad_port), \
         patch.object(self_heal, "ensure_valid_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(self_heal, "queue", MagicMock()) as fake_queue:
        new_ids = await _run_self_heal(db=db, user_id=user.id)
    assert new_ids == []
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_self_heal_skips_when_strava_budget_exhausted(db, monkeypatch):
    """When the shared Strava budget is exhausted (#544), the self-heal yields:
    it spends no Strava call and enqueues nothing, leaving the scarce shared
    capacity for the live webhook path. The next app-open re-triggers it."""
    from app.services.strava_ingestion import strava_budget

    user = _seed_account(db, email="a@heal.dev", athlete_id=1)
    monkeypatch.setattr(strava_budget, "over_budget", lambda: True)
    port = _fake_port([900])
    with patch.object(self_heal, "get_strava_port", return_value=port), \
         patch.object(self_heal, "ensure_valid_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(self_heal, "queue", MagicMock()) as fake_queue:
        new_ids = await _run_self_heal(db=db, user_id=user.id)

    assert new_ids == []
    port.list_recent_activities.assert_not_called()  # no Strava call spent
    fake_queue.enqueue.assert_not_called()


# --- the endpoint --------------------------------------------------------------

def test_refresh_endpoint_triggers_bounded_check(client, db):
    _seed_account(db, email="a@heal.dev", athlete_id=1)
    with patch("app.jobs.self_heal.maybe_enqueue_self_heal", return_value=True) as heal:
        resp = client.post("/api/activities/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"status": "checking"}
    heal.assert_called_once()


def test_refresh_endpoint_no_account_is_graceful(client, db):
    resp = client.post("/api/activities/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_account"}
