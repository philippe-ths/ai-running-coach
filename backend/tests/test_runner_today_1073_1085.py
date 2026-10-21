"""#1073, #1085: "today" is the runner's date, not the server's.

The server runs on UTC. The snapshot's Melbourne runner (UTC+11 in October) was
a day out for eleven hours of every day: the chat's "this week" and a session
ticked by hand used the server's day while their sessions were bucketed by each
activity's local day. The runner's zone now comes from their latest activity,
the same source as that local day.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, User
from app.services.runner_clock import local_date, runner_today, runner_zone

# 22 Oct, 07:30 in Melbourne; still 21 Oct on the server.
SERVER_NOW = datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc)


def _runner(db, *, zone=None, offset_hours=None) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    if zone is not None or offset_hours is not None:
        start = datetime(2026, 10, 20, 6, 0)
        db.add(Activity(
            user_id=user.id, strava_activity_id=hash(user.email) % 10**9, type="Run",
            name="Run", start_date=start,
            start_date_local=start + timedelta(hours=offset_hours or 0),
            distance_m=5000, moving_time_s=1500, elapsed_time_s=1500,
            raw_summary={"timezone": zone} if zone else {},
        ))
        db.commit()
    return user


def test_a_melbourne_runner_is_already_on_tomorrow(db):
    user = _runner(db, zone="(GMT+10:00) Australia/Melbourne", offset_hours=11)

    assert runner_today(db, user.id, now=SERVER_NOW) == date(2026, 10, 22)


def test_the_zone_follows_daylight_saving_not_the_offset_it_was_recorded_at(db):
    """Recorded at +10:00 in winter, the zone is +11:00 by late October."""
    user = _runner(db, zone="(GMT+10:00) Australia/Melbourne", offset_hours=10)

    assert runner_today(db, user.id, now=datetime(2026, 10, 21, 13, 30, tzinfo=timezone.utc)) == date(2026, 10, 22)


def test_with_no_zone_name_the_activitys_own_offset_is_used(db):
    user = _runner(db, offset_hours=11)

    assert runner_today(db, user.id, now=SERVER_NOW) == date(2026, 10, 22)


def test_a_runner_with_no_activities_is_on_utc(db):
    user = _runner(db)

    assert runner_zone(db, user.id) == timezone.utc
    assert runner_today(db, user.id, now=SERVER_NOW) == date(2026, 10, 21)


def test_a_london_runner_late_in_the_evening_is_still_on_today(db):
    user = _runner(db, zone="(GMT+00:00) Europe/London", offset_hours=1)

    # 23:30 BST on the 21st is 22:30 UTC.
    assert runner_today(db, user.id, now=datetime(2026, 10, 21, 22, 30, tzinfo=timezone.utc)) == date(2026, 10, 21)


def test_a_stored_instant_reads_as_the_runners_day():
    melbourne = __import__("zoneinfo").ZoneInfo("Australia/Melbourne")

    assert local_date(datetime(2026, 10, 21, 20, 30), melbourne) == date(2026, 10, 22)


# --- where it is read ------------------------------------------------------------


def test_a_hand_tick_counts_on_the_runners_day_not_utcs(db):
    """#1085: 20:00 UTC on Tuesday is 06:00 Wednesday in Melbourne."""
    from tests.test_schedule_week import TUE, THU, WED, _seed_plan, _seed_session, _seed_user
    from app.services.schedule.week import build_week

    user = _seed_user(db)
    db.add(Activity(
        user_id=user.id, strava_activity_id=abs(hash(user.email)) % 10**9, type="Run",
        name="Run", start_date=datetime(2026, 8, 1, 21, 0),
        start_date_local=datetime(2026, 8, 2, 7, 0), distance_m=5000,
        moving_time_s=1500, elapsed_time_s=1500,
        raw_summary={"timezone": "(GMT+10:00) Australia/Melbourne"},
    ))
    db.commit()
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=TUE, end=THU, title="Walk",
        completed_at=datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc),
    )

    week = build_week(db, user, today=TUE)

    assert {s.title: s for s in week.sessions}["Walk"].done_on == WED


def test_the_chats_tools_resolve_windows_from_the_runners_date(client, db, monkeypatch):
    """#1073: every tool the chat runs is handed the runner's today."""
    from unittest.mock import patch

    from app.services import runner_clock
    from app.services.coach import chat
    from tests._chat_stubs import chat_tool_loop_stub
    from tests.test_chat_policy_validation import _seed

    class _ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return SERVER_NOW

    monkeypatch.setattr(runner_clock, "datetime", _ServerClock)
    activity = _seed(db)
    activity.start_date_local = activity.start_date + timedelta(hours=11)
    activity.raw_summary = {"timezone": "(GMT+11:00) Australia/Melbourne"}
    db.commit()
    seen = []
    real = chat.execute_chat_tool

    def _spy(db_, owner, name, tool_input, today=None):
        seen.append(today)
        return real(db_, owner, name, tool_input, today=today)

    monkeypatch.setattr(chat, "execute_chat_tool", _spy)
    rounds = [
        [{"name": "list_activities_in_range", "input": {"window": "this_week"}}],
        "Here is your week.",
    ]
    with patch(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        new=chat_tool_loop_stub(rounds),
    ):
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "How is my week?", "anchor_activity_id": str(activity.id)},
        )

    assert resp.status_code == 200
    assert seen == [date(2026, 10, 22)]
