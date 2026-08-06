"""Per-runner week start actually shifts the week boundary (#676).

The default (Monday / null) path is pinned byte-identical by the existing volume,
recent_weeks, and trends suites. These tests cover the NET-NEW behavior: a Sunday
week start moves the calendar-week boundary in every surface, and the profile
setting threads all the way through the Trends API entry point.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.query_tools import resolve_window
from app.services.coach.recent_weeks import build_recent_weeks
from app.services.coach.volume import build_training_volume, build_volume_report
from app.services.activity_facts import DailyFact
from app.services.trends import build_weekly_buckets, get_volume_report
from app.services.weeks import MONDAY, SUNDAY

# Wednesday 2024-01-03. Its Monday week starts 2024-01-01 (Mon); its Sunday week
# starts 2023-12-31 (Sun).
WED = date(2024, 1, 3)
THAT_MONDAY = date(2024, 1, 1)
THAT_SUNDAY = date(2023, 12, 31)


class TestRecentWeeksBoundary:
    def test_default_this_week_starts_monday(self):
        ctx = build_recent_weeks([], {}, WED)
        assert ctx.this_week.start.weekday == "Mon"
        assert ctx.this_week.days_elapsed == 3  # Mon, Tue, Wed

    def test_sunday_this_week_starts_sunday(self):
        ctx = build_recent_weeks([], {}, WED, SUNDAY)
        assert ctx.this_week.start.weekday == "Sun"
        assert ctx.this_week.days_elapsed == 4  # Sun, Mon, Tue, Wed
        # Last week is the prior complete Sunday-Saturday block.
        assert ctx.last_week.start.weekday == "Sun"
        assert ctx.last_week.end.weekday == "Sat"


class TestVolumeBoundary:
    def test_default_calendar_week_days_elapsed(self):
        ctx = build_training_volume([], WED)
        assert ctx.calendar_week.days_elapsed == 3  # Mon, Tue, Wed

    def test_sunday_calendar_week_days_elapsed(self):
        ctx = build_training_volume([], WED, SUNDAY)
        assert ctx.calendar_week.days_elapsed == 4  # Sun, Mon, Tue, Wed

    def test_report_7d_calendar_period_start_shifts(self):
        default = build_volume_report([], WED, "7D")
        sunday = build_volume_report([], WED, "7D", SUNDAY)
        assert default.calendar.period_start == THAT_MONDAY
        assert sunday.calendar.period_start == THAT_SUNDAY


class TestTrendsWeeklyBucketBoundary:
    def _daily(self, d: date) -> DailyFact:
        return DailyFact(d)

    def test_calendar_mode_buckets_align_to_week_start(self):
        # Calendar mode (rolling_anchor=None) keys buckets on the week boundary.
        facts = [self._daily(WED)]
        default = build_weekly_buckets(
            facts, since=THAT_SUNDAY, until=WED, rolling_anchor=None
        )
        sunday = build_weekly_buckets(
            facts, since=THAT_SUNDAY, until=WED, rolling_anchor=None, week_starts_on=SUNDAY
        )
        assert all(b.week_start.weekday() == MONDAY for b in default)
        assert all(b.week_start.weekday() == SUNDAY for b in sunday)


class TestResolveWindow:
    def test_this_week_default_monday(self):
        w = resolve_window("this_week", WED)
        assert w.start == THAT_MONDAY

    def test_this_week_sunday(self):
        w = resolve_window("this_week", WED, SUNDAY)
        assert w.start == THAT_SUNDAY

    def test_last_week_sunday(self):
        w = resolve_window("last_week", WED, SUNDAY)
        assert w.start == THAT_SUNDAY - timedelta(days=7)
        assert w.end == THAT_SUNDAY  # exclusive end == this week's start


# ---------------------------------------------------------------------------
# The profile setting threads through the Trends API entry point end to end.
# ---------------------------------------------------------------------------

def _user_with_week_start(db, week_starts_on) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    profile = UserProfile(
        user_id=user.id,
        goal_type="general",
        experience_level="intermediate",
        weekly_days_available=4,
    )
    if week_starts_on is not None:
        profile.week_starts_on = week_starts_on
    db.add(profile)
    db.flush()
    return user.id


def _activity_on(db, user_id, on: date) -> Activity:
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        start_date_local=datetime.combine(on, time(12, 0)),
        type="Run",
        name="Run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort_score=1.0,
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_get_volume_report_uses_profile_week_start(db):
    """A Sunday-week-start profile shifts the 7D calendar period start; a default
    (null) profile keeps Monday. Proves the profile -> builder thread on the API path."""
    monday_user = _user_with_week_start(db, None)  # null -> Monday
    sunday_user = _user_with_week_start(db, SUNDAY)
    _activity_on(db, monday_user, WED)
    _activity_on(db, sunday_user, WED)

    monday_report = get_volume_report(db, monday_user, "7D", as_of=WED)
    sunday_report = get_volume_report(db, sunday_user, "7D", as_of=WED)

    assert monday_report.calendar.period_start == THAT_MONDAY
    assert sunday_report.calendar.period_start == THAT_SUNDAY


def test_recent_weeks_context_uses_profile_week_start(db):
    """The coach-pack recent_weeks builder loads the profile via _load_profile and
    threads the runner's week start, so this_week shifts to Sunday for a Sunday runner."""
    from datetime import time as _time

    from app.services.coach.context import _build_recent_weeks_context

    monday_user = _user_with_week_start(db, MONDAY)
    sunday_user = _user_with_week_start(db, SUNDAY)
    monday_activity = _activity_on(db, monday_user, WED)
    sunday_activity = _activity_on(db, sunday_user, WED)
    as_of = datetime.combine(WED, _time(12, 0))

    monday_ctx = _build_recent_weeks_context(db, monday_activity, as_of)
    sunday_ctx = _build_recent_weeks_context(db, sunday_activity, as_of)

    assert monday_ctx.this_week.start.weekday == "Mon"
    assert sunday_ctx.this_week.start.weekday == "Sun"
