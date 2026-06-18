"""#339 (Fork 2): chat is a relationship-level thread, not per-activity silos.

Storage stays per-activity (no migration); at read time the chat system prompt
carries a bounded, user-scoped digest of the runner's recent chat turns from their
OTHER activities, so a turn continues the ongoing conversation instead of starting
fresh per run.

Disciplines pinned here:
- a turn sees prior conversation from another of the SAME runner's activities,
- the digest is bounded (turn cap),
- it is user-scoped (never another runner's chat — the security boundary),
- it excludes THIS activity (already loaded as the per-activity history),
- and the prompt is byte-stable when there is no other-activity chat.
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport
from app.services.coach.chat import (
    MEDICAL_REDIRECT_MESSAGE,
    _MAX_CROSS_ACTIVITY_CHARS,
    _MAX_CROSS_ACTIVITY_TURNS,
    _build_chat_system_prompt,
    _build_cross_activity_block,
)

_athlete_seq = iter(range(1000, 100000))


def _user(db) -> User:
    u = User(email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.commit()
    db.add(UserProfile(
        user_id=u.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=u.id, strava_athlete_id=next(_athlete_seq),
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    db.commit()
    return u


def _activity(db, user, *, strava_id, day) -> Activity:
    a = Activity(
        user_id=user.id, strava_activity_id=strava_id,
        start_date=datetime(2026, 5, day, 10, 0, 0), type="Run", name=f"Run {day}",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(a)
    db.commit()
    db.add(DerivedMetric(
        activity_id=a.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.add(CoachReport(
        activity_id=a.id, report={"key_takeaways": [{"text": "ok"}]},
        meta={}, context_pack={}, prompt_id="x", schema_version=1, is_fallback=False,
    ))
    db.commit()
    db.refresh(a)
    return a


def _chat(db, activity, role, content):
    db.add(CoachChatMessage(activity_id=activity.id, role=role, content=content))
    db.commit()


def _capture_system(client, activity) -> str:
    """POST a chat turn with a mocked LLM and return the system prompt it received."""
    captured = {}

    async def fake_stream(self, system, messages, max_tokens=1024):
        captured["system"] = system
        yield "ok"

    with patch("app.services.coach.llm.AnthropicClient.stream_chat", new=fake_stream):
        resp = client.post(
            f"/api/activities/{activity.id}/coach-chat",
            json={"message": "How did I do?"},
        )
    assert resp.status_code == 200
    return captured["system"]


def test_chat_sees_prior_conversation_from_another_activity(client, db):
    """A turn on a new run carries the runner's recent chat from an earlier run."""
    u = _user(db)
    old = _activity(db, u, strava_id=101, day=20)
    new = _activity(db, u, strava_id=102, day=27)
    _chat(db, old, "user", "Should I add a second long run this week?")
    _chat(db, old, "assistant",
          "Hold off on a second long run for now; build the first one's consistency.")

    system = _capture_system(client, new)

    assert "RELATIONSHIP CONVERSATION" in system
    assert "Hold off on a second long run" in system


def test_cross_activity_history_is_user_scoped(client, db):
    """The security boundary: a turn only ever sees the SAME runner's chat, never
    another runner's. Scoped to the activity owner's user_id."""
    u1, u2 = _user(db), _user(db)
    u1_old = _activity(db, u1, strava_id=201, day=20)
    u1_new = _activity(db, u1, strava_id=202, day=27)
    u2_act = _activity(db, u2, strava_id=203, day=21)
    _chat(db, u1_old, "assistant", "U1HISTORY keep your easy days easy.")
    _chat(db, u2_act, "assistant", "U2PRIVATE add tempo work on Thursdays.")

    system = _capture_system(client, u1_new)

    assert "U1HISTORY" in system       # the same runner's history threads forward
    assert "U2PRIVATE" not in system   # another runner's chat never leaks in


def test_current_activity_thread_is_not_in_the_cross_activity_block(client, db):
    """This activity's own prior chat is loaded as the per-activity history (the
    `messages` array), so it must NOT also appear in the system prompt's
    cross-activity block — no double-counting."""
    u = _user(db)
    act = _activity(db, u, strava_id=301, day=20)
    _chat(db, act, "assistant", "OWNTHREAD this is the current activity's own chat.")

    system = _capture_system(client, act)

    assert "OWNTHREAD" not in system  # belongs to the per-activity history, not the block


def test_cross_activity_block_is_bounded(db):
    """The digest is capped at the turn limit, so the injected history cannot grow
    unbounded as the relationship accumulates."""
    u = _user(db)
    cur = _activity(db, u, strava_id=401, day=27)
    other = _activity(db, u, strava_id=402, day=20)
    for i in range(_MAX_CROSS_ACTIVITY_TURNS + 5):
        _chat(db, other, "user", f"question number {i}")

    block = _build_cross_activity_block(db, cur)
    entries = [ln for ln in block.splitlines() if ln.startswith("[")]
    assert len(entries) == _MAX_CROSS_ACTIVITY_TURNS


def test_cross_activity_block_truncates_long_messages(db):
    """A single long message is truncated, so one verbose turn cannot blow the token
    budget."""
    u = _user(db)
    cur = _activity(db, u, strava_id=411, day=27)
    other = _activity(db, u, strava_id=412, day=20)
    long_msg = "x" * (_MAX_CROSS_ACTIVITY_CHARS + 200)
    _chat(db, other, "assistant", long_msg)

    block = _build_cross_activity_block(db, cur)
    assert long_msg not in block and "…" in block


def test_cross_activity_block_skips_error_and_redirect_noise(db):
    """Error / safe-redirect sentinels are not real conversation and must not thread
    forward into another run's context."""
    u = _user(db)
    cur = _activity(db, u, strava_id=501, day=27)
    other = _activity(db, u, strava_id=502, day=20)
    _chat(db, other, "assistant", MEDICAL_REDIRECT_MESSAGE)
    _chat(db, other, "assistant", "Sorry, I encountered an error. Please try again.")
    _chat(db, other, "assistant", "REALADVICE ease back to easy mileage this week.")

    block = _build_cross_activity_block(db, cur)
    assert "REALADVICE" in block
    assert MEDICAL_REDIRECT_MESSAGE not in block
    assert "I encountered an error" not in block


def test_no_cross_activity_chat_keeps_prompt_byte_stable(db):
    """A runner with no other-activity chat gets an empty block, and the assembled
    prompt is byte-identical to the no-block prompt (the voice_block-style idiom)."""
    u = _user(db)
    solo = _activity(db, u, strava_id=601, day=20)
    _chat(db, solo, "user", "only this activity has chat")

    assert _build_cross_activity_block(db, solo) == ""

    with_default = _build_chat_system_prompt({}, {}, {}, {}, [])
    with_empty = _build_chat_system_prompt({}, {}, {}, {}, [], cross_activity_block="")
    assert with_default == with_empty
    assert "RELATIONSHIP CONVERSATION (recent chats" not in with_empty
