"""ADR 0026 Slice 2 PR 2 (#670): the rebased `training_history` ladder in the coach pack.

Under the grouped_v2 prompt the `the_runner.training_history` section is REBASED to begin
after the 2-week `recent_weeks` window (a 14-60d bridging bucket) and each bucket is
enriched with a modality-agnostic weekly load, a per-type split, and calendar bounds. Under
every prior prompt (prod lean_v1, grouped_v1, v12) the ORIGINAL 60d ladder emits with none
of those keys, so the prior packs stay byte-identical. Both signals write the ONE
`training_history` pack key; they are mutually exclusive by prompt feature.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User
from app.schemas.coach_context import CoachContextPack
from app.services.coach.context import build_context_pack

# The grouped_v2-only enrichment keys that must NOT leak into any prior prompt's pack.
_BUCKET_ENRICHMENTS = ("from_date", "to_date", "avg_weekly_load", "by_type")
_TRAIT_ENRICHMENTS = ("peak_sustained_weekly_load", "current_vs_peak_load_pct")

GROUPED2 = "coach_message_lean_grouped_v2"
PRIOR = "coach_message_lean_v1"  # carries the original TRAINING_HISTORY ladder


def _seed(db) -> Activity:
    """~2 years of history (enough to cross the 42-day / 8-activity floor and paint several
    ladder buckets), with a cross-training block so by_type has a non-run type to separate."""
    user = User(id=uuid.uuid4(), email=f"th-{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    base = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    subject = None
    for w in range(104):  # one activity per week, ~2 years back
        start = base - timedelta(days=7 * w)
        is_ski = 26 <= w < 34  # a cross-training block ~6-8 months back
        act = Activity(
            id=uuid.uuid4(),
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            name="Session",
            type="BackcountrySki" if is_ski else "Run",
            start_date=start,
            start_date_local=start,
            distance_m=40_000 if is_ski else 12_000,
            moving_time_s=6000 if is_ski else 3000,
            elapsed_time_s=6000 if is_ski else 3000,
            avg_hr=150.0,
            raw_summary={},
        )
        db.add(act)
        db.flush()
        db.add(
            DerivedMetric(
                id=uuid.uuid4(),
                activity_id=act.id,
                effort="easy",
                structure="continuous",
                duration_class="standard",
                effort_score=200.0 if is_ski else 90.0,
                hr_drift=3.0,
                flags=[],
                confidence="high",
                confidence_reasons=[],
            )
        )
        db.flush()
        if w == 0:
            subject = act
    db.refresh(subject)
    return subject


def test_grouped_v2_training_history_is_rebased_and_enriched(db):
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id=GROUPED2)

    assert pack.training_history is not None
    th = pack.to_serializable_dict()["training_history"]

    # The rebased ladder carries the 14-60d bridging bucket the 60d ladder omits.
    labels = {b["label"] for b in th["timeline"]}
    assert "2 weeks - 2 months ago" in labels

    # Every bucket is enriched, and the cross-training block reads as its own type.
    for bucket in th["timeline"]:
        for k in _BUCKET_ENRICHMENTS:
            assert k in bucket, (bucket["label"], k)
    all_types = {t["type"] for b in th["timeline"] for t in b["by_type"]}
    assert "BackcountrySki" in all_types

    # Load traits ride the section.
    for k in _TRAIT_ENRICHMENTS:
        assert k in th["traits"]

    # It lives under the_runner in the grouped serialization.
    assert "training_history" in pack.to_grouped_dict()["the_runner"]


def test_prior_prompt_training_history_is_byte_stable(db):
    """Under a prior prompt the ORIGINAL 60d ladder emits with NONE of the grouped_v2
    enrichment keys anywhere in the section — so the prior pack fingerprint is unchanged."""
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id=PRIOR)

    assert pack.training_history is not None
    th = pack.to_serializable_dict()["training_history"]

    assert "2 weeks - 2 months ago" not in {b["label"] for b in th["timeline"]}
    for bucket in th["timeline"]:
        for k in _BUCKET_ENRICHMENTS:
            assert k not in bucket, (bucket["label"], k)
    for k in _TRAIT_ENRICHMENTS:
        assert k not in th["traits"]
    # The pre-existing Optional trait nulls are untouched by the surgical drop.
    assert "current_vs_peak_pct" in th["traits"]


def test_grouped_v2_pack_strict_reparses(db):
    """The chat read path + eval harness strict-re-parse a STORED pack; both the flat and
    grouped serializations of the enriched section must round-trip through load()."""
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id=GROUPED2)
    for shape in (pack.to_serializable_dict(), pack.to_grouped_dict()):
        reloaded = CoachContextPack.load(shape)
        assert reloaded.training_history is not None
        assert reloaded.training_history.timeline[0].by_type is not None
