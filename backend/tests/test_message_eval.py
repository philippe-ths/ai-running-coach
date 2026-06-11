"""Eval-gate coverage for the A3 prose-message shape (deliverable 7).

Pins that the rubric extractors branch correctly over the union, that the message
fixtures are a clean inverted oracle, that the harness loader parses 2.x rows as
CoachMessageReport, and that the tail_degraded counter tallies degraded tails.
"""

from datetime import datetime
from uuid import uuid4

from app.models import Activity, StravaAccount, User
from app.models.coach_report import CoachReport
from app.services.coach.eval.fixtures import (
    deliberately_bad_message_report,
    known_good_message_report,
)
from app.services.coach.eval.harness import run_self_test, score_db_reports
from app.services.coach.eval.rubric import AssertionStatus, score_report


class TestMessageRubric:
    def test_known_good_message_passes_all_applicable(self):
        content, pack = known_good_message_report()
        score = score_report(content, pack)
        assert score.failed_count == 0
        assert score.applicable_count > 0

    def test_deliberately_bad_message_fails_every_assertion(self):
        content, pack = deliberately_bad_message_report()
        score = score_report(content, pack)
        failed = {a.name for a in score.assertions if a.status is AssertionStatus.FAIL}
        all_names = {a.name for a in score.assertions}
        assert failed == all_names, f"not failed: {sorted(all_names - failed)}"

    def test_self_test_covers_both_shapes(self):
        ok, report = run_self_test()
        assert ok, report
        assert "message fixture" in report
        assert "structured fixture" in report


def _seed_message_report(db, *, schema_version, report, is_fallback=False):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=int(uuid4().int % 1_000_000),
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 1_000_000),
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500,
        elev_gain_m=10.0, avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    _, pack = known_good_message_report()
    db.add(CoachReport(
        activity_id=activity.id, prompt_id="coach_message_v1",
        schema_version=schema_version, report=report,
        meta={}, context_pack=pack.to_serializable_dict(),
        is_fallback=is_fallback,
    ))
    db.commit()
    return activity


class TestHarnessMessageLoader:
    def test_loader_scores_2_0_rows_as_message(self, db):
        content, _ = known_good_message_report()
        _seed_message_report(db, schema_version="2.0", report=content.model_dump())
        card = score_db_reports(
            db, prompt_id="coach_message_v1", schema_version="2.0"
        )
        assert len(card.report_scores) == 1
        assert card.errors == []
        assert card.report_scores[0].failed_count == 0

    def test_tail_degraded_counter(self, db):
        content, _ = known_good_message_report()
        degraded = content.model_dump()
        degraded["tail_degraded"] = True
        degraded["next_steps"] = []
        _seed_message_report(db, schema_version="2.0", report=degraded)
        card = score_db_reports(
            db, prompt_id="coach_message_v1", schema_version="2.0"
        )
        assert card.tail_degraded == 1
        assert card.to_dict()["tail_degraded"] == 1
