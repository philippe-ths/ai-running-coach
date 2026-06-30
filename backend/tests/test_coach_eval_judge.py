"""Tests for the opt-in semantic-judge eval layer (#164).

The judge calls an LLM, so every test injects a FAKE structured client (no API key,
no cost, deterministic). The fakes stand in for the judge's verdict so we exercise the
real wiring: prompt rendering, strict coercion, aggregation, the scorecard section, the
DB walk, error handling, and the advisory compare — never a real API call.

Fixtures reuse the harness's synthetic good/bad reports (trust level 5, NOT production
data). A "smart" fake reads the rendered report and returns high scores for a clean
report and low scores for the deliberately-bad one, so we can assert the layer
distinguishes them end-to-end through the structured pipeline.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.activity import Activity
from app.models.coach_report import CoachReport
from app.models.user import User
from app.services.coach.eval.fixtures import (
    deliberately_bad_message_report,
    deliberately_bad_report,
    known_good_message_report,
    known_good_report,
)
from app.services.coach.eval.harness import (
    Scorecard,
    compare_judge_sections,
    judge_db_reports,
    run_self_test,
    score_db_reports,
)
from app.services.coach.eval.judge import (
    JUDGE_CRITERIA,
    JudgeReportScore,
    JudgeVerdict,
    judge_report,
    render_judge_messages,
    summarize_judge_scores,
)
from app.services.coach.service import SCHEMA_VERSION

PROMPT_ID = "coach_report_v2"
MESSAGE_PROMPT_ID = "coach_message_v8"
MESSAGE_SCHEMA = "2.0"


# --- fake structured clients --------------------------------------------------


def _verdict_dict(score: int, note: str = "ok") -> dict:
    return {
        **{name: {"score": score, "reason": f"{name} reason"} for name in JUDGE_CRITERIA},
        "overall_note": note,
    }


class FixedJudgeClient:
    """Returns the same verdict for every call. Records the calls for assertions."""

    def __init__(self, score: int):
        self.score = score
        self.calls: list[dict] = []

    async def generate_structured(self, *, system, user, tool, max_tokens=1024):
        self.calls.append({"system": system, "user": user, "tool": tool})
        return _verdict_dict(self.score)


class SmartJudgeClient:
    """A stand-in judge: low scores when the rendered report carries a bad marker
    (medical overreach / ungrounded trend / nagging), high otherwise. Lets us assert
    the layer separates the good fixture from the deliberately-bad one."""

    # Specific to the deliberately-bad fixture's PROSE, not pack keys like
    # `never_diagnose` (which the good fixture also carries).
    BAD_MARKERS = ("i would diagnose", "trending upward", "keep ignoring")

    async def generate_structured(self, *, system, user, tool, max_tokens=1024):
        low = any(marker in user.lower() for marker in self.BAD_MARKERS)
        return _verdict_dict(2 if low else 5)


class MalformedJudgeClient:
    """Returns an out-of-range score so strict coercion rejects it."""

    async def generate_structured(self, *, system, user, tool, max_tokens=1024):
        bad = _verdict_dict(5)
        bad["human_voice"]["score"] = 9  # outside 1-5
        return bad


# --- DB seeding ---------------------------------------------------------------


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


# --- judge_report + coercion --------------------------------------------------


class TestJudgeReport:
    @pytest.mark.asyncio
    async def test_coerces_valid_verdict(self):
        content, pack = known_good_report()
        verdict = await judge_report(FixedJudgeClient(4), content, pack)
        assert isinstance(verdict, JudgeVerdict)
        assert verdict.human_voice.score == 4
        assert set(verdict.scores()) == set(JUDGE_CRITERIA)

    @pytest.mark.asyncio
    async def test_malformed_verdict_raises_validation_error(self):
        content, pack = known_good_report()
        with pytest.raises(ValidationError):
            await judge_report(MalformedJudgeClient(), content, pack)

    @pytest.mark.asyncio
    async def test_good_report_scores_higher_than_bad(self):
        good_c, good_p = known_good_report()
        bad_c, bad_p = deliberately_bad_report()
        good = await judge_report(SmartJudgeClient(), good_c, good_p)
        bad = await judge_report(SmartJudgeClient(), bad_c, bad_p)
        good_mean = sum(good.scores().values()) / len(JUDGE_CRITERIA)
        bad_mean = sum(bad.scores().values()) / len(JUDGE_CRITERIA)
        assert good_mean > bad_mean

    @pytest.mark.asyncio
    async def test_works_for_prose_message_shape(self):
        good_c, good_p = known_good_message_report()
        bad_c, bad_p = deliberately_bad_message_report()
        good = await judge_report(SmartJudgeClient(), good_c, good_p)
        bad = await judge_report(SmartJudgeClient(), bad_c, bad_p)
        assert sum(good.scores().values()) > sum(bad.scores().values())

    def test_render_includes_report_and_pack(self):
        content, pack = known_good_report()
        system, user = render_judge_messages(content, pack)
        assert "evaluator" in system.lower()  # system frames the judge task
        assert "COACH REPORT" in user and "CONTEXT PACK" in user
        # The prior digest (used for non_samey) reaches the judge via the pack.
        assert "Tempo run" in user


# --- judge_db_reports walk ----------------------------------------------------


class TestJudgeDbReports:
    @pytest.mark.asyncio
    async def test_judges_current_version_reports(self, db):
        _seed_report(db, *known_good_report())
        _seed_report(db, *deliberately_bad_report())
        scores, errors = await judge_db_reports(
            db, SmartJudgeClient(), prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION
        )
        assert len(scores) == 2
        assert errors == []
        assert all(isinstance(s, JudgeReportScore) for s in scores)

    @pytest.mark.asyncio
    async def test_skips_fallback(self, db):
        _seed_report(db, *known_good_report(), is_fallback=True)
        scores, errors = await judge_db_reports(
            db, FixedJudgeClient(4), prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION
        )
        assert scores == [] and errors == []

    @pytest.mark.asyncio
    async def test_malformed_verdict_recorded_as_error_not_crash(self, db):
        _seed_report(db, *known_good_report())
        scores, errors = await judge_db_reports(
            db, MalformedJudgeClient(), prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION
        )
        assert scores == []
        assert len(errors) == 1
        assert "report_id" in errors[0] and "error" in errors[0]

    @pytest.mark.asyncio
    async def test_limit_caps_judged_count(self, db):
        _seed_report(db, *known_good_report())
        _seed_report(db, *known_good_report())
        client = FixedJudgeClient(4)
        scores, _ = await judge_db_reports(
            db, client, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION, limit=1
        )
        assert len(scores) == 1
        assert len(client.calls) == 1  # the cap stops further calls (cost control)


# --- aggregation + scorecard section ------------------------------------------


class TestScorecardJudgeSection:
    def test_default_scorecard_has_no_judge_section(self, db):
        _seed_report(db, *known_good_report())
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert "judge" not in card.to_dict()  # opt-in: absent unless the judge ran

    @pytest.mark.asyncio
    async def test_judge_section_present_and_labelled_when_attached(self, db):
        _seed_report(db, *known_good_report())
        card = score_db_reports(db, prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION)
        det_summary_before = card.to_dict()["assertion_summary"]
        scores, errors = await judge_db_reports(
            db, FixedJudgeClient(5), prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION
        )
        card.judge_scores = scores
        card.judge_errors = errors
        data = card.to_dict()
        assert "judge" in data
        assert data["judge"]["reports_judged"] == 1
        assert data["judge"]["criterion_summary"]["human_voice"]["mean"] == 5.0
        # The deterministic section is untouched by the judge.
        assert data["assertion_summary"] == det_summary_before
        assert "overall_pass_rate" in data

    def test_summarize_empty_is_zero_count(self):
        summary = summarize_judge_scores([])
        assert summary["human_voice"]["count"] == 0
        assert summary["human_voice"]["mean"] is None


# --- advisory compare ---------------------------------------------------------


class TestCompareJudgeSections:
    def _card_with_judge(self, score: int) -> dict:
        verdict = JudgeVerdict.model_validate(_verdict_dict(score))
        js = JudgeReportScore(verdict=verdict, report_id="r1")
        card = Scorecard(report_scores=[], judge_scores=[js])
        return card.to_dict()

    def test_flags_meaningful_drop(self):
        before = self._card_with_judge(5)
        after = self._card_with_judge(3)  # a 2-point drop, well over the 0.5 threshold
        drift = compare_judge_sections(before, after)
        assert any("human_voice" in line for line in drift)

    def test_ignores_small_wobble(self):
        before = self._card_with_judge(4)
        after = self._card_with_judge(4)
        assert compare_judge_sections(before, after) == []

    def test_no_judge_section_means_no_drift(self):
        before = {"overall_pass_rate": 1.0}  # no judge section
        after = self._card_with_judge(3)
        assert compare_judge_sections(before, after) == []


# --- the judge must not perturb the keyless deterministic floor ---------------


class TestDeterministicFloorUnaffected:
    def test_self_test_still_passes_without_judge(self):
        # `make eval-selftest` runs this; it must stay green with no API key and no
        # judge involvement.
        ok, report = run_self_test()
        assert ok, report
        assert "judge" not in report.lower()
