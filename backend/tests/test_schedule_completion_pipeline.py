"""#830: the schedule inside the ingestion pipeline.

Pins the guard that keeps a schedule fault out of ingestion and analysis, and the
projection that finally feeds the interval matcher.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import Activity, User
from app.models.planned_session import PlannedSession
from app.services.analysis import _orchestrator
from app.services.schedule import completion
from tests._schedule_completion_fixtures import (
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)


# --- the pipeline: additive, never fatal -----------------------------------


def test_a_schedule_fault_does_not_break_the_auto_completion_caller(db):
    """The schedule is an addition to the ingestion pipeline, not a dependency
    of it. A runner's run must still be ingested, analysed and reported on if the
    schedule cannot answer."""
    from app.jobs import process_new_activity as job

    user = _seed_user(db)
    activity = _seed_activity(db, user)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("the schedule is down")

    with patch.object(completion, "complete_from_activity", _boom):
        job._autocomplete_planned_session(db, activity)  # must not raise


@pytest.mark.asyncio
async def test_the_ingestion_pipeline_survives_a_schedule_fault(db):
    """The same guard through its real caller: the whole pipeline runs and the
    activity is ingested and analysed while auto-completion is broken."""
    from app.core.config import settings
    from app.jobs.process_new_activity import process_new_activity
    from app.models import DerivedMetric, StravaAccount
    from app.services.notifications import InMemoryNotifier
    from app.services.strava_ingestion import InMemoryStravaAdapter

    user = _seed_user(db)
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=12345,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()

    adapter = InMemoryStravaAdapter()
    adapter.seed_activities(
        [
            {
                "id": 9301,
                "name": "Morning easy run",
                "type": "Run",
                "start_date": "2026-08-11T08:00:00Z",
                "distance": 8200,
                "moving_time": 2400,
                "elapsed_time": 2400,
                "total_elevation_gain": 40,
                "average_heartrate": 142,
                "average_speed": 3.4,
            }
        ]
    )
    adapter.seed_streams(
        9301,
        {
            "time": {"data": [0, 60, 120, 180]},
            "heartrate": {"data": [130, 140, 145, 148]},
            "distance": {"data": [0, 200, 400, 600]},
        },
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("the schedule is down")

    fake_client = AsyncMock()
    # An unusable body: the report degrades to a fallback and no notification is
    # sent, which keeps this test about the pipeline surviving rather than about
    # the coach.
    fake_client.generate_json_with_usage = AsyncMock(return_value=("nope", None))

    with patch.object(settings, "COACH_PROMPT_ID", "coach_report_v10"), patch.object(
        completion, "complete_from_activity", _boom
    ), patch("app.services.coach.turn.AnthropicClient", return_value=fake_client):
        await process_new_activity(
            db=db,
            account=account,
            strava_activity_id=9301,
            strava_port=adapter,
            notifier=InMemoryNotifier(),
        )

    stored = db.query(Activity).filter_by(strava_activity_id=9301).one()
    assert db.query(DerivedMetric).filter_by(activity_id=stored.id).count() == 1


# --- the formerly dormant stub ---------------------------------------------
#
# `_extract_planned_workout` returned None from the beginning of the project.
# `tests/test_analysis_stages.py` covers the end-to-end reachability it now has;
# these cover the PROJECTION itself, including the shapes that must still read
# as "no plan" so every runner without prescribed reps matches as they always did.


def test_the_planned_workout_projection_reads_a_session_as_a_plan(db):
    assert _orchestrator._extract_planned_workout(None) is None
    assert _orchestrator._extract_planned_workout(PlannedSession(structure=None)) is None
    # An empty structure is not a plan — it would otherwise reach the matcher as
    # a prescription of nothing and score the run against it.
    assert _orchestrator._extract_planned_workout(PlannedSession(structure={})) is None
    assert _orchestrator._extract_planned_workout(
        PlannedSession(structure={"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60})
    ) == {"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60}


def test_analysis_survives_a_schedule_that_cannot_answer(db):
    """Analysis is a lower layer than the schedule. A schedule fault must leave a
    run analysed with no plan, not unanalysed."""
    user = _seed_user(db)
    activity = _seed_activity(db, user)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("the schedule is down")

    with patch.object(completion, "find_matching_session", _boom):
        assert _orchestrator._resolve_planned_session(db, activity, False) is None


def test_a_reanalysis_after_the_auto_tick_keeps_the_plan_it_was_judged_against(
    db, monkeypatch
):
    """KNOWN DEFECT, left failing deliberately (see the report for this slice).

    The pipeline analyses the run and THEN ticks the session off, so the first
    analysis sees the plan. But `find_matching_session` excludes completed
    sessions, so every LATER analysis of the same activity finds nothing — and
    re-analysis is routine: `write_checkin` re-analyses (so does the Telegram RPE
    tap), as do the intent write, the stream backfill and the bulk re-analysis.

    The `DerivedMetric` upsert writes all columns unconditionally, so the second
    pass silently overwrites the plan comparison this slice just made possible:
    `match_score` goes 0.5 -> 1.0 and the CRITICAL `interval_structure_mismatch`
    is replaced by `no_planned_workout`. The runner taps RPE on their
    notification and the run stops disagreeing with the plan it disagreed with.

    Correct behaviour is not in doubt — a re-analysis must not change the read —
    but WHERE the fix belongs is a design call (a credited-session lookup ahead
    of the open-session match, in `_resolve_planned_session` or in
    `completion.planned_structure_for`, which exists for exactly this question
    and is currently unused), so this is reported rather than patched.
    """
    from app.services.analysis._orchestrator import analyze
    from tests.test_analysis_composition import _seed_run

    detected = {
        "source": "recorded_laps",
        "summary": {
            "rep_count": 3,
            "total_work_time_s": 300,
            "work_duration_cv": 3.0,
            "consistency_score": 0.9,
        },
        "work_segments": [{"duration_s": 100, "distance_m": 400}] * 3,
    }

    activity = _seed_run(db)
    day = activity.local_start.date()
    plan = _seed_plan(db, db.get(User, activity.user_id))
    _seed_session(
        db,
        plan,
        start=day,
        end=day,
        intent="quality",
        title="12x400m",
        target_distance_m=10000,
        structure={"reps_planned": 12, "rep_distance_m": 400, "rest_s": 60},
    )

    monkeypatch.setattr(
        _orchestrator, "detect_intervals_from_laps", lambda *a, **k: detected
    )
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)
    real_classify = _orchestrator.classify_activity

    def _force_intervals(*args, **kwargs):
        classified = real_classify(*args, **kwargs)
        classified.structure = "intervals"
        return classified

    monkeypatch.setattr(_orchestrator, "classify_activity", _force_intervals)

    first = analyze(db, activity.id)
    assert "interval_structure_mismatch" in (first.confidence_reasons or [])

    completion.complete_from_activity(db, activity)
    second = analyze(db, activity.id)

    assert (second.workout_match or {}).get("match_score") == (
        first.workout_match or {}
    ).get("match_score")
    assert "interval_structure_mismatch" in (second.confidence_reasons or [])


def test_the_planned_session_resolved_for_analysis_is_the_matched_one(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(
        db,
        plan,
        intent="quality",
        title="8x400m",
        target_distance_m=10000,
        structure={"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60},
    )
    run = _seed_activity(db, user, distance_m=10000)

    resolved = _orchestrator._resolve_planned_session(db, run, reps_shown=True)

    assert resolved == session
    assert _orchestrator._extract_planned_workout(resolved) == session.structure
    # #971: without reps seen, the same run is not this rep session's.
    assert _orchestrator._resolve_planned_session(db, run, reps_shown=False) is None
