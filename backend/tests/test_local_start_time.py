"""The runner's local wall-clock start (#399).

start_date stays UTC for ordering/windowing; start_date_local (Strava's
start_date_local) is what the coach states as the time of day and what
trends/load bucket the calendar day by. Readers fall back to start_date when
start_date_local is absent (pre-#399 rows / payloads missing it).
"""

import uuid
from datetime import datetime, timezone

from types import SimpleNamespace

from app.models import Activity, DerivedMetric
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.strava_ingestion.ingestion import upsert_activity
from app.services.trends import ActivityFact


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _raw(strava_id, *, with_local):
    raw = {
        "id": strava_id,
        "name": "Morning Run",
        "type": "Run",
        "start_date": "2026-06-20T08:00:18Z",  # UTC
        "distance": 5000, "moving_time": 1800, "elapsed_time": 1900,
        "total_elevation_gain": 10.0, "average_speed": 2.78,
    }
    if with_local:
        raw["start_date_local"] = "2026-06-20T09:00:18Z"  # local wall clock (+1h)
    return raw


# --- ingestion ----------------------------------------------------------------


def test_ingestion_stores_local_start(db):
    uid = _user(db)
    act = upsert_activity(db, _raw(111, with_local=True), uid)
    db.flush()
    assert act.start_date_local == datetime(2026, 6, 20, 9, 0, 18)  # naive local
    assert act.start_date == datetime(2026, 6, 20, 8, 0, 18)        # UTC unchanged
    assert act.local_start == datetime(2026, 6, 20, 9, 0, 18)


def test_ingestion_without_local_falls_back(db):
    uid = _user(db)
    act = upsert_activity(db, _raw(222, with_local=False), uid)
    db.flush()
    assert act.start_date_local is None
    assert act.local_start == act.start_date  # property falls back to UTC


def test_resync_backfills_local_start(db):
    """A first sync without the field, then a re-sync that carries it, populates it."""
    uid = _user(db)
    upsert_activity(db, _raw(333, with_local=False), uid)
    db.flush()
    act = upsert_activity(db, _raw(333, with_local=True), uid)
    db.flush()
    assert act.start_date_local == datetime(2026, 6, 20, 9, 0, 18)


# --- coach context (the reported bug) -----------------------------------------


def _activity_with_local(db, uid, *, start_utc, start_local):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=start_utc, start_date_local=start_local,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a); db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort="easy", duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=3.0,
        confidence="high", confidence_reasons=[], flags=[],
    ))
    db.flush()
    return a


def test_coach_pack_states_local_time(db):
    """The LLM-facing activity date is the runner's 09:00, not the 08:00 UTC."""
    uid = _user(db)
    act = _activity_with_local(
        db, uid,
        start_utc=datetime(2026, 6, 20, 8, 0, 18, tzinfo=timezone.utc),
        start_local=datetime(2026, 6, 20, 9, 0, 18),
    )
    pack = build_context_pack(db, act)
    assert pack.activity.date.startswith("2026-06-20T09:00:18")


def test_coach_pack_falls_back_without_local(db):
    uid = _user(db)
    act = _activity_with_local(
        db, uid,
        start_utc=datetime(2026, 6, 20, 8, 0, 18, tzinfo=timezone.utc),
        start_local=None,
    )
    pack = build_context_pack(db, act)
    assert pack.activity.date.startswith("2026-06-20T08:00:18")


# --- trends/load day bucketing ------------------------------------------------


def test_trends_buckets_on_local_day_across_midnight():
    """A 23:30 UTC run whose local time is 00:30 the next day buckets locally."""
    row = SimpleNamespace(
        id=uuid.uuid4(),
        start_date=datetime(2026, 6, 20, 23, 30, tzinfo=timezone.utc),
        start_date_local=datetime(2026, 6, 21, 0, 30),
        type="Run", user_intent=None,
        distance_m=5000, moving_time_s=1800, elapsed_time_s=1900,
        elev_gain_m=10.0, avg_hr=150.0, avg_cadence=170.0, average_speed_mps=2.78,
        effort_score=10.0, effort="easy", time_in_zones=None,
    )
    fact = ActivityFact.from_row(row)
    assert fact.local_date == datetime(2026, 6, 21).date()


def test_trends_bucket_falls_back_without_local():
    row = SimpleNamespace(
        id=uuid.uuid4(),
        start_date=datetime(2026, 6, 20, 23, 30, tzinfo=timezone.utc),
        start_date_local=None,
        type="Run", user_intent=None,
        distance_m=5000, moving_time_s=1800, elapsed_time_s=1900,
        elev_gain_m=10.0, avg_hr=150.0, avg_cadence=170.0, average_speed_mps=2.78,
        effort_score=10.0, effort="easy", time_in_zones=None,
    )
    fact = ActivityFact.from_row(row)
    assert fact.local_date == datetime(2026, 6, 20).date()
