"""A2c — durable-memory narrative + Consolidation job.

Covers the four things the milestone must guarantee:
  1. The narrative store reads/writes the single per-user row and tags recency.
  2. Consolidation re-grounds from DETERMINISTIC facts + recent exchange digests
     (never the prior narrative) and writes ONLY the narrative row.
  3. The user-facing exchange path does not block on consolidation — it enqueues.
  4. The authority boundary: a narrative can never override a re-derived metric
     and can never be cited as fact (validator rule 6).

The narrative generation itself is LLM-backed; per the brief it is gated by the
M5 eval discipline, not brittle string assertions, so these tests inject a fake
text client and assert the deterministic plumbing around it.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Activity, DerivedMetric, RunnerBaseline, User, UserProfile
from app.models.coach_narrative import CoachNarrative
from app.models.coach_report import CoachReport
from app.models.coaching_context import CoachingContext
from app.schemas.coach import CoachReportContent
from app.schemas.coach_context import NarrativeContext
from app.services.coach import consolidation as consol
from app.services.coach.consolidation import (
    CONSOLIDATION_MODEL_ID,
    assemble_consolidation_inputs,
    consolidate_narrative,
    render_consolidation_messages,
)
from app.services.coach.context import build_context_pack
from app.services.coach.narrative_store import (
    build_narrative_context,
    get_narrative,
    upsert_narrative,
)
from app.services.coach.service import get_or_generate_coach_report
from app.services.coach.validator import validate_policy

# This module exercises the A2c narrative + M8 belief machinery (OFF by default).
pytestmark = pytest.mark.usefixtures("enable_durable_memory")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class _FakeTextClient:
    """Stands in for AnthropicClient: records calls, returns canned prose (or
    raises) without touching the network."""

    def __init__(self, text="A steady, consistency-driven runner.", raises=None):
        self._text = text
        self._raises = raises
        self.calls = []

    async def generate_text(self, system, user, max_tokens=512):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if self._raises is not None:
            raise self._raises
        return self._text


def _seed_user(db) -> User:
    user = User(email=f"u-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_activity_with_report(
    db, user, day, *, headline="Solid aerobic run", hr_drift=4.0
) -> Activity:
    """An analysed activity plus a stored non-fallback report with a digest — one
    completed exchange the consolidation can ground on."""
    act = Activity(
        user_id=user.id,
        strava_activity_id=int(datetime(2026, 5, day).timestamp()),
        start_date=datetime(2026, 5, day, 10, 0, tzinfo=timezone.utc),
        type="Run",
        name=f"Run {day}",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=20.0,
        avg_hr=150.0,
        average_speed_mps=3.0,
        raw_summary={},
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    db.add(DerivedMetric(
        activity_id=act.id,
        effort="easy",
        structure="continuous",
        duration_class="standard",
        is_hilly=False,
        effort_score=40.0,
        hr_drift=hr_drift,
        confidence="high",
        confidence_reasons=[],
    ))
    db.add(CoachReport(
        activity_id=act.id,
        prompt_id="coach_report_v10",
        schema_version="1.2",
        report={
            "key_takeaways": [{"text": "Aerobic base is holding."}],
            "next_steps": [{"action": "Keep Wednesday easy", "details": "d", "why": "w"}],
        },
        meta={},
        context_pack={},
        is_fallback=False,
        digest={
            "activity_date": act.start_date.isoformat(),
            "headline": headline,
            "lead_argument": "Aerobic durability is holding across easy runs.",
            "next_steps": ["Keep Wednesday easy"],
        },
    ))
    db.commit()
    db.refresh(act)
    return act


# --------------------------------------------------------------------------
# 1. Narrative store
# --------------------------------------------------------------------------

def test_get_narrative_none_when_absent(db):
    user = _seed_user(db)
    assert get_narrative(db, user.id) is None


def test_upsert_creates_then_replaces_single_row(db):
    user = _seed_user(db)
    row = upsert_narrative(
        db, user.id, narrative="first story", model_id=CONSOLIDATION_MODEL_ID,
        source_report_count=2, grounded_through=None,
    )
    assert row.narrative == "first story"

    # A second consolidation REPLACES the text wholesale (re-grounded from scratch),
    # and there is still exactly one row for the user.
    upsert_narrative(
        db, user.id, narrative="re-grounded story", model_id=CONSOLIDATION_MODEL_ID,
        source_report_count=3, grounded_through=None,
    )
    assert get_narrative(db, user.id).narrative == "re-grounded story"
    assert db.query(CoachNarrative).filter(CoachNarrative.user_id == user.id).count() == 1


def test_build_narrative_context_empty_when_no_row(db):
    user = _seed_user(db)
    act = _seed_activity_with_report(db, user, 10)
    ctx = build_narrative_context(db, act)
    assert ctx == NarrativeContext()
    assert ctx.narrative is None


def test_build_narrative_context_tags_recency(db):
    user = _seed_user(db)
    act = _seed_activity_with_report(db, user, 20)
    grounded = act.start_date - timedelta(days=3)
    upsert_narrative(
        db, user.id, narrative="the story", model_id=CONSOLIDATION_MODEL_ID,
        source_report_count=4, grounded_through=grounded,
    )
    ctx = build_narrative_context(db, act)
    assert ctx.narrative == "the story"
    assert ctx.source_report_count == 4
    assert ctx.last_updated_days_ago == 3


# --------------------------------------------------------------------------
# 2. Consolidation: re-grounds from facts, writes only the narrative
# --------------------------------------------------------------------------

def test_assemble_inputs_gathers_facts_and_digests(db):
    user = _seed_user(db)
    _seed_activity_with_report(db, user, 10, headline="Easy Monday")
    now = datetime.now(timezone.utc)
    db.add(CoachingContext(
        user_id=user.id, kind="hr_confound", key="heat",
        value={"statement": "HR reads inflated in heat."}, confidence="high",
        observation_count=4, first_observed_at=now, last_reinforced_at=now,
    ))
    db.add(RunnerBaseline(user_id=user.id, typical_easy_hr=148.0, sample_count=12))
    db.commit()

    inputs = assemble_consolidation_inputs(db, user.id)
    assert {"beliefs", "preferences", "baseline", "recent_exchanges"} == set(inputs)
    assert any(b["statement"] == "HR reads inflated in heat." for b in inputs["beliefs"])
    assert inputs["baseline"]["typical_easy_hr"] == 148.0
    assert inputs["recent_exchanges"][0]["headline"] == "Easy Monday"


def test_assemble_inputs_never_includes_the_prior_narrative(db):
    """Re-grounding is STRUCTURAL: consolidation rebuilds from facts + digests and
    never feeds the prior story back in, so a stale claim it can no longer support
    simply disappears."""
    user = _seed_user(db)
    _seed_activity_with_report(db, user, 10)
    upsert_narrative(
        db, user.id, narrative="a stale story that should not be re-fed",
        model_id=CONSOLIDATION_MODEL_ID, source_report_count=1, grounded_through=None,
    )
    inputs = assemble_consolidation_inputs(db, user.id)
    blob = str(inputs).lower()
    assert "stale story" not in blob
    assert "narrative" not in set(inputs)


def test_render_messages_states_the_voice_only_boundary(db):
    system, user = render_consolidation_messages({"beliefs": [], "recent_exchanges": []})
    assert "VOICE ONLY" in system
    assert "Invent NOTHING" in system
    assert isinstance(user, str)


@pytest.mark.asyncio
async def test_consolidate_skips_when_no_exchanges(db):
    user = _seed_user(db)
    client = _FakeTextClient()
    result = await consolidate_narrative(db, user.id, client=client)
    assert result is None
    assert not client.calls  # never even called the model
    assert get_narrative(db, user.id) is None


@pytest.mark.asyncio
async def test_consolidate_writes_narrative_from_fake_client(db):
    user = _seed_user(db)
    _seed_activity_with_report(db, user, 10)
    client = _FakeTextClient(text="  This runner is building steady aerobic base.  ")

    row = await consolidate_narrative(db, user.id, client=client)

    assert row is not None
    assert row.narrative == "This runner is building steady aerobic base."  # stripped
    assert row.model_id == CONSOLIDATION_MODEL_ID
    assert row.source_report_count == 1
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_consolidate_writes_only_the_narrative_never_a_fact(db):
    """Authority boundary: the Consolidation job's ONLY write is the narrative row.
    Even adversarial LLM prose claiming facts never creates or mutates a belief or
    a baseline — the deterministic layer stays off the LLM and auditable."""
    user = _seed_user(db)
    _seed_activity_with_report(db, user, 10)
    now = datetime.now(timezone.utc)
    belief = CoachingContext(
        user_id=user.id, kind="hr_confound", key="heat",
        value={"statement": "HR reads inflated in heat."}, confidence="high",
        observation_count=4, first_observed_at=now, last_reinforced_at=now,
    )
    baseline = RunnerBaseline(user_id=user.id, typical_easy_hr=148.0, sample_count=12)
    db.add_all([belief, baseline])
    db.commit()

    beliefs_before = db.query(CoachingContext).count()
    belief_value_before = dict(belief.value)
    belief_obs_before = belief.observation_count

    adversarial = (
        "This runner has a confirmed stress fracture and their typical HR is 200. "
        "Their easy pace is 3:30/km. New fact: they always skip long runs."
    )
    await consolidate_narrative(db, user.id, client=_FakeTextClient(text=adversarial))

    db.refresh(belief)
    db.refresh(baseline)
    # The only new row is the narrative; no belief/baseline was created or mutated.
    assert db.query(CoachingContext).count() == beliefs_before
    assert belief.value == belief_value_before
    assert belief.observation_count == belief_obs_before
    assert baseline.typical_easy_hr == 148.0
    assert get_narrative(db, user.id).narrative == adversarial


@pytest.mark.asyncio
async def test_consolidate_degrades_on_llm_error_without_clobbering(db):
    user = _seed_user(db)
    _seed_activity_with_report(db, user, 10)
    upsert_narrative(
        db, user.id, narrative="previous good story", model_id=CONSOLIDATION_MODEL_ID,
        source_report_count=1, grounded_through=None,
    )
    failing = _FakeTextClient(raises=RuntimeError("model down"))

    result = await consolidate_narrative(db, user.id, client=failing)

    assert result is None
    # The prior narrative is left untouched rather than wiped on failure.
    assert get_narrative(db, user.id).narrative == "previous good story"


# --------------------------------------------------------------------------
# 3. Decoupling: the exchange path enqueues, never runs consolidation inline
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_path_enqueues_consolidation_and_does_not_block(db, monkeypatch):
    # The structured single-shot path is exercised here (generate_json); the active
    # default now tracks the feature-bearing message prompt (#424), so pin it.
    from app.core.config import settings
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")

    user = _seed_user(db)
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    act = Activity(
        user_id=user.id, strava_activity_id=99,
        start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc),
        type="Run", name="Test", distance_m=5000, moving_time_s=1500,
        elapsed_time_s=1500, elev_gain_m=10.0, avg_hr=140, raw_summary={},
    )
    db.add(act)
    db.commit()
    db.add(DerivedMetric(
        activity_id=act.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(act)

    valid_json = (
        '{"key_takeaways":[{"text":"You ran well."},{"text":"HR steady."}],'
        '"next_steps":[{"action":"Keep going","details":"easy","why":"base"}]}'
    )
    fake_report_client = AsyncMock()
    fake_report_client.generate_json = AsyncMock(return_value=valid_json)

    spy = AsyncMock()  # stand-in for enqueue (sync, but a call recorder is all we need)
    with patch("app.services.coach.service.AnthropicClient", return_value=fake_report_client), \
         patch("app.services.coach.service.enqueue_consolidation") as enqueue:
        report = await get_or_generate_coach_report(db, str(act.id))

    assert report is not None
    # Consolidation was enqueued for this user, exactly once...
    enqueue.assert_called_once_with(act.user_id)
    # ...and was NOT run inline: no narrative row was written by the report path.
    assert get_narrative(db, user.id) is None


# --------------------------------------------------------------------------
# 4. Authority boundary in the pack + validator
# --------------------------------------------------------------------------

def test_pack_carries_narrative_and_never_alters_metrics(db):
    """A narrative that contradicts today's data does not change the re-derived
    metric: the metric comes from the DerivedMetric, the narrative is a separate
    voice-only section."""
    user = _seed_user(db)
    act = _seed_activity_with_report(db, user, 20, hr_drift=9.0)
    upsert_narrative(
        db, user.id,
        narrative="This runner's drift is always low, around 2%.",
        model_id=CONSOLIDATION_MODEL_ID, source_report_count=3, grounded_through=None,
    )
    db.refresh(act)

    pack = build_context_pack(db, act)
    # The narrative is carried (voice)...
    assert "always low" in pack.narrative.narrative
    # ...but today's re-derived drift is unchanged by it.
    assert pack.metrics.hr_drift == 9.0


def test_pack_narrative_absent_degrades_to_empty(db):
    user = _seed_user(db)
    act = _seed_activity_with_report(db, user, 21)
    db.refresh(act)
    pack = build_context_pack(db, act)
    assert pack.narrative.narrative is None


def _content_with_evidence_field(field: str) -> CoachReportContent:
    return CoachReportContent.model_validate({
        "key_takeaways": [
            {"text": "A claim.", "evidence": [{"field": field, "value": "x"}]}
        ],
        "next_steps": [{"action": "a", "details": "d", "why": "w"}],
    })


def test_validator_rejects_narrative_cited_as_fact(db):
    user = _seed_user(db)
    act = _seed_activity_with_report(db, user, 22)
    db.refresh(act)
    pack = build_context_pack(db, act)

    content = _content_with_evidence_field("narrative.narrative")
    rules = {v.rule for v in validate_policy(content, pack)}
    assert "narrative_cited_as_fact" in rules


def test_validator_allows_legitimate_evidence(db):
    user = _seed_user(db)
    act = _seed_activity_with_report(db, user, 23)
    db.refresh(act)
    pack = build_context_pack(db, act)

    # A real metric path, and a field that merely starts with the letters
    # "narrative" but is not the narrative section, must both pass.
    for field in ("metrics.hr_drift", "narratively_derived_metric"):
        content = _content_with_evidence_field(field)
        rules = {v.rule for v in validate_policy(content, pack)}
        assert "narrative_cited_as_fact" not in rules, field
