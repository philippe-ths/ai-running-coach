"""#400 training_volume pack section — gated emission + byte-stability + integration.

The section is emitted ONLY under a volume-aware prompt id (v9), and adds NOTHING
to the rest of the pack: the v9 pack minus training_volume is byte-identical to the
v8 pack. End-to-end, a deliberate easy week reaches the section as `down` vs norm.
"""

from datetime import date, datetime, time as dtime, timedelta, timezone
from uuid import uuid4

from app.api.profile import get_current_user_profile
from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import build_context_pack

V9 = "coach_message_v9"
V8 = "coach_message_v8"


def _user(db):
    user = User(email=f"vol-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=5, max_hr=190,
    ))
    db.commit()
    return user


def _add(db, user, when, *, type="Run", distance_m=10000, load=50.0):
    act = Activity(
        user_id=user.id, strava_activity_id=int(when.timestamp() * 1000) % 10**12 + uuid4().int % 1000,
        start_date=when, start_date_local=when.replace(tzinfo=None),
        type=type, name=type, distance_m=distance_m,
        moving_time_s=3600, elapsed_time_s=3600, elev_gain_m=10.0,
        avg_hr=150, average_speed_mps=2.78, raw_summary={},
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    db.add(DerivedMetric(
        activity_id=act.id, effort="easy", structure="continuous",
        effort_score=load, confidence="high", flags=[], confidence_reasons=[],
    ))
    db.commit()
    return act


def _seed_normal_then_easy(db):
    """12 prior weeks at 5 sessions/50km each, then a light current week (subject)."""
    user = _user(db)
    as_of = datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)  # Saturday
    baseline_end = as_of - timedelta(days=7)
    for week in range(12):
        wk = baseline_end - timedelta(days=week * 7)
        for offset in range(4):
            _add(db, user, wk - timedelta(days=offset))           # 4 runs
        _add(db, user, wk - timedelta(days=4), type="Walk")       # 1 walk
    # Current week: two light runs, the later one is the subject.
    _add(db, user, as_of - timedelta(days=2))
    subject = _add(db, user, as_of)
    return subject


def test_volume_emitted_only_under_v9(db):
    subject = _seed_normal_then_easy(db)
    pack9 = build_context_pack(db, subject, prompt_id=V9)
    pack8 = build_context_pack(db, subject, prompt_id=V8)
    packn = build_context_pack(db, subject)  # default path, no prompt id

    assert pack9.training_volume is not None
    assert pack8.training_volume is None
    assert packn.training_volume is None
    # Serialization: present under v9, absent (not even null) otherwise.
    assert "training_volume" in pack9.to_serializable_dict()
    assert "training_volume" not in pack8.to_serializable_dict()


def test_v9_pack_minus_volume_is_byte_identical_to_v8(db):
    subject = _seed_normal_then_easy(db)
    d9 = build_context_pack(db, subject, prompt_id=V9).to_serializable_dict()
    d8 = build_context_pack(db, subject, prompt_id=V8).to_serializable_dict()
    d9.pop("training_volume", None)
    assert d9 == d8  # the section adds nothing else to the pack


def test_easy_week_reaches_section_as_down(db):
    subject = _seed_normal_then_easy(db)
    vol = build_context_pack(db, subject, prompt_id=V9).training_volume
    assert vol.has_baseline is True
    by_metric = {m.metric: m for m in vol.rolling_7d.metrics}
    # 2 sessions / 20km this week vs a 5-session / 50km norm.
    assert by_metric["sessions"].direction == "down"
    assert by_metric["distance_m"].direction == "down"
    # Runs-only rides alongside the holistic total (here all current activities are runs).
    assert by_metric["sessions"].current_all == 2
    assert by_metric["sessions"].current_runs == 2


def test_volume_endpoint_returns_signal_as_of_today(client, db):
    """The Trends-page endpoint computes the same signal as of today (#400)."""
    uid = get_current_user_profile(db).user_id  # the user the endpoint resolves
    today = date.today()
    base = datetime.combine(today, dtime(9, 0), tzinfo=timezone.utc)
    baseline_end = base - timedelta(days=7)
    # 12 normal prior weeks, then a single light run today.
    for week in range(12):
        wk = baseline_end - timedelta(days=week * 7)
        for offset in range(4):
            _add(db, _U(uid), wk - timedelta(days=offset))
        _add(db, _U(uid), wk - timedelta(days=4), type="Walk")
    _add(db, _U(uid), base)

    resp = client.get("/api/trends/volume?range=7D")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["range"] == "7D"
    assert body["has_baseline"] is True
    assert body["rolling"]["label"] == "7-day rolling"
    assert body["calendar"]["label"] == "This week"
    rolling = {m["metric"]: m for m in body["rolling"]["metrics"]}
    assert rolling["sessions"]["direction"] == "down"  # 1 session vs the norm

    # The range drives the framing: 30D switches to month labels.
    resp30 = client.get("/api/trends/volume?range=30D")
    assert resp30.status_code == 200, resp30.text
    body30 = resp30.json()
    assert body30["range"] == "30D"
    assert body30["rolling"]["label"] == "30-day rolling"
    assert body30["calendar"]["label"] == "This month"


class _U:
    """A minimal stand-in carrying just the .id _add reads, to seed under an existing user."""
    def __init__(self, uid):
        self.id = uid
