"""Single-home invariant: every fact the pack folds to one place STAYS folded.

`SINGLE_HOME_FACTS` is the authoritative registry of facts that used to appear in
two sections of the context pack. The one-fact-one-place fold dropped the redundant
COPY at serialization and left the fact in exactly one HOME. This test builds a real,
fully-populated pack and asserts, for each entry, that the copy path is ABSENT from
the serialized pack while the home path is PRESENT — so a future change that
re-introduces a copy (a new builder field, a reverted drop) fails HERE.

Why a targeted registry and not a blanket "no scalar value appears twice" check: the
pack legitimately carries the same number in unrelated fields by coincidence (a
pace_variability that happens to equal a norm, a grade that equals a drift), so a
blanket scalar-uniqueness guard is all false positives. The general
byte-identical-subobject guard (test_pack_no_duplicate_content) cannot see scalar
copies either. This registry is the durable backstop for the specific folds.

To add a fold: drop the copy in `coach_context.to_serializable_dict` and add one row
here. To verify the drop is doing real work, each entry's copy is also confirmed to be
present on the MODEL object (the builder still computes it; only serialization drops).
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.user import User
from app.services.coach.context import build_context_pack

# (label, copy_container_path, copy_key, home_container_path, home_key)
# copy_*  -> must be ABSENT from the serialized pack (folded out)
# home_*  -> must be PRESENT in the serialized pack (the one home)
SINGLE_HOME_FACTS = [
    ("rpe",
     ("perceived_effort",), "rpe", ("check_in",), "rpe"),
    ("effort_score",
     ("perceived_effort",), "effort_score", ("metrics",), "effort_score"),
    ("hr_drift via discount_signals",
     ("metrics", "discount_signals"), "hr_drift_pct", ("metrics",), "hr_drift"),
    ("hr_drift via calibration",
     ("calibration", "hr_drift"), "observed_drift_pct", ("metrics",), "hr_drift"),
]


def _dig(d, path):
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _build_fully_populated_pack(db):
    """A real pack where every folded fact's COPY would be populated (a check-in for
    rpe, a stored discount_signals for hr_drift_pct, a non-null drift for calibration's
    observed_drift_pct, a load for effort_score) — so the drop is doing real work."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"sh-{user_id}@example.com"))
    db.flush()
    act = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 3, 1, 10, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=50.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(act)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=act.id,
        effort="easy", duration_class="standard", structure="continuous",
        is_hilly=False, is_race=False, effort_score=42.0, hr_drift=9.0,
        pace_variability=5.0, time_in_zones={"Z1": 600, "Z2": 1200},
        flags=[], confidence="high", confidence_reasons=[],
        discount_signals={
            "hr_drift_pct": 9.0, "likely_inflated_by": ["heat"],
            "temperature_c": 29.0, "confidence": "high", "interpretation": "heat",
        },
    ))
    db.add(CheckIn(id=uuid.uuid4(), activity_id=act.id, rpe=7, pain_score=1))
    db.flush()
    db.refresh(act)
    return build_context_pack(db, act)


def test_folded_facts_each_have_exactly_one_home(db):
    pack = _build_fully_populated_pack(db)

    # The MODEL still carries the copies (the fold is serialization-only; the derived
    # reads compute from them). If this regresses the pack didn't build as intended.
    model = pack.model_dump(mode="python")
    for label, copy_path, copy_key, _home_path, _home_key in SINGLE_HOME_FACTS:
        copy_container = _dig(model, copy_path)
        assert copy_container is not None and copy_container.get(copy_key) is not None, (
            f"{label}: precondition — the copy {copy_path}.{copy_key} should be "
            f"populated on the model so the drop is exercised."
        )

    out = pack.to_serializable_dict()
    for label, copy_path, copy_key, home_path, home_key in SINGLE_HOME_FACTS:
        home_container = _dig(out, home_path)
        assert home_container is not None and home_key in home_container, (
            f"{label}: the single home {home_path}.{home_key} is missing from the pack."
        )
        copy_container = _dig(out, copy_path)
        assert copy_container is None or copy_key not in copy_container, (
            f"{label}: the folded-out copy {copy_path}.{copy_key} reappeared in the "
            f"serialized pack — it must live only at {home_path}.{home_key}."
        )
