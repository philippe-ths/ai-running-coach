"""#1137: the activity list and detail endpoints serialize with Pydantic's own JSON
encoder instead of `jsonable_encoder` + `JSONResponse`.

The old path walked ~40k stream floats in Python (~60ms of an ~80ms handler).
These tests pin that the bytes sent are IDENTICAL to what the old path produced,
on an activity with realistic non-round streams, metrics, laps and a check-in, so
the speed-up changes nothing the frontend reads. The cadence single-normalisation
guard (#684) is covered by test_cadence_normalization_684.py.
"""

import math
import random
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api import activities as activities_api
from app.models import Activity, ActivityStream, CheckIn, User
from app.models.user_profile import UserProfile
from app.schemas import ActivityDetailRead
from app.services import analysis

_BASE = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)


def _streams(n: int, rng: random.Random) -> dict:
    t = list(range(n))
    v = [2.8 + 0.3 * math.sin(i / 200) + rng.random() * 0.2 for i in t]
    d, acc = [], 0.0
    for x in v:
        acc += x
        d.append(round(acc, 1))
    return {
        "time": t,
        "distance": d,
        "velocity_smooth": v,
        "heartrate": [120 + min(40, i / 60) + rng.random() * 5 for i in t],
        "cadence": [85 + rng.random() * 3 for _ in t],
        "altitude": [50 + 10 * math.sin(i / 300) for i in t],
        "grade_smooth": [math.cos(i / 300) for i in t],
        "moving": [True] * n,
        "latlng": [[51.5 + i * 1e-5, -0.1 + i * 1e-5] for i in t],
        "temp": [15] * n,
    }


def _seed(db, n_acts: int = 3, samples: int = 1500) -> User:
    rng = random.Random(1137)
    user = User(email=f"enc-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=5,
                       max_hr=185, resting_hr=50))
    for i in range(n_acts):
        start = _BASE - timedelta(days=i * 1.3, hours=rng.random() * 3)
        activity = Activity(
            user_id=user.id, strava_activity_id=900_000_000 + i + uuid.uuid4().int % 100_000,
            start_date=start, start_date_local=start.replace(tzinfo=None),
            type="Run", name=f"Run {i} été", distance_m=int(samples * 2.9),
            moving_time_s=samples, elapsed_time_s=samples + 60, elev_gain_m=30.3,
            avg_hr=140.7, max_hr=170.0, avg_cadence=86.1, average_speed_mps=2.913,
            raw_summary={"sport_type": "Run", "average_temp": 15, "laps": []},
        )
        db.add(activity)
        db.commit()
        for kind, values in _streams(samples, rng).items():
            db.add(ActivityStream(activity_id=activity.id, stream_type=kind, data=values))
        db.add(CheckIn(activity_id=activity.id, rpe=5, pain_score=0, notes="fine — ok"))
        db.commit()
        analysis.analyze(db, str(activity.id))
    return user


def test_detail_bytes_identical_to_jsonable_encoder(client, db, monkeypatch):
    user = _seed(db, n_acts=1)
    activity = db.query(Activity).filter(Activity.user_id == user.id).one()

    captured = {}
    original = ActivityDetailRead.model_dump_json

    def _spy(self, *args, **kwargs):
        captured["model"] = self
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ActivityDetailRead, "model_dump_json", _spy)
    resp = client.get(f"/api/activities/{activity.id}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/json"

    model = captured["model"]
    assert len(model.streams) >= 9  # the fixture really carried streams
    old = JSONResponse(content=jsonable_encoder(model)).body
    assert resp.content == old


def test_list_bytes_identical_to_jsonable_encoder(client, db, monkeypatch):
    _seed(db, n_acts=3, samples=300)

    captured = {}
    adapter = activities_api._ACTIVITY_LIST_ADAPTER

    class _Spy:
        def dump_json(self, value):
            captured["models"] = value
            return adapter.dump_json(value)

    monkeypatch.setattr(activities_api, "_ACTIVITY_LIST_ADAPTER", _Spy())
    resp = client.get("/api/activities")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/json"

    models = captured["models"]
    assert len(models) == 3
    old = JSONResponse(content=jsonable_encoder(models)).body
    assert resp.content == old
