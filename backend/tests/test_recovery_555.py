"""Daily recovery store, Garmin adapter, sync job and read endpoint (#555).

Oracle notes. The Garmin payloads below are hand-written to the shape of Garmin
Connect's wellness endpoints (the raw JSON `garminconnect` returns from
`get_sleep_data`, `get_hrv_data`, `get_rhr_day`). The library does not model the
JSON, so these shapes are NOT verified against a live account; the parser is
tested to be tolerant of absence precisely because of that. The store, sync and
endpoint assertions do not depend on Garmin's shape at all.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.jobs import garmin_sync
from app.models import RecoveryDay, User
from app.services.recovery import store
from app.services.recovery.garmin_adapter import (
    TOKENS_REDIS_KEY,
    GarminRecoverySource,
    parse_garmin_day,
)
from app.services.recovery.port import RecoveryAuthError, RecoveryReading
from app.services.recovery.sync import sync_recent

DAY = date(2026, 10, 7)

SLEEP = {
    "dailySleepDTO": {
        "calendarDate": "2026-10-07",
        "sleepTimeSeconds": 27000,
        "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
    },
    "avgOvernightHrv": 51.0,
    "hrvStatus": "BALANCED",
    "restingHeartRate": 50,
}
HRV = {
    "hrvSummary": {
        "calendarDate": "2026-10-07",
        "lastNightAvg": 54,
        "weeklyAvg": 52,
        "status": "BALANCED",
        "baseline": {"balancedLow": 47, "balancedUpper": 60},
    }
}
RHR = {
    "allMetrics": {
        "metricsMap": {
            "WELLNESS_RESTING_HEART_RATE": [{"value": 49.0, "calendarDate": "2026-10-07"}]
        }
    }
}


# ---- parser -------------------------------------------------------------


def test_parse_reads_every_signal():
    r = parse_garmin_day(DAY, SLEEP, HRV, RHR)
    assert r.sleep_duration_s == 27000
    assert r.sleep_score == 82
    assert r.hrv_avg_ms == 54.0  # the HRV endpoint wins over the sleep summary
    assert (r.hrv_status, r.hrv_baseline_low_ms, r.hrv_baseline_high_ms) == ("BALANCED", 47.0, 60.0)
    assert r.resting_hr == 49  # the RHR endpoint wins over the sleep summary


def test_parse_falls_back_to_the_sleep_summary():
    r = parse_garmin_day(DAY, SLEEP, None, None)
    assert (r.hrv_avg_ms, r.resting_hr, r.hrv_status) == (51.0, 50, "BALANCED")


def test_parse_treats_absent_and_zero_as_not_measured():
    watch_off = {"dailySleepDTO": {"sleepTimeSeconds": 0}, "restingHeartRate": 0}
    r = parse_garmin_day(DAY, watch_off, {}, {"allMetrics": None})
    assert r == RecoveryReading(day=DAY)
    assert not r.has_signal()


def test_parse_survives_garbage_types():
    r = parse_garmin_day(DAY, {"dailySleepDTO": "x"}, [], {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": "no"}}})
    assert r == RecoveryReading(day=DAY)


# ---- store --------------------------------------------------------------


def _user(db, email="owner@x.dev"):
    u = User(email=email)
    db.add(u)
    db.commit()
    return u


def test_upsert_is_idempotent_per_user_day_source(db):
    u = _user(db)
    store.upsert_reading(db, u.id, "garmin", RecoveryReading(day=DAY, sleep_score=70, hrv_avg_ms=40.0))
    db.commit()
    store.upsert_reading(db, u.id, "garmin", RecoveryReading(day=DAY, sleep_score=82))
    db.commit()
    rows = db.query(RecoveryDay).filter_by(user_id=u.id).all()
    assert len(rows) == 1
    assert rows[0].sleep_score == 82
    assert rows[0].hrv_avg_ms is None  # the corrected fetch withdrew it


def test_a_second_source_is_a_second_row(db):
    u = _user(db)
    store.upsert_reading(db, u.id, "garmin", RecoveryReading(day=DAY, sleep_score=70))
    store.upsert_reading(db, u.id, "other", RecoveryReading(day=DAY, sleep_score=60))
    db.commit()
    assert db.query(RecoveryDay).count() == 2


# ---- sync ---------------------------------------------------------------


class FakeSource:
    source = "garmin"

    def __init__(self, readings=None, fail_days=(), auth_fail_on=None):
        self.readings = readings or {}
        self.fail_days = set(fail_days)
        self.auth_fail_on = auth_fail_on
        self.calls = []

    def fetch_day(self, day):
        self.calls.append(day)
        if day == self.auth_fail_on:
            raise RecoveryAuthError("expired")
        if day in self.fail_days:
            raise RuntimeError("boom")
        return self.readings.get(day, RecoveryReading(day=day))


def test_sync_writes_signal_nights_and_skips_empty_and_failed_ones(db):
    u = _user(db)
    d = lambda n: DAY - timedelta(days=n)  # noqa: E731
    src = FakeSource(
        readings={d(0): RecoveryReading(day=d(0), sleep_score=80), d(2): RecoveryReading(day=d(2), resting_hr=48)},
        fail_days=[d(1)],
    )
    res = sync_recent(db, u.id, src, today=DAY, days=3)
    assert (res.written, res.skipped_empty, res.failed) == (2, 0, 1)
    assert sorted(r.day for r in db.query(RecoveryDay).all()) == [d(2), d(0)]


def test_sync_stops_on_auth_failure(db):
    u = _user(db)
    src = FakeSource(auth_fail_on=DAY - timedelta(days=1))
    res = sync_recent(db, u.id, src, today=DAY, days=5)
    assert res.auth_failed
    assert len(src.calls) == 4  # days 4,3,2 then the failing day 1; day 0 never asked


# ---- adapter (library faked) --------------------------------------------

LONG = "t" * 600


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v, **kw):
        self.data[k] = v
        return True


class FakeGarmin:
    class client:  # noqa: N801
        @staticmethod
        def dumps():
            return "REFRESHED" + "r" * 600

    def __init__(self, accept=(LONG,), payloads=None, raise_on=None):
        self.accept = accept
        self.payloads = payloads or {}
        self.raise_on = raise_on
        self.logins = []

    def login(self, tokenstore=None):
        self.logins.append(tokenstore)
        if tokenstore not in self.accept:
            raise RuntimeError("secret-detail-" + tokenstore)

    def get_sleep_data(self, d):
        return self._get("sleep", d, SLEEP)

    def get_hrv_data(self, d):
        return self._get("hrv", d, HRV)

    def get_rhr_day(self, d):
        return self._get("rhr", d, RHR)

    def _get(self, name, d, default):
        if self.raise_on and self.raise_on[0] == name:
            raise self.raise_on[1]
        return self.payloads.get(name, default)


class GarminConnectAuthenticationError(Exception):
    pass


def _src(fake, redis=None, env=LONG):
    return GarminRecoverySource(env, redis or FakeRedis(), client_factory=lambda: fake, sleep=lambda s: None)


def test_adapter_logs_in_fetches_and_caches_the_refreshed_token():
    redis = FakeRedis()
    fake = FakeGarmin()
    r = _src(fake, redis).fetch_day(DAY)
    assert r.sleep_score == 82 and r.hrv_avg_ms == 54.0 and r.resting_hr == 49
    assert fake.logins == [LONG]
    assert redis.data[TOKENS_REDIS_KEY].startswith("REFRESHED")


def test_adapter_prefers_the_redis_token_then_falls_back_to_env():
    redis = FakeRedis({TOKENS_REDIS_KEY: "stale" + "s" * 600})
    fake = FakeGarmin(accept=(LONG,))
    _src(fake, redis).fetch_day(DAY)
    assert fake.logins == ["stale" + "s" * 600, LONG]


def test_adapter_without_any_accepted_token_raises_auth_error_and_never_leaks_it(caplog):
    fake = FakeGarmin(accept=())
    with caplog.at_level("DEBUG"):
        with pytest.raises(RecoveryAuthError) as ei:
            _src(fake).fetch_day(DAY)
    assert LONG not in str(ei.value)
    assert LONG not in caplog.text and "secret-detail" not in caplog.text


def test_adapter_rejects_a_value_too_short_to_be_a_dump():
    # The library would read a short string as a file PATH; never hand it one.
    fake = FakeGarmin(accept=("short",))
    with pytest.raises(RecoveryAuthError):
        _src(fake, env="short").fetch_day(DAY)
    assert fake.logins == []


def test_adapter_maps_a_mid_run_auth_error_and_tolerates_a_missing_endpoint():
    with pytest.raises(RecoveryAuthError):
        _src(FakeGarmin(raise_on=("hrv", GarminConnectAuthenticationError("401")))).fetch_day(DAY)
    r = _src(FakeGarmin(raise_on=("hrv", RuntimeError("404")))).fetch_day(DAY)
    assert r.hrv_avg_ms == 51.0  # sleep summary still supplies it


# ---- job ----------------------------------------------------------------


def test_next_run_is_the_next_configured_hour():
    before = datetime(2026, 10, 7, 5, 30, tzinfo=timezone.utc)
    after = datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)
    assert garmin_sync.seconds_until_next_run(before, 7) == 90 * 60
    assert garmin_sync.seconds_until_next_run(after, 7) == 24 * 3600


def test_job_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GARMIN_SYNC_ENABLED", False)
    monkeypatch.setattr(garmin_sync, "SessionLocal", lambda: pytest.fail("touched the DB"))
    monkeypatch.setattr(garmin_sync, "_schedule_next", lambda *a: pytest.fail("rescheduled"))
    assert garmin_sync.start_chain_if_needed() is False
    garmin_sync.garmin_sync_job()


def test_first_run_backfills_then_daily_runs_look_back_three_days(db, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@x.dev")
    monkeypatch.setattr(settings, "GARMIN_BACKFILL_DAYS", 30)
    owner = _user(db, "owner@x.dev")
    src = FakeSource(readings={DAY: RecoveryReading(day=DAY, sleep_score=80)})
    garmin_sync.run_garmin_sync(db, source=src, today=DAY)
    assert len(src.calls) == 30
    src2 = FakeSource()
    garmin_sync.run_garmin_sync(db, source=src2, today=DAY)
    assert len(src2.calls) == 3
    assert owner.id


def test_nothing_is_fetched_for_a_non_owner_deployment(db, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@x.dev")
    _user(db, "someone-else@x.dev")
    src = FakeSource()
    assert garmin_sync.run_garmin_sync(db, source=src, today=DAY) is None
    assert src.calls == []


def test_chain_survives_a_failed_run(monkeypatch):
    monkeypatch.setattr(settings, "GARMIN_SYNC_ENABLED", True)
    scheduled = []

    class Boom:
        def rollback(self): ...
        def close(self): ...

    monkeypatch.setattr(garmin_sync, "SessionLocal", lambda: Boom())
    monkeypatch.setattr(garmin_sync, "run_garmin_sync", lambda db: 1 / 0)
    monkeypatch.setattr(garmin_sync, "_schedule_next", lambda *a: scheduled.append(1))
    garmin_sync.garmin_sync_job()
    assert scheduled == [1]


# ---- endpoint -----------------------------------------------------------


def test_endpoint_returns_only_the_callers_nights_newest_first(client, db):
    from app.core.clerk_auth import require_current_user
    from app.main import app

    me, other = _user(db, "me@x.dev"), _user(db, "other@x.dev")
    today = datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    for n in (0, 1, 40):
        db.add(RecoveryDay(user_id=me.id, day=today - timedelta(days=n), source="garmin", sleep_score=80 - n, fetched_at=now))
    db.add(RecoveryDay(user_id=other.id, day=today, source="garmin", sleep_score=1, fetched_at=now))
    db.commit()
    app.dependency_overrides[require_current_user] = lambda: me
    try:
        body = client.get("/api/recovery?days=30").json()
        assert [d["sleep_score"] for d in body["days"]] == [80, 79]  # 40 days old is out, other's is out
        assert client.get("/api/recovery?days=0").status_code == 422
        assert client.get("/api/recovery?days=366").status_code == 422
    finally:
        app.dependency_overrides.pop(require_current_user, None)
