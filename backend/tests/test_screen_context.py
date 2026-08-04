"""Slice 3 of #764 (#767): screen context — a server-resolved pointer.

The client names which screen and which selections; the server resolves that
into a screen view with the same builders that produced the screen (ADR 0028).
Selections are inputs; numbers are facts: nothing numeric is accepted from the
client. Resolution is owner-scoped — a pointer naming another runner's data
resolves to nothing. Only the CURRENT screen's view reaches the coach; past
turns keep the label of where they were asked.

All row data is synthetic test setup.
"""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.thread import Thread
from tests._chat_stubs import chat_turn_stub


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


def _seed_user(db, *, athlete_id: int = 42, calibrated: bool = True) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    profile = UserProfile(
        user_id=user.id,
        goal_type="general",
        experience_level="intermediate",
        weekly_days_available=4,
    )
    if calibrated:
        profile.max_hr = 190
        profile.max_hr_source = "user"
        profile.hr_zones = [95, 114, 133, 152, 171]
        profile.hr_zones_source = "strava"
    db.add(profile)
    db.add(
        StravaAccount(
            user_id=user.id,
            strava_athlete_id=athlete_id,
            access_token="t",
            refresh_token="r",
            expires_at=9999999999,
            scope="read",
        )
    )
    db.commit()
    return user


def _seed_activity(db, user, *, strava_id=1000, name="Morning run") -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_id,
        start_date=datetime(2026, 7, 30, 8, 0, 0, tzinfo=timezone.utc),
        type="Run",
        name=name,
        distance_m=8200,
        moving_time_s=2850,
        elapsed_time_s=2900,
        elev_gain_m=40.0,
        avg_hr=141,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(
        DerivedMetric(
            activity_id=activity.id,
            effort="easy",
            structure="continuous",
            duration_class="standard",
            effort_score=55.0,
            flags=[],
            confidence="medium",
            confidence_reasons=[],
        )
    )
    db.commit()
    db.refresh(activity)
    return activity


def _post_turn(client, payload, deltas=("Okay.",), capture=None):
    with patch(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        new=chat_turn_stub(list(deltas), capture=capture),
    ):
        resp = client.post("/api/coach/threads/messages", json=payload)
    assert resp.status_code == 200
    resp.read()
    return resp


