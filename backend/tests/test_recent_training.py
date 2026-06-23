"""#444: the modality-aware recent-training builder + its pack wiring.

`build_recent_training` is a pure function over duck-typed ActivityFacts: per-type
breakdown + share, a bounded per-activity list for the recent window, and vs-typical
(per-day-rate, reused from the Trends core) + vs-prev comparisons with self-describing
basis labels. The pack integration test pins that the section rides the DEFAULT pack
only under the recent-training-aware prompt (coach_message_v11) and is dropped (byte-
stable) under v10 and the default path.
"""

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models import Activity, DerivedMetric, User
from app.services.coach.context import build_context_pack
from app.services.coach.recent_training import _MAX_RECENT_ACTIVITIES, build_recent_training

_AS_OF = date(2026, 6, 1)


def _fact(days_ago, *, type="Run", dist=8000, time=2400, effort_score=50.0, effort="easy"):
    return SimpleNamespace(
        activity_id=uuid.uuid4(),
        local_date=date.fromordinal(_AS_OF.toordinal() - days_ago),
        activity_type=type,
        distance_m=dist,
        moving_time_s=time,
        effort_score=effort_score,
        effort=effort,
    )


# --- per-type breakdown + share --------------------------------------------

def test_per_type_breakdown_counts_totals_and_share():
    facts = [
        _fact(1, type="Run", dist=10000),
        _fact(2, type="Run", dist=8000),
        _fact(3, type="Run", dist=6000),
        _fact(4, type="Walk", dist=3000),
        _fact(5, type="Walk", dist=2000),
    ]
    ctx = build_recent_training(facts, _AS_OF)
    by_type = {b.type: b for b in ctx.last_7d.by_type}
    assert by_type["Run"].count == 3
    assert by_type["Run"].distance_m == 24000
    assert by_type["Run"].share_pct == 60.0  # 3 of 5 sessions
    assert by_type["Walk"].count == 2
    assert by_type["Walk"].share_pct == 40.0
    # most-frequent type first (modality mix is a number to read).
    assert ctx.last_7d.by_type[0].type == "Run"
    # roll-up totals across all types.
    assert ctx.last_7d.activity_count == 5
    assert ctx.last_7d.total_distance_m == 29000


def test_mixed_modality_runs_only_differs_from_all():
    facts = [_fact(1, type="Run"), _fact(2, type="Walk"), _fact(3, type="Ride")]
    ctx = build_recent_training(facts, _AS_OF)
    sessions = next(c for c in ctx.last_7d.comparisons if c.metric == "sessions")
    assert sessions.current_all == 3
    assert sessions.current_runs == 1  # a walk and a ride are never read as runs


# --- per-activity list (recent window only) --------------------------------

def test_recent_window_carries_capped_per_activity_list():
    facts = [_fact(i % 7, effort="easy", effort_score=40.0 + i) for i in range(20)]
    ctx = build_recent_training(facts, _AS_OF)
    assert len(ctx.last_7d.activities) == _MAX_RECENT_ACTIVITIES  # bounded
    item = ctx.last_7d.activities[0]
    assert item.type == "Run" and item.effort == "easy"
    assert item.effort_score is not None
    # the longer windows do NOT carry a per-activity list (bounded by design).
    assert ctx.last_30d.activities == []
    assert ctx.previous_30d.activities == []


# --- vs-prev ---------------------------------------------------------------

def test_vs_prev_compares_equal_length_prior_window():
    # 3 sessions in the last 7 days, 1 in the 7 days before that.
    facts = [_fact(1), _fact(2), _fact(3), _fact(10)]
    ctx = build_recent_training(facts, _AS_OF)
    sessions = next(c for c in ctx.last_7d.comparisons if c.metric == "sessions")
    assert sessions.vs_prev_pct == 200.0  # (3 - 1) / 1 * 100
    assert sessions.vs_prev_direction == "up"
    assert "immediately before" in sessions.prev_basis


def test_vs_prev_abstains_when_prior_window_empty():
    facts = [_fact(1), _fact(2)]  # nothing in the prior 7 days
    ctx = build_recent_training(facts, _AS_OF)
    sessions = next(c for c in ctx.last_7d.comparisons if c.metric == "sessions")
    assert sessions.vs_prev_pct is None
    assert sessions.vs_prev_direction == "no_norm"


