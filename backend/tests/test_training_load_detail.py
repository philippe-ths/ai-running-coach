"""P3 detail-read projection (ADR 0016): GET /api/activities/{id} carries the
readiness read for the runner's card, projected at read time (the `laps` idiom),
independent of the active coach prompt."""

import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.models import Activity, DerivedMetric, User

_BASE = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _seed(db, n_days, daily_load=60.0):
    user = User(email=f"tld-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    last = None
    for i in range(n_days):
        when = _BASE + timedelta(days=i)
        a = Activity(
            user_id=user.id, strava_activity_id=int(when.timestamp()),
            start_date=when, type="Run", name="Run", distance_m=8000,
            moving_time_s=2400, elapsed_time_s=2400, elev_gain_m=10.0,
            avg_hr=150, average_speed_mps=3.0, raw_summary={},
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        db.add(DerivedMetric(
            activity_id=a.id, effort_score=daily_load, confidence="high",
            flags=[], confidence_reasons=[],
        ))
        db.commit()
        last = a
    return last


def test_detail_endpoint_includes_training_load(client, db):
    last = _seed(db, 60)
    resp = client.get(f"/api/activities/{last.id}")
    assert resp.status_code == 200, resp.text
    tl = resp.json()["training_load"]
    assert tl is not None
    assert tl["warming_up"] is False
    assert tl["fitness"] > 0 and tl["fatigue"] > 0
    assert tl["condition"] in {"fresh", "balanced", "fatigued", "overreaching"}
    assert tl["sample_count"] == 60


def test_detail_training_load_present_regardless_of_coach_prompt(client, db, monkeypatch):
    # The readiness model is always computable; the COACH_PROMPT_ID gate governs only
    # the coach surface, not this read-path projection.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")  # non-v6
    last = _seed(db, 10)
    resp = client.get(f"/api/activities/{last.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["training_load"] is not None


def test_detail_training_load_warming_up_thin_history(client, db):
    last = _seed(db, 2)
    resp = client.get(f"/api/activities/{last.id}")
    assert resp.status_code == 200, resp.text
    tl = resp.json()["training_load"]
    assert tl["warming_up"] is True
    assert tl["condition"] == "building_baseline"
