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

from app.models import Activity, DerivedMetric, User
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


def _session(db, user, *, on: date, type="Run", distance_m=10000, moving_time_s=3600,
             avg_hr=150.0, zones=None, effort="easy"):
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
        activity_id=a.id, effort=effort, structure="continuous", duration_class="standard",
        effort_score=40.0, time_in_zones=zones, flags=[], confidence="high",
        confidence_reasons=[],
    ))
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
    _session(db, u, on=date(2026, 7, 7), zones=MOSTLY_Z2)
    result = _metric(db, u, metric="distance_km", window="last_4_weeks", group_by="week")
    first = result["periods"][0]
    assert first["sessions"] == 0 and first["value"] == 0


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

def _stored_fields():
    fields = set(ActivityFact.__slots__)
    for model in (Activity, DerivedMetric):
        fields |= {c.key for c in inspect(model).column_attrs}
    return fields


def test_every_stored_activity_field_is_served_or_excluded_with_a_reason():
    stored = _stored_fields()
    assert {"time_in_zones", "avg_hr", "hr_drift"} <= stored, "the field scan found nothing to check"
    read = {f for m in tm.METRICS for f in m.reads}
    unaccounted = sorted(stored - read - set(tm.STORED_FIELDS_EXCLUDED))
    assert not unaccounted, (
        f"stored but unreachable by the chat coach: {unaccounted}. Add a measure in "
        "coach/training_metrics.py, or list the field in STORED_FIELDS_EXCLUDED with "
        "where it is served or why it is not."
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