# --- vs-typical (per-day-rate baseline, reused from the Trends core) --------

def test_vs_typical_resolves_on_30d_window_and_carries_basis():
    # #451: only the 30d window carries vs-typical (the ~6-month per-day rate); the 7d
    # weekly vs-norm verdict lives in training_volume, so 7d omits vs-typical entirely.
    # Runs spread across the ~6 months before now -> the 30d vs-typical resolves.
    facts = [_fact(7 * w + 8) for w in range(20)]  # ~20 runs over ~20 weeks
    facts += [_fact(1), _fact(2), _fact(3)]  # a busy current week
    ctx = build_recent_training(facts, _AS_OF)
    assert ctx.has_baseline is True

    s30 = next(c for c in ctx.last_30d.comparisons if c.metric == "sessions")
    assert s30.vs_typical_direction in {"up", "in_line", "down"}
    assert s30.vs_typical_pct is not None
    assert s30.typical_basis is not None
    assert "average daily" in s30.typical_basis and "6 months" in s30.typical_basis

    # 7d keeps vs-prev but carries NO vs-typical read (basis None, direction no_norm).
    s7 = next(c for c in ctx.last_7d.comparisons if c.metric == "sessions")
    assert s7.vs_typical_pct is None
    assert s7.vs_typical_direction == "no_norm"
    assert s7.typical_basis is None
    assert s7.vs_prev_pct is not None  # the vs-prev comparison is still present


def test_vs_typical_abstains_on_thin_history():
    facts = [_fact(1), _fact(2)]  # only this week, no baseline
    ctx = build_recent_training(facts, _AS_OF)
    assert ctx.has_baseline is False
    for window in (ctx.last_7d, ctx.last_30d):
        for c in window.comparisons:
            assert c.vs_typical_direction == "no_norm"
            assert c.vs_typical_pct is None


def test_windows_and_comparison_metrics_are_complete():
    facts = [_fact(1)]
    ctx = build_recent_training(facts, _AS_OF)
    assert ctx.last_7d.window == "last_7d" and ctx.last_7d.days == 7
    assert ctx.last_30d.window == "last_30d" and ctx.last_30d.days == 30
    assert ctx.previous_30d.window == "previous_30d"
    # 7d/30d carry comparisons over all four metrics; previous_30d carries none.
    assert {c.metric for c in ctx.last_7d.comparisons} == {
        "sessions", "distance_m", "moving_time_s", "effort_score"
    }
    assert ctx.previous_30d.comparisons == []


# --- pack wiring (DB) ------------------------------------------------------

def _seed(db) -> Activity:
    user = User(id=uuid.uuid4(), email=f"rt-{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    activity = Activity(
        id=uuid.uuid4(),
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run",
        type="Run",
        start_date=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3600,
        avg_hr=150.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort="easy",
            structure="continuous",
            duration_class="standard",
            effort_score=80.0,
            flags=[],
            confidence="high",
            confidence_reasons=[],
        )
    )
    db.flush()
    db.refresh(activity)
    return activity


def test_recent_training_in_default_pack_only_under_v11(db):
    activity = _seed(db)

    pack_v11 = build_context_pack(db, activity, prompt_id="coach_message_v11")
    assert pack_v11.recent_training is not None
    assert "recent_training" in pack_v11.to_serializable_dict()

    pack_v10 = build_context_pack(db, activity, prompt_id="coach_message_v10")
    assert pack_v10.recent_training is None
    assert "recent_training" not in pack_v10.to_serializable_dict()

    pack_default = build_context_pack(db, activity)
    assert pack_default.recent_training is None
    assert "recent_training" not in pack_default.to_serializable_dict()

    # #451: the coarse recent_training_summary is RETIRED — no freshly built pack
    # emits it, under any prompt (the rich recent_training supersedes it under v11).
    for pack in (pack_v11, pack_v10, pack_default):
        assert pack.recent_training_summary is None
        assert "recent_training_summary" not in pack.to_serializable_dict()
