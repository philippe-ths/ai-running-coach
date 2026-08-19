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
    deliberately_verbose_message_report,
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

    def test_the_bad_message_fixtures_between_them_fail_every_assertion(self):
        """Every assertion is provoked by one of the bad fixtures.

        Two rather than one since #655: the depth assertion only speaks about a session
        that earned no length, and the original bad fixture fires a referral, which is
        exactly a session whose length IS earned. One synthetic report cannot be both,
        so the coverage claim is a union — and each fixture must still fail something,
        so a fixture that quietly stopped provoking anything is caught rather than
        carried by its sibling."""
        failed = set()
        all_names = set()
        for fixture in (deliberately_bad_message_report, deliberately_verbose_message_report):
            content, pack = fixture()
            score = score_report(content, pack)
            names = {a.name for a in score.assertions if a.status is AssertionStatus.FAIL}
            assert names, f"{fixture.__name__} failed no assertion at all"
            failed |= names
            all_names |= {a.name for a in score.assertions}
        assert failed == all_names, f"not failed: {sorted(all_names - failed)}"

    def test_the_verbose_fixture_is_the_one_that_carries_the_depth_dimension(self):
        """Named, so the union above cannot silently start relying on the wrong half."""
        content, pack = deliberately_verbose_message_report()
        score = score_report(content, pack)
        depth = next(a for a in score.assertions if a.name == "depth_matched_the_session")
        assert depth.status is AssertionStatus.FAIL

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

    def test_opener_only_rows_are_skipped_not_scored(self, db):
        # A4: an opener-only row (opener_message set, message empty) is a not-yet-
        # complete exchange — the harness skips it (the rubric scores the fuller
        # turn only), counted separately from fallbacks.
        content, _ = known_good_message_report()
        opener = content.model_dump()
        opener["opener_message"] = "Nice work — full breakdown to follow."
        opener["message"] = ""
        opener["next_steps"] = []
        _seed_message_report(db, schema_version="2.0", report=opener)
        card = score_db_reports(
            db, prompt_id="coach_message_v1", schema_version="2.0"
        )
        assert card.skipped_opener_only == 1
        assert len(card.report_scores) == 0
        assert card.errors == []  # skipped before parse/score, never an error
        assert card.to_dict()["skipped_opener_only"] == 1

    def test_fuller_rows_still_scored_alongside_opener(self, db):
        # A complete fuller row (non-empty message) is scored; the opener row beside
        # it is skipped.
        content, _ = known_good_message_report()
        _seed_message_report(db, schema_version="2.0", report=content.model_dump())
        opener = content.model_dump()
        opener["opener_message"] = "Quick reaction."
        opener["message"] = ""
        _seed_message_report(db, schema_version="2.0", report=opener)
        card = score_db_reports(
            db, prompt_id="coach_message_v1", schema_version="2.0"
        )
        assert len(card.report_scores) == 1
        assert card.skipped_opener_only == 1
