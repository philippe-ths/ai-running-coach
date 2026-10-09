"""#1071: the chat coach can reach every measure the app stores.

A runner asked for their weekly zone 2+ time and the coach answered "I can't
confirm", though every activity held it. These pin the class fix: the
`get_training_metric` tool, its agreement with the figure the challenge tracker
already shows the runner, missing data reading as missing rather than zero, and
the completeness check that keeps a newly stored field from going unreachable.

Fixtures are synthetic test setup (trust level 5). The zone-time ORACLE is not:
it is `norms.weekly_actuals`, the computation behind the challenge figure the
Schedule screen shows today, run over the same activities.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect

import app.models  # noqa: F401  (registers every table on the metadata)
from app.db.base import Base
from app.models import Activity, CheckIn, DerivedMetric, User, UserProfile
from app.services.activity_facts import ActivityFact, query_facts
from app.services.coach import query_tools as qt
from app.services.coach import training_metrics as tm
from app.services.schedule.norms import weekly_actuals

TODAY = date(2026, 7, 11)  # a Saturday; weeks start Monday by default


def _user(db) -> User:
    u = User(email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.commit()
    return u


def _profile(db, user, **fields):
    db.add(UserProfile(user_id=user.id, goal_type="general", experience_level="intermediate",
                       weekly_days_available=4, **fields))
    db.commit()


def _session(db, user, *, on: date, type="Run", distance_m=10000, moving_time_s=3600,
             avg_hr=150.0, zones=None, effort="easy", duration_class="standard",
             is_hilly=None, hr_drift=None, check_in=None):
    a = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=datetime(on.year, on.month, on.day, 8, 0), type=type, name="Morning run",
        distance_m=distance_m, moving_time_s=moving_time_s, elapsed_time_s=moving_time_s,
        elev_gain_m=50.0, avg_hr=avg_hr, max_hr=(avg_hr + 20 if avg_hr else None),
        avg_cadence=85, raw_summary={},
    )
    db.add(a)
    db.commit()
    db.add(DerivedMetric(
        activity_id=a.id, effort=effort, structure="continuous", duration_class=duration_class,
        is_hilly=is_hilly, hr_drift=hr_drift,
        effort_score=40.0, time_in_zones=zones, flags=[], confidence="high",
        confidence_reasons=[],
    ))
    if check_in is not None:
        db.add(CheckIn(activity_id=a.id, **check_in))
    db.commit()
    return a


# Sample counts per zone. zone_seconds spreads them over moving time, so a
# session recorded every 4 s still measures on the same clock as a 1 s one.
MOSTLY_Z2 = {"Z1": 600, "Z2": 2400, "Z3": 600}
SMART_RECORDED = {"Z1": 150, "Z2": 600, "Z3": 150}  # a ~4 s watch: same shares, a quarter of the samples


def _metric(db, user, **kw):
    kw.setdefault("group_by", None)
    kw.setdefault("type_filter", None)
    kw.setdefault("min_zone", None)
    kw.setdefault("session_kind", None)
    return qt.get_training_metric(db, user.id, today=TODAY, **kw)


# --- the question that failed -------------------------------------------------

def test_weekly_zone_2_plus_matches_the_challenge_tracker(db):
    u = _user(db)
    for on, zones in [
        (date(2026, 6, 23), MOSTLY_Z2), (date(2026, 6, 25), SMART_RECORDED),
        (date(2026, 7, 1), MOSTLY_Z2), (date(2026, 7, 9), SMART_RECORDED),
        (date(2026, 7, 10), MOSTLY_Z2),
    ]:
        _session(db, u, on=on, zones=zones)

    result = _metric(db, u, metric="zone_time_h", window="last_4_weeks", group_by="week")

    facts = query_facts(db, None, TODAY + timedelta(days=1), user_id=u.id)
    assert len(result["periods"]) == 4
    for period in result["periods"]:
        if period.get("before_records"):
            continue
        oracle = weekly_actuals(facts, date.fromisoformat(period["from"]), 0).zone_time_s(2) / 3600
        assert period["value"] == round(oracle, 1), (
            f"week {period['from']}: tool {period['value']} h, challenge tracker {oracle:.2f} h"
        )
    assert result["periods"][1]["value"] > 0, "the fixture weeks must carry zone time"


def test_whole_week_window_opens_on_a_week_boundary(db):
    u = _user(db)
    result = _metric(db, u, metric="sessions", window="last_4_weeks", group_by="week")
    starts = [p["from"] for p in result["periods"]]
    assert starts == ["2026-06-15", "2026-06-22", "2026-06-29", "2026-07-06"]
    assert result["periods"][-1].get("in_progress") is True


def test_a_week_with_no_training_reads_zero_not_missing(db):
    u = _user(db)
    _session(db, u, on=date(2026, 5, 1))  # records begin well before the window
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)
    result = _metric(db, u, metric="distance_km", window="last_4_weeks", group_by="week")
    first = result["periods"][0]
    assert first["sessions"] == 0 and first["value"] == 0


def test_a_week_before_the_record_begins_reads_unknown_not_zero(db):
    u = _user(db)
    _session(db, u, on=date(2026, 6, 24))  # first record: mid week of 06-22
    periods = _metric(db, u, metric="distance_km", window="last_4_weeks", group_by="week")["periods"]
    assert periods[0] == {"from": "2026-06-15", "to": "2026-06-21", "value": None, "before_records": True}
    assert periods[1]["records_begin_mid_period"] == "2026-06-24"


def test_a_week_by_week_read_follows_the_runners_own_week_start(db):
    u = _user(db)
    _profile(db, u, week_starts_on=6)
    for on, zones in [(date(2026, 6, 28), MOSTLY_Z2), (date(2026, 7, 4), SMART_RECORDED),
                      (date(2026, 7, 5), MOSTLY_Z2)]:  # Sun, Sat, Sun
        _session(db, u, on=on, zones=zones)
    result = _metric(db, u, metric="zone_time_h", window="last_4_weeks", group_by="week")
    facts = query_facts(db, None, TODAY + timedelta(days=1), user_id=u.id)
    for period in result["periods"]:
        if period.get("before_records"):
            continue
        assert date.fromisoformat(period["from"]).weekday() == 6
        oracle = weekly_actuals(facts, date.fromisoformat(period["from"]), 6).zone_time_s(2) / 3600
        assert period["value"] == round(oracle, 1), period


def test_monthly_periods_follow_the_calendar_and_mark_a_clipped_month(db):
    u = _user(db)
    _session(db, u, on=date(2026, 4, 1))
    _session(db, u, on=date(2026, 5, 31), distance_m=5000)
    periods = _metric(db, u, metric="distance_km", window="last_90_days", group_by="month")["periods"]
    assert [(p["from"], p["to"]) for p in periods] == [
        ("2026-04-13", "2026-04-30"), ("2026-05-01", "2026-05-31"),
        ("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-11"),
    ]
    assert "partial_period" in periods[0]
    assert "partial_period" not in periods[1] and periods[1]["value"] == 5.0


# --- missing data is not zero ---------------------------------------------------

def test_a_session_without_heart_rate_is_left_out_and_counted_as_missing(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)
    _session(db, u, on=date(2026, 7, 8), zones=None, avg_hr=None)

    result = _metric(db, u, metric="zone_time_h", window="this_week")

    whole = result["whole_window"]
    assert whole["sessions"] == 2
    assert whole["sessions_with_data"] == 1
    assert "missing_data_note" in result


def test_zone_time_says_whether_zones_are_calibrated(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)
    result = _metric(db, u, metric="zone_time_h", window="this_week", min_zone=3)
    assert result["min_zone"] == 3
    assert result["zones_calibrated"] is False
    assert "zones_note" in result


# --- the server decides how a measure combines ----------------------------------

def test_average_heart_rate_is_time_weighted_not_summed(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), avg_hr=140.0, moving_time_s=3600)
    _session(db, u, on=date(2026, 7, 8), avg_hr=170.0, moving_time_s=1200)
    result = _metric(db, u, metric="avg_hr_bpm", window="this_week")
    # (140*3600 + 170*1200) / 4800 = 147.5
    assert result["whole_window"]["value"] == 147.5


def test_pace_reads_runs_only_unless_told_otherwise(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), distance_m=10000, moving_time_s=3000)  # 5:00/km
    _session(db, u, on=date(2026, 7, 8), type="Ride", distance_m=40000, moving_time_s=3600)
    result = _metric(db, u, metric="avg_pace_per_km", window="this_week")
    assert result["whole_window"]["value"] == "5:00/km"
    assert result["window"]["type_filter"] == "run"


def test_effort_mix_counts_sessions_by_label(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), effort="easy")
    _session(db, u, on=date(2026, 7, 8), effort="easy")
    _session(db, u, on=date(2026, 7, 9), effort="hard")
    result = _metric(db, u, metric="sessions_by_effort", window="this_week")
    assert result["whole_window"]["value"] == {"easy": 2, "hard": 1}


# --- what a measure means ---------------------------------------------------------

def test_a_higher_min_zone_counts_less_time(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)  # 60 min: 10 Z1, 40 Z2, 10 Z3
    z2 = _metric(db, u, metric="zone_time_h", window="this_week")["whole_window"]["value"]
    z3 = _metric(db, u, metric="zone_time_h", window="this_week", min_zone=3)["whole_window"]["value"]
    assert (z2, z3) == (0.8, 0.2)


def test_an_invalid_min_zone_is_refused_not_replaced(db):
    u = _user(db)
    assert _metric(db, u, metric="zone_time_h", window="this_week", min_zone=7)["error"] == "invalid_min_zone"


def test_pace_over_several_runs_is_total_distance_over_total_time(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), distance_m=10000, moving_time_s=3000)  # 5:00/km
    _session(db, u, on=date(2026, 7, 8), distance_m=5000, moving_time_s=1800)   # 6:00/km
    # 15 km in 4800 s = 5:20/km, not the 5:30 a mean of the two paces gives
    assert _metric(db, u, metric="avg_pace_per_km", window="this_week")["whole_window"]["value"] == "5:20/km"


def test_highest_heart_rate_is_the_single_highest_session(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), avg_hr=140.0)  # max 160
    _session(db, u, on=date(2026, 7, 8), avg_hr=165.0)  # max 185
    assert _metric(db, u, metric="max_hr_bpm", window="this_week")["whole_window"]["value"] == 185.0


def test_drift_is_the_mean_of_the_sessions_that_recorded_it(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), hr_drift=2.0)
    _session(db, u, on=date(2026, 7, 8), hr_drift=6.0)
    _session(db, u, on=date(2026, 7, 9), hr_drift=None)
    whole = _metric(db, u, metric="hr_drift_pct", window="this_week")["whole_window"]
    assert (whole["value"], whole["sessions_with_data"]) == (4.0, 2)


def test_zone_time_says_when_zones_are_calibrated(db):
    u = _user(db)
    _profile(db, u, max_hr=190, max_hr_source="user_entered")
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)
    result = _metric(db, u, metric="zone_time_h", window="this_week")
    assert result["zones_calibrated"] is True and "zones_note" not in result


def test_a_session_kind_narrows_to_those_sessions(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), avg_hr=140.0, duration_class="long")
    _session(db, u, on=date(2026, 7, 8), avg_hr=170.0)
    result = _metric(db, u, metric="avg_hr_bpm", window="this_week", session_kind="long")
    assert result["whole_window"] == {"value": 140.0, "sessions": 1}


def test_hilly_sessions_are_counted_from_the_stored_flag(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), is_hilly=True)
    _session(db, u, on=date(2026, 7, 8), is_hilly=False)
    assert _metric(db, u, metric="hilly_sessions", window="this_week")["whole_window"]["value"] == 1


def test_mileage_over_every_type_states_the_runs_only_figure_alongside(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), distance_m=20000)
    _session(db, u, on=date(2026, 7, 8), type="Ride", distance_m=60000)
    whole = _metric(db, u, metric="distance_km", window="this_week")["whole_window"]
    assert (whole["value"], whole["runs_only"]) == (80.0, 20.0)


def test_how_hard_sessions_felt_comes_from_the_runners_check_ins(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), check_in={"rpe": 4})
    _session(db, u, on=date(2026, 7, 8), check_in={"rpe": 8})
    assert _metric(db, u, metric="avg_rpe", window="this_week")["whole_window"]["value"] == 6.0


def test_a_ride_carries_no_running_cadence(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 8), type="Ride")
    entry = qt.list_activities_in_range(
        db, u.id, window="this_week", type_filter=None, today=TODAY
    )["activities"][0]
    assert "avg_cadence_spm" not in entry


def test_a_monthly_breakdown_too_long_to_read_suggests_a_shorter_window(db):
    u = _user(db)
    _session(db, u, on=date(2020, 1, 1))
    result = _metric(db, u, metric="distance_km", window="all_time", group_by="month")
    assert result["error"] == "too_many_periods" and result["hint"] == "use a shorter window"


def test_session_detail_carries_what_the_runner_told_us_and_the_stored_measures(db):
    u = _user(db)
    a = _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2, is_hilly=True,
                 check_in={"rpe": 7, "pain_score": 3, "pain_location": "left calf",
                           "notes": "windy"})
    detail = qt.get_session_detail(db, u.id, activity_id=str(a.id), today=TODAY)
    assert detail["name"] == "Morning run"
    assert detail["minutes_by_hr_zone"] == {"Z1": 10, "Z2": 40, "Z3": 10}
    assert (detail["rpe"], detail["pain_score"]) == (7, 3)
    assert (detail["pain_location"], detail["runner_notes"]) == ("left calf", "windy")
    assert detail["hilly"] is True


# --- scoping and bounds -----------------------------------------------------------

def test_another_runners_sessions_are_never_counted(db):
    me, other = _user(db), _user(db)
    _session(db, me, on=date(2026, 7, 7), distance_m=5000)
    _session(db, other, on=date(2026, 7, 7), distance_m=42000)
    result = _metric(db, me, metric="distance_km", window="this_week")
    assert result["whole_window"]["value"] == 5.0


def test_an_unbounded_weekly_breakdown_is_refused_with_a_way_forward(db):
    u = _user(db)
    _session(db, u, on=date(2024, 1, 1))
    result = _metric(db, u, metric="distance_km", window="all_time", group_by="week")
    assert result["error"] == "too_many_periods"
    assert "month" in result["hint"]


def test_an_unknown_metric_names_the_allowed_ones(db):
    u = _user(db)
    result = qt.execute_chat_tool(
        db, u.id, "get_training_metric", {"metric": "vo2max", "window": "this_week"}, today=TODAY
    )
    assert result["error"] == "unknown_metric"
    assert "zone_time_h" in result["allowed"]


# --- per-session reach ------------------------------------------------------------

def test_each_listed_session_carries_its_recorded_measures(db):
    u = _user(db)
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)
    entry = qt.list_activities_in_range(
        db, u.id, window="this_week", type_filter=None, today=TODAY
    )["activities"][0]
    assert entry["avg_hr_bpm"] == 150.0
    assert entry["max_hr_bpm"] == 170.0
    assert entry["elevation_gain_m"] == 50.0
    assert entry["minutes_by_hr_zone"] == {"Z1": 10, "Z2": 40, "Z3": 10}


def test_the_trace_names_what_was_measured():
    entry = qt.summarize_tool_call(
        "get_training_metric",
        {"metric": "zone_time_h", "window": "last_4_weeks", "group_by": "week"},
        {"min_zone": 2, "whole_window": {"sessions": 5}},
    )
    assert entry["label"] == "Measured your training"
    assert entry["detail"] == "zone 2+ time, last 4 weeks"
    assert entry["count"] == 5


# --- completeness: nothing stored goes unreachable --------------------------------

def _activity_tables():
    """Every table that is, or is keyed to, an activity, found from the schema."""
    return {
        t.name: t for t in Base.metadata.tables.values()
        if t.name == "activities"
        or any(fk.column.table.name == "activities" for fk in t.foreign_keys)
    }


def test_every_table_keyed_to_an_activity_is_accounted_for():
    tables = _activity_tables()
    assert {"activities", "derived_metrics", "check_ins"} <= set(tables), "the schema scan found nothing"
    unlisted = sorted(set(tables) - set(tm.ACTIVITY_TABLES))
    assert not unlisted, (
        f"tables keyed to an activity that the chat coach cannot reach: {unlisted}. Read their "
        "fields in coach/training_metrics.py, or list the table in ACTIVITY_TABLES with where "
        "it is served or why it is not."
    )


def test_every_stored_activity_field_is_served_or_excluded_with_a_reason():
    tables = _activity_tables()
    stored = set(ActivityFact.__slots__)
    for name, how in tm.ACTIVITY_TABLES.items():
        if how == "fields":
            stored |= {c.name for c in tables[name].columns}
    read = {f for m in tm.METRICS for f in m.reads}
    unaccounted = sorted(stored - read - set(tm.STORED_FIELDS_EXCLUDED))
    assert not unaccounted, (
        f"stored but unreachable by the chat coach: {unaccounted}. Add a measure in "
        "coach/training_metrics.py, or list the field in STORED_FIELDS_EXCLUDED with "
        "where it is served or why it is not."
    )


class _RecordingFact:
    """Answers any field a measure asks for, and remembers which it asked."""

    _VALUES = {"time_in_zones": {"Z2": 100}, "activity_type": "Run", "user_intent": None,
               "effort": "easy", "structure": "intervals", "duration_class": "long"}

    def __init__(self):
        self.read = set()

    def __getattr__(self, name):
        self.read.add(name)
        return self._VALUES.get(name, 10.0)


@pytest.mark.parametrize("metric", tm.METRICS, ids=lambda m: m.key)
def test_a_measure_reads_every_field_it_claims_to_serve(metric):
    fact = _RecordingFact()
    metric.value(fact, 2)
    assert set(metric.reads) <= fact.read, (
        f"{metric.key} declares it serves {sorted(set(metric.reads) - fact.read)} but never reads them"
    )


def test_no_field_is_both_served_and_excluded():
    read = {f for m in tm.METRICS for f in m.reads}
    assert not read & set(tm.STORED_FIELDS_EXCLUDED)


def test_the_tool_schema_offers_every_registered_measure():
    schema = next(t for t in qt.CHAT_TOOLS if t["name"] == "get_training_metric")
    assert schema["input_schema"]["properties"]["metric"]["enum"] == [m.key for m in tm.METRICS]


# --- the turn: the coach can call it and the figure reaches the model --------------

@pytest.mark.asyncio
async def test_a_chat_turn_fetches_weekly_zone_time_and_feeds_it_back(db):
    from unittest.mock import patch

    from app.services.coach.llm import AnthropicClient
    from app.services.coach.thread_turn import stream_thread_turn
    from tests._chat_stubs import chat_tool_loop_stub
    from tests.test_coach_chat_stream import _seed_activity_with_report

    anchor = _seed_activity_with_report(db)
    user = db.query(User).filter(User.id == anchor.user_id).one()
    _session(db, user, on=date.today() - timedelta(days=1), zones=MOSTLY_Z2)

    capture = {}
    stub = chat_tool_loop_stub([
        [{"name": "get_training_metric", "input": {
            "metric": "zone_time_h", "window": "last_4_weeks", "group_by": "week"}}],
        "You did about 0.8 h of zone 2+ this week.",
    ], capture=capture)
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = [ev async for ev in stream_thread_turn(
            db, user, message="What's my weekly zone 2+?", anchor_activity=anchor)]

    assert any(ev.status_label == "Measuring your training…" for ev in events)
    results = [
        blk["content"] for m in capture["messages_seen"][1]
        if isinstance(m.get("content"), list)
        for blk in m["content"] if isinstance(blk, dict) and blk.get("type") == "tool_result"
    ]
    assert results and '"periods"' in results[0], "the weekly breakdown reached the model"
    assert '"error"' not in results[0], results[0]
