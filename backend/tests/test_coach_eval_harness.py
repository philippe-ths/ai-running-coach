"""Tests for the offline eval harness (M5): DB loading, aggregation, regression
compare, and the bundled good/bad self-test fixtures.

Reports are seeded directly as CoachReport rows (the harness scores each row
self-contained from its stored report + context_pack). Fixtures are synthetic
(trust level 5), clearly NOT production data.
"""

from datetime import datetime
from uuid import uuid4

import pytest

from app.models.activity import Activity
from app.models.coach_report import CoachReport
from app.models.user import User
from app.services.coach.eval.fixtures import deliberately_bad_report, known_good_report
from app.services.coach.eval.harness import (
    Scorecard,
    compare_scorecards,
    run_self_test,
    score_db_reports,
)
from app.services.coach.eval.rubric import AssertionStatus, score_report
from app.services.coach.service import SCHEMA_VERSION

PROMPT_ID = "coach_report_v2"


def _seed_activity(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    activity = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Test run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def _seed_report(db, content, pack, *, is_fallback=False, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION):
    activity = _seed_activity(db)
    row = CoachReport(
        activity_id=activity.id,
        prompt_id=prompt_id,
        schema_version=schema_version,
        report=content.model_dump(mode="json"),
        meta={"confidence": "high", "model_id": "test", "prompt_id": prompt_id,
              "schema_version": schema_version, "input_hash": "x",
              "generated_at": "2026-05-27T10:00:00+00:00", "policy_violations": []},
        context_pack=pack.to_serializable_dict(),
        raw_llm_response=None,
        is_fallback=is_fallback,
    )
    db.add(row)
    db.commit()
    return row


class TestScoreDbReports:
    def test_scores_current_version_reports(self, db):
        good_c, good_p = known_good_report()
        bad_c, bad_p = deliberately_bad_report()
        _seed_report(db, good_c, good_p)
        _seed_report(db, bad_c, bad_p)

        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert isinstance(card, Scorecard)
        assert len(card.report_scores) == 2
        assert card.overall_pass_rate < 1.0  # the bad report drags it down

    def test_skips_fallback_reports(self, db):
        good_c, good_p = known_good_report()
        _seed_report(db, good_c, good_p, is_fallback=True)
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert card.report_scores == []
        assert card.skipped_fallback == 1

    def test_unparseable_pack_recorded_as_error_not_crash(self, db):
        good_c, good_p = known_good_report()
        row = _seed_report(db, good_c, good_p)
        row.context_pack = {"garbage": True}  # drifted / corrupt pack
        db.commit()
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert card.report_scores == []
        assert len(card.errors) == 1

    def test_version_filter_excludes_other_versions(self, db):
        good_c, good_p = known_good_report()
        _seed_report(db, good_c, good_p, prompt_id="coach_report_v1", schema_version="1.1")
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert card.report_scores == []


class TestAggregateAndSerialise:
    def test_aggregate_assertion_summary(self, db):
        good_c, good_p = known_good_report()
        _seed_report(db, good_c, good_p)
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        summary = card.to_dict()["assertion_summary"]
        assert "led_with_headline" in summary
        assert summary["led_with_headline"]["pass_rate"] == 1.0

    def test_to_dict_is_json_serialisable_and_stable(self, db):
        good_c, good_p = known_good_report()
        _seed_report(db, good_c, good_p)
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        import json
        a = json.dumps(card.to_dict(), sort_keys=True)
        b = json.dumps(card.to_dict(), sort_keys=True)
        assert a == b


class TestRegressionCompare:
    def test_no_regression_for_identical(self):
        good_c, good_p = known_good_report()
        score = score_report(good_c, good_p, report_id="r1")
        card = Scorecard(report_scores=[score])
        regressions = compare_scorecards(card.to_dict(), card.to_dict())
        assert regressions == []

    def test_detects_pass_rate_drop(self):
        good_c, good_p = known_good_report()
        bad_c, bad_p = deliberately_bad_report()
        before = Scorecard(report_scores=[score_report(good_c, good_p, report_id="r1")]).to_dict()
        after = Scorecard(report_scores=[score_report(bad_c, bad_p, report_id="r1")]).to_dict()
        regressions = compare_scorecards(before, after)
        assert regressions  # at least one assertion regressed


class TestSelfTest:
    def test_self_test_passes_on_good_and_bad_fixtures(self):
        # The harness's own validation: the good fixture must pass all applicable
        # assertions and the bad fixture must fail the intended dimensions.
        ok, report = run_self_test()
        assert ok, report

    def test_known_good_fixture_passes_all_applicable(self):
        content, pack = known_good_report()
        score = score_report(content, pack)
        assert score.failed_count == 0
        assert score.applicable_count > 0

    def test_deliberately_bad_fixture_fails(self):
        content, pack = deliberately_bad_report()
        score = score_report(content, pack)
        assert score.failed_count >= 4
