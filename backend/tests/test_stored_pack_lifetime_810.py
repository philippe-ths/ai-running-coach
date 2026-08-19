"""The stored context pack's readability lifetime (#810, ADR 0032).

A `coach_reports` row stores the pack its report was generated from, and the eval
harness re-parses that pack later to score the report. The pack schema keeps
evolving under `extra="forbid"`, so old packs stop parsing. This suite pins the
declared policy: packs written under a prompt id in `UNREADABLE_PACK_PROMPT_IDS`
are settled history and become a COUNTED, non-scoring outcome; any other parse
failure stays a loud error; and neither can be mistaken for a report that parsed
and scored badly.

GROUND TRUTH for the three unreadable fixtures. The issue measured three failure
shapes on a production snapshot, all on `coach_report_v1`. The pack shapes below
are not invented: each is the real `CoachContextPack` declaration of its era,
read back out of git history, so the rejections these fixtures produce are the
rejections production produces.

  * `_ERA_FIRST_TYPED_PACK`: the pack as first typed, commit b1821ea
    ("refactor(coach): type the context pack with a Pydantic schema (#43)").
    Six top-level sections; `metrics.activity_class` before the ADR 0007
    classification axes replaced it. Reproduces failure shape 1: a missing
    `our_thread` group, several `metrics` fields absent, `activity_class`
    rejected as an extra key.
  * `_ERA_PRE_DISCOUNT_SIGNALS`: commit 603f955 ("feat(coach): version-aware
    report cache + medical-scope validator rule (M0)"), the last shape before
    5dccfb1 added `discount_signals`. Metrics already carries the classification
    axes. Reproduces failure shape 3: a missing `our_thread` with
    `metrics.discount_signals` absent.
  * `_ERA_PRE_ADHERENCE`: commit 50a7717, the last shape before 63ab77b
    ("feat(coach): adherence learning loop (M7)") added `adherence`. Top level
    carries `longitudinal` and `perceived_effort`, so the `our_thread` group
    exists but its required `adherence` does not. Reproduces failure shape 2.
    The two section BODIES are taken from the current eval fixtures so that the
    missing `adherence` is the only rejection, which is the shape the issue
    names.

MEASURED against a seeded local snapshot of production (`make seed-local`), on
2026-08-19: 133 stored packs, 124 readable, 9 unreadable, every unreadable one
`coach_report_v1`, and ZERO parse failures outside the declared cutoff. The three
rejection shapes on those nine rows are exactly the three the issue names, and
the fixtures below reproduce them error-for-error (`MEASURED_REJECTIONS`). Note
that 11 further `coach_report_v1` rows in that snapshot DO parse, which is why
the declaration says which prompt ids are ALLOWED to be unreadable rather than
predicting which rows will be. The count itself is not asserted (a snapshot is
one runner's history, and the issue measured a different one: 131/105/26); the
assertions are about the CLASSIFICATION being closed and correct.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.models.activity import Activity
from app.models.coach_report import CoachReport
from app.models.user import User
from app.schemas.coach_context import (
    UNREADABLE_PACK_PROMPT_IDS,
    CoachContextPack,
    StoredPackUnreadable,
    load_stored_pack,
)
from app.services.coach.chat import (
    _render_authority_tiering,
    _validate_conversational_text,
)
from app.services.coach.eval.fixtures import (
    deliberately_bad_report,
    known_good_report,
)
from app.services.coach.eval.harness import compare_scorecards, score_db_reports
from app.services.coach.prompt_archive import ARCHIVED_PROMPT_VERSIONS
from app.services.coach.service import SCHEMA_VERSION

RETIRED_PROMPT_ID = "coach_report_v1"
LIVE_PROMPT_ID = "coach_report_v2"  # retired too, but NOT past the declared cutoff


# --- The three historical pack shapes -------------------------------------

_ACTIVITY = {
    "date": "2026-02-15T10:00:00+00:00", "name": "Run", "type": "Run",
    "distance_m": 10000, "moving_time_s": 3600,
    "avg_hr": 150.0, "max_hr": 175.0, "avg_cadence": 170.0, "elev_gain_m": 50.0,
}
_CHECK_IN = {"rpe": 6, "pain_score": 0, "pain_location": None,
             "sleep_quality": 4, "notes": None}
_PROFILE = {
    "goal_type": None, "experience_level": None, "weekly_days_available": None,
    "injury_notes": None, "max_hr": None, "max_hr_source": None,
    "current_weekly_km": None,
}
_PERIOD = {"activity_count": 0, "total_distance_m": 0,
           "total_moving_time_s": 0, "total_effort": 0.0}
_RECENT = {"last_7d": _PERIOD, "last_28d": _PERIOD, "previous_28d": _PERIOD}
_SAFETY = {"never_diagnose": True, "pain_severe_threshold": 7,
           "no_invented_facts": True}

# Shape 1: the pack as first typed (b1821ea).
_ERA_FIRST_TYPED_PACK = {
    "activity": _ACTIVITY,
    "metrics": {
        "activity_class": "easy_run",
        "effort_score": 3.0, "hr_drift": 9.0, "pace_variability": None,
        "flags": [], "confidence": "high", "confidence_reasons": [],
        "time_in_zones": None, "zones_calibrated": True,
        "zones_basis": "user_user_entered",
        "efficiency_analysis": None, "stops_analysis": None,
        "interval_structure": None, "workout_match": None, "interval_kpis": None,
        "risk_level": None, "risk_score": None, "risk_reasons": [],
        "training_context": None,
    },
    "check_in": _CHECK_IN,
    "profile": _PROFILE,
    "recent_training_summary": _RECENT,
    "safety_rules": _SAFETY,
}

# Shape 3: classification axes present, discount_signals not yet (603f955).
_ERA_PRE_DISCOUNT_SIGNALS = {
    "activity": _ACTIVITY,
    "metrics": {
        "headline": "Easy run", "effort": "easy", "duration_class": "standard",
        "structure": "continuous", "is_hilly": False, "is_race": False,
        "effort_score": 3.0, "hr_drift": 9.0, "pace_variability": None,
        "flags": [], "confidence": "high", "confidence_reasons": [],
        "time_in_zones": None, "zones_calibrated": True,
        "zones_basis": "user_user_entered",
        "efficiency_analysis": None, "stops_analysis": None,
        "interval_structure": None, "workout_match": None, "interval_kpis": None,
        "risk_level": None, "risk_score": None, "risk_reasons": [],
        "training_context": None,
    },
    "check_in": _CHECK_IN,
    "profile": _PROFILE,
    "recent_training_summary": _RECENT,
    "safety_rules": _SAFETY,
}

# Shape 2: longitudinal + perceived_effort landed, adherence had not (50a7717).
_ERA_PRE_ADHERENCE = {
    **copy.deepcopy(_ERA_PRE_DISCOUNT_SIGNALS),
    "longitudinal": {"prior_reports": [], "baseline_trend": None},
    "perceived_effort": {
        "rpe": None, "effort_axis": "easy", "effort_score": 3.0,
        "divergence": None, "divergence_direction": None,
        "hr_confounded": False, "recommended_weighting": "hr_only",
        "pain_trend": None,
    },
}
_ERA_PRE_ADHERENCE["metrics"] = {
    **_ERA_PRE_ADHERENCE["metrics"], "discount_signals": None,
}

HISTORICAL_SHAPES = {
    "first_typed_pack": _ERA_FIRST_TYPED_PACK,
    "pre_discount_signals": _ERA_PRE_DISCOUNT_SIGNALS,
    "pre_adherence": _ERA_PRE_ADHERENCE,
}


def _seed_report(db, content, pack_dict, *, prompt_id, schema_version=SCHEMA_VERSION):
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
    row = CoachReport(
        activity_id=activity.id,
        prompt_id=prompt_id,
        schema_version=schema_version,
        report=content.model_dump(mode="json"),
        meta={"confidence": "high", "model_id": "test", "prompt_id": prompt_id,
              "schema_version": schema_version, "input_hash": "x",
              "generated_at": "2026-05-27T10:00:00+00:00", "policy_violations": []},
        context_pack=pack_dict,
        raw_llm_response=None,
        is_fallback=False,
    )
    db.add(row)
    db.commit()
    return row


# --- The fixtures really are unreadable ------------------------------------


# The rejection each shape MUST produce, as a set of `field [error type]` pairs.
# These are not hand-written expectations: they are the rejections the nine
# genuinely-unreadable production packs produce, read off a seeded local snapshot
# (see the module docstring). A fixture whose rejection drifts from these has
# stopped standing in for the real thing.
MEASURED_REJECTIONS = {
    "first_typed_pack": {
        "our_thread [missing]",
        "this_run.metrics.activity_class [extra_forbidden]",
        "this_run.metrics.discount_signals [missing]",
        "this_run.metrics.duration_class [missing]",
        "this_run.metrics.effort [missing]",
        "this_run.metrics.headline [missing]",
        "this_run.metrics.is_hilly [missing]",
        "this_run.metrics.is_race [missing]",
        "this_run.metrics.structure [missing]",
    },
    "pre_discount_signals": {
        "our_thread [missing]",
        "this_run.metrics.discount_signals [missing]",
    },
    # The group itself exists here (longitudinal landed before adherence), so the
    # rejection is the missing member, not the missing group.
    "pre_adherence": {"our_thread.adherence [missing]"},
}


def _rejection_set(exc: ValidationError) -> set:
    return {
        ".".join(str(p) for p in e["loc"]) + f" [{e['type']}]"
        for e in exc.errors()
    }


class TestHistoricalShapesAreGenuinelyRejected:
    """If a fixture parsed, every test below it would be vacuous."""

    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_shape_is_rejected_by_the_current_schema(self, name):
        with pytest.raises(ValidationError):
            CoachContextPack.load(copy.deepcopy(HISTORICAL_SHAPES[name]))

    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_shape_fails_exactly_the_measured_way(self, name):
        with pytest.raises(ValidationError) as exc:
            CoachContextPack.load(copy.deepcopy(HISTORICAL_SHAPES[name]))
        assert _rejection_set(exc.value) == MEASURED_REJECTIONS[name]

    def test_the_three_shapes_are_distinct(self):
        # Three shapes were measured, so three fixtures must differ. Two fixtures
        # that collapsed onto one rejection would test one thing three times.
        assert len({frozenset(v) for v in MEASURED_REJECTIONS.values()}) == 3


# --- The declared cutoff ----------------------------------------------------


class TestReadabilityCutoffDeclaration:
    def test_every_declared_id_is_a_retired_prompt(self):
        for prompt_id in UNREADABLE_PACK_PROMPT_IDS:
            assert prompt_id in ARCHIVED_PROMPT_VERSIONS, (
                f"{prompt_id!r} is declared beyond the pack readability cutoff but "
                "is not in the retired prompt archive."
            )

    def test_the_active_prompt_is_never_past_the_cutoff(self):
        # The cutoff describes settled history. Declaring the LIVE prompt would
        # turn today's schema drift into a silent count.
        assert settings.COACH_PROMPT_ID not in UNREADABLE_PACK_PROMPT_IDS


class TestLoadStoredPack:
    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_retired_prompt_raises_the_distinct_type(self, name):
        with pytest.raises(StoredPackUnreadable) as exc:
            load_stored_pack(
                copy.deepcopy(HISTORICAL_SHAPES[name]), prompt_id=RETIRED_PROMPT_ID
            )
        assert exc.value.prompt_id == RETIRED_PROMPT_ID
        assert exc.value.detail  # the rejection reason is carried, not swallowed

    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_same_shape_under_a_live_prompt_stays_loud(self, name):
        # The identical bytes under an undeclared prompt id are live drift.
        with pytest.raises(ValidationError):
            load_stored_pack(
                copy.deepcopy(HISTORICAL_SHAPES[name]), prompt_id=LIVE_PROMPT_ID
            )

    def test_unknown_prompt_id_stays_loud(self):
        with pytest.raises(ValidationError):
            load_stored_pack(copy.deepcopy(_ERA_FIRST_TYPED_PACK), prompt_id=None)

    def test_a_current_pack_still_loads_under_a_retired_prompt_id(self):
        # The declaration says which ids are ALLOWED to be unreadable, never that
        # they must be: a v1 row whose pack does parse is parsed.
        _, pack = known_good_report()
        loaded = load_stored_pack(
            pack.to_serializable_dict(), prompt_id=RETIRED_PROMPT_ID
        )
        assert loaded.fingerprint() == pack.fingerprint()


# --- The scorecard tells the three outcomes apart --------------------------


class TestScorecardDistinguishesUnreadableFromBadlyScored:
    def test_current_pack_still_parses_and_scores(self, db):
        content, pack = known_good_report()
        _seed_report(db, content, pack.to_serializable_dict(), prompt_id=LIVE_PROMPT_ID)
        card = score_db_reports(db, prompt_id=LIVE_PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert len(card.report_scores) == 1
        assert card.unreadable_packs == []
        assert card.errors == []
        assert card.overall_pass_rate == 1.0

    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_unreadable_pack_is_counted_not_errored(self, db, name):
        content, _ = known_good_report()
        row = _seed_report(
            db, content, copy.deepcopy(HISTORICAL_SHAPES[name]),
            prompt_id=RETIRED_PROMPT_ID,
        )
        card = score_db_reports(
            db, prompt_id=RETIRED_PROMPT_ID, schema_version=SCHEMA_VERSION
        )
        assert card.report_scores == []      # contributes no assertions at all
        assert card.errors == []             # and is NOT an error
        assert len(card.unreadable_packs) == 1
        entry = card.unreadable_packs[0]
        assert entry["report_id"] == str(row.id)
        assert entry["prompt_id"] == RETIRED_PROMPT_ID
        assert entry["detail"]

    def test_unreadable_pack_under_a_live_prompt_stays_an_error(self, db):
        content, _ = known_good_report()
        _seed_report(
            db, content, copy.deepcopy(_ERA_FIRST_TYPED_PACK), prompt_id=LIVE_PROMPT_ID
        )
        card = score_db_reports(db, prompt_id=LIVE_PROMPT_ID, schema_version=SCHEMA_VERSION)
        assert card.unreadable_packs == []
        assert len(card.errors) == 1

    def test_unreadable_is_distinguishable_from_a_bad_score(self, db):
        """The AC in one assertion: a pack that could not be read and a report
        that read fine and scored badly must not look the same."""
        bad_content, bad_pack = deliberately_bad_report()
        _seed_report(
            db, bad_content, bad_pack.to_serializable_dict(), prompt_id=RETIRED_PROMPT_ID
        )
        good_content, _ = known_good_report()
        _seed_report(
            db, good_content, copy.deepcopy(_ERA_FIRST_TYPED_PACK),
            prompt_id=RETIRED_PROMPT_ID,
        )

        card = score_db_reports(
            db, prompt_id=RETIRED_PROMPT_ID, schema_version=SCHEMA_VERSION
        )
        data = card.to_dict()

        # The badly-scored one is scored, and its failures are visible.
        assert data["reports_scored"] == 1
        assert data["overall_pass_rate"] < 1.0
        # The unreadable one is a separate, named, counted outcome.
        assert data["skipped_unreadable_pack"] == 1
        assert data["unreadable_packs"][0]["prompt_id"] == RETIRED_PROMPT_ID
        assert data["errors"] == []

    def test_scorecard_states_the_figure_even_when_zero(self, db):
        content, pack = known_good_report()
        _seed_report(db, content, pack.to_serializable_dict(), prompt_id=LIVE_PROMPT_ID)
        data = score_db_reports(
            db, prompt_id=LIVE_PROMPT_ID, schema_version=SCHEMA_VERSION
        ).to_dict()
        assert data["skipped_unreadable_pack"] == 0
        json.dumps(data, sort_keys=True)  # still serialisable


class TestCompareScorecardsFlagsARise:
    def test_a_rise_in_unreadable_packs_is_a_regression(self):
        before = {"overall_pass_rate": 1.0, "assertion_summary": {},
                  "skipped_unreadable_pack": 26}
        after = {"overall_pass_rate": 1.0, "assertion_summary": {},
                 "skipped_unreadable_pack": 40}
        assert any("unreadable stored packs rose 26 -> 40" in r
                   for r in compare_scorecards(before, after))

    def test_a_steady_or_falling_count_is_not_a_regression(self):
        base = {"overall_pass_rate": 1.0, "assertion_summary": {}}
        assert compare_scorecards({**base, "skipped_unreadable_pack": 26},
                                  {**base, "skipped_unreadable_pack": 26}) == []
        assert compare_scorecards({**base, "skipped_unreadable_pack": 26},
                                  {**base, "skipped_unreadable_pack": 3}) == []

    def test_a_scorecard_predating_the_field_is_not_a_false_alarm(self):
        before = {"overall_pass_rate": 1.0, "assertion_summary": {}}
        after = {"overall_pass_rate": 1.0, "assertion_summary": {},
                 "skipped_unreadable_pack": 26}
        assert compare_scorecards(before, after) == []


# --- The conversational floor does not depend on a readable pack -----------


class TestConversationalFloorSurvivesAnUnreadablePack:
    """ADR 0024: the medical-scope rule is pack-independent BY DESIGN. #810 must
    not quietly make it pack-dependent, so pin it against the unreadable packs."""

    MEDICAL_TEXT = "That is patellar tendinitis. Take 400 mg of ibuprofen twice a day."

    def test_medical_floor_fires_with_no_pack_at_all(self):
        violations = _validate_conversational_text(
            self.MEDICAL_TEXT, zones_calibrated=True, sessions_in_play=[]
        )
        assert any(v.rule == "medical_overreach" for v in violations)

    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_authority_tiering_degrades_without_raising(self, name):
        # The tiering renderer reads a pack tolerantly (unnest_pack, non-validating)
        # rather than strict-parsing it, so an unreadable pack costs a briefing at
        # most, never the turn. The floor header is emitted regardless.
        block = _render_authority_tiering(
            copy.deepcopy(HISTORICAL_SHAPES[name]),
            voice_present=False,
            conversation_present=False,
        )
        assert block
        assert "measured" in block.lower()

    @pytest.mark.parametrize("name", sorted(HISTORICAL_SHAPES))
    def test_floor_still_fires_on_a_turn_whose_pack_is_unreadable(self, name):
        _render_authority_tiering(
            copy.deepcopy(HISTORICAL_SHAPES[name]),
            voice_present=False,
            conversation_present=False,
        )
        violations = _validate_conversational_text(
            self.MEDICAL_TEXT, zones_calibrated=True, sessions_in_play=[]
        )
        assert any(v.rule == "medical_overreach" for v in violations)