class TestScreenResolution:
    def test_activity_pointer_resolves_to_server_facts(self, client, db):
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        capture = {}
        _post_turn(
            client,
            {
                "message": "how was this one?",
                "screen": {"screen": "activity", "activity_id": str(activity.id)},
            },
            capture=capture,
        )
        system = capture["system"]
        assert "LOOKING AT" in system
        # Facts are server-derived: the resolved view carries the run's own
        # numbers (8.2 km), not anything the client sent.
        assert "8.2" in system
        # The stored provenance label derives from the pointer.
        from app.models.coach_chat_message import CoachChatMessage

        rows = db.query(CoachChatMessage).all()
        assert all(r.asked_from == "activity" for r in rows)

    def test_cross_user_activity_pointer_resolves_to_nothing(self, client, db):
        user = _seed_user(db)
        other = User(email=f"other-{uuid4()}@example.com")
        db.add(other)
        db.commit()
        others_activity = _seed_activity(db, other, strava_id=2000, name="Not yours")
        _act_as(user)
        capture = {}
        _post_turn(
            client,
            {
                "message": "how was this one?",
                "screen": {"screen": "activity", "activity_id": str(others_activity.id)},
            },
            capture=capture,
        )
        # The turn still streams; the view is simply absent (and so is the
        # other runner's data).
        assert "Not yours" not in capture["system"]
        assert "LOOKING AT" not in capture["system"]

    def test_trends_pointer_resolves_via_volume_builder(self, client, db):
        user = _seed_user(db)
        _seed_activity(db, user)
        _act_as(user)
        capture = {}
        _post_turn(
            client,
            {
                "message": "why is this dropping?",
                "screen": {"screen": "trends", "range": "30D"},
            },
            capture=capture,
        )
        system = capture["system"]
        assert "LOOKING AT" in system
        assert "Trends" in system
        # The resolved trends view is the volume-vs-norm read.
        assert "rolling" in system

    def test_identity_only_screens_carry_no_view(self, client, db):
        user = _seed_user(db)
        _act_as(user)
        capture = {}
        _post_turn(
            client,
            {"message": "hello", "screen": {"screen": "load"}},
            capture=capture,
        )
        # Load contributes identity only (the baseline already carries its
        # content, ADR 0028); the coach still learns WHERE the runner is.
        assert "LOOKING AT" in capture["system"]
        assert "Load" in capture["system"]

    def test_invalid_pointer_is_rejected_before_the_stream(self, client, db):
        user = _seed_user(db)
        _act_as(user)
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "x", "screen": {"screen": "not-a-screen"}},
        )
        assert resp.status_code == 422
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "x", "screen": {"screen": "trends", "range": "99Y"}},
        )
        assert resp.status_code == 422

    def test_one_live_view_never_two(self, client, db):
        """Turn 2 from another screen carries ONLY that screen's view; the
        earlier screen survives as the stored label, not as data."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        first = {}
        _post_turn(
            client,
            {
                "message": "how was this one?",
                "screen": {"screen": "activity", "activity_id": str(activity.id)},
            },
            capture=first,
        )
        thread = db.query(Thread).one()
        second = {}
        _post_turn(
            client,
            {
                "message": "and my trends?",
                "thread_id": str(thread.id),
                "screen": {"screen": "trends", "range": "30D"},
            },
            capture=second,
        )
        assert "Trends" in second["system"]
        # The activity view does not ride along a second time.
        assert "8.2" not in second["system"].split("LOOKING AT")[-1]
        from app.models.coach_chat_message import CoachChatMessage

        labels = [r.asked_from for r in db.query(CoachChatMessage).order_by(CoachChatMessage.created_at)]
        assert "activity" in labels and "trends" in labels


class TestResourcedPolicyFloor:
    """#767: the conversational policy rules bind without a stored report pack,
    re-sourced from the turn's own facts (ADR 0028 — the floor gets stronger,
    not weaker, for being moved off a stored artifact)."""

    def test_zone_language_gated_by_profile_calibration(self):
        from app.services.coach.chat import _validate_conversational_text

        violations = _validate_conversational_text(
            "Keep it in Z2 for most of the week.",
            zones_calibrated=False,
            sessions_in_play=[],
        )
        assert any(v.rule == "uncalibrated_zone_reference" for v in violations)
        # Calibrated runner: the same sentence is fine.
        assert not _validate_conversational_text(
            "Keep it in Z2 for most of the week.",
            zones_calibrated=True,
            sessions_in_play=[],
        )

    def test_interval_claims_gated_by_sessions_in_play(self):
        from app.services.coach.chat import _validate_conversational_text

        text = "You executed 8 intervals cleanly there."
        low = [{"detection_confidence": "low", "source": None}]
        assert any(
            v.rule == "ungated_interval_claim"
            for v in _validate_conversational_text(
                text, zones_calibrated=True, sessions_in_play=low
            )
        )
        # Ground-truth structure (recorded laps, high confidence): allowed.
        high = [{"detection_confidence": "high", "source": "recorded_laps"}]
        assert not _validate_conversational_text(
            text, zones_calibrated=True, sessions_in_play=high
        )

    def test_fetched_session_without_structure_gates_claims(self):
        from app.services.coach.chat import sessions_in_play_from_tool_results

        sessions = sessions_in_play_from_tool_results(
            [
                ("get_session_detail", {"activity_id": "x", "interval": None}),
                ("get_training_summary", {"totals": {}}),
                ("get_session_detail", {"error": "not_found"}),
            ]
        )
        assert sessions == [{"detection_confidence": "low"}]

    def test_thread_turn_medical_floor_still_unconditional(self, client, db):
        user = _seed_user(db, calibrated=False)
        _act_as(user)
        resp_capture = {}
        resp = None
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_turn_stub(
                ["That sounds like a stress fracture. You have a stress fracture, so take 800mg ibuprofen."],
                capture=resp_capture,
            ),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={"message": "my shin hurts", "screen": {"screen": "home"}},
            )
        assert resp.status_code == 200
        body = resp.text
        # The raw overreach never reaches the runner; the safe redirect does.
        assert "stress fracture" not in body
        assert "clinician" in body

    def test_profile_zones_calibration_helper(self, db):
        from app.services.coach.context import zones_calibration

        user = _seed_user(db, calibrated=False)
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        calibrated, basis = zones_calibration(profile)
        assert calibrated is False and basis == "uncalibrated"
        profile.hr_zones = [95, 114, 133, 152, 171]
        calibrated, basis = zones_calibration(profile)
        assert calibrated is True and basis == "strava_zones"
