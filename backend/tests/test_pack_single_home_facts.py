"""Single-home invariant: every fact the pack folds to one place STAYS folded.

`SINGLE_HOME_FACTS` is the authoritative registry of facts that used to appear in
two sections of the context pack. The one-fact-one-place folds dropped the redundant
COPY at serialization and left the fact in exactly one HOME. This test builds a real,
fully-populated v11 pack (history + check-in + discount signals, so every folded
fact's copy WOULD be present) and asserts, for each entry, that the copy is ABSENT
from the serialized pack while the home is PRESENT — so a future change that
re-introduces a copy (a new builder field, a reverted drop) fails HERE.

Why a targeted registry and not a blanket "no value appears twice" check: the pack
legitimately carries the same number in unrelated fields by coincidence (a
pace_variability that happens to equal a norm, a grade that equals a drift), so a
blanket scalar-uniqueness guard is all false positives. The general
byte-identical-subobject guard (test_pack_no_duplicate_content) cannot see scalar
copies either. This registry is the durable backstop for the specific folds.

Path mini-language: a tuple of keys; the element ``"[]"`` iterates a list (so the
volume fold's per-metric copy is checked across every metric row). A precondition
confirms each copy is present on the un-dropped MODEL dump (the builder still computes
it), so the test exercises the drop rather than passing vacuously.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.user import User
from app.services.coach.context import build_context_pack

V11 = "coach_message_v11"

# (label, copy_path, home_path). copy_path -> must be ABSENT from the serialized pack;
# home_path -> must be PRESENT. "[]" in a path iterates a list.
SINGLE_HOME_FACTS = [
    ("rpe",
     ("perceived_effort", "rpe"), ("check_in", "rpe")),
    ("effort_score",
     ("perceived_effort", "effort_score"), ("metrics", "effort_score")),
    ("hr_drift via discount_signals",
     ("metrics", "discount_signals", "hr_drift_pct"), ("metrics", "hr_drift")),
    ("hr_drift via calibration",
     ("calibration", "hr_drift", "observed_drift_pct"), ("metrics", "hr_drift")),
    # The 7-day volume fold: rolling_7d's per-metric current values duplicate
    # recent_training.last_7d's roll-up (same trailing 7 days). Checked across every
    # metric row via the "[]" wildcard.
    ("7d volume current_all (rolling_7d)",
     ("training_volume", "rolling_7d", "metrics", "[]", "current_all"),
     ("recent_training", "last_7d", "total_distance_m")),
    ("7d volume current_runs (rolling_7d)",
     ("training_volume", "rolling_7d", "metrics", "[]", "current_runs"),
     ("recent_training", "last_7d", "activity_count")),
]


def _iter_targets(root, path):
    """Yield each (container, leaf_key) the path resolves to. '[]' iterates a list;
    a missing intermediate node simply yields nothing."""
    *head, leaf = path
    nodes = [root]
    for key in head:
        nxt = []
        for n in nodes:
            if key == "[]":
                if isinstance(n, list):
                    nxt.extend(n)
            elif isinstance(n, dict) and n.get(key) is not None:
                nxt.append(n[key])
        nodes = nxt
    for n in nodes:
        if isinstance(n, dict):
            yield n, leaf


def _build_fully_populated_v11_pack(db):
    """A real v11 pack where every folded fact's COPY is populated: 12 prior weeks of
    history (so training_volume has a baseline and recent_training fills), plus a
    subject run carrying a check-in (rpe), a stored discount_signals (hr_drift_pct) and
    a non-null hr_drift (calibration's observed_drift_pct) and effort_score."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"sh-{user_id}@example.com"))
    db.flush()

    def add(when, *, subject=False):
        act = Activity(
            id=uuid.uuid4(), user_id=user_id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            name="Run", type="Run",
            start_date=when, start_date_local=when.replace(tzinfo=None),
            distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
            avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=50.0,
            average_speed_mps=2.78, raw_summary={},
        )
        db.add(act)
        db.flush()
        db.add(DerivedMetric(
            id=uuid.uuid4(), activity_id=act.id,
            effort="easy", duration_class="standard", structure="continuous",
            is_hilly=False, is_race=False, effort_score=42.0,
            hr_drift=9.0 if subject else 5.0, pace_variability=5.0,
            time_in_zones={"Z1": 600, "Z2": 1200}, flags=[],
            confidence="high", confidence_reasons=[],
            discount_signals=(
                {"hr_drift_pct": 9.0, "likely_inflated_by": ["heat"],
                 "temperature_c": 29.0, "confidence": "high", "interpretation": "heat"}
                if subject else None
            ),
        ))
        if subject:
            db.add(CheckIn(id=uuid.uuid4(), activity_id=act.id, rpe=7, pain_score=1))
        db.flush()
        return act

    anchor = datetime(2026, 6, 24, 18, 0, tzinfo=timezone.utc)  # a Wednesday
    baseline_end = anchor - timedelta(days=7)
    for week in range(12):
        wk = baseline_end - timedelta(days=week * 7)
        for offset in range(4):
            add(wk - timedelta(days=offset))
    add(anchor - timedelta(days=2))
    subject = add(anchor, subject=True)
    db.refresh(subject)
    return build_context_pack(db, subject, prompt_id=V11)


def test_folded_facts_each_have_exactly_one_home(db):
    pack = _build_fully_populated_v11_pack(db)

    # Precondition: the un-dropped MODEL dump still carries every copy (the fold is
    # serialization-only; the derived reads compute from them). Guards a vacuous pass.
    model = pack.model_dump(mode="python")
    for label, copy_path, _home_path in SINGLE_HOME_FACTS:
        targets = list(_iter_targets(model, copy_path))
        assert targets and all(c.get(k) is not None for c, k in targets), (
            f"{label}: precondition — the copy {copy_path} should be populated on the "
            f"model so the drop is actually exercised."
        )

    out = pack.to_serializable_dict()
    for label, copy_path, home_path in SINGLE_HOME_FACTS:
        # Home present.
        home_targets = list(_iter_targets(out, home_path))
        assert home_targets and all(k in c for c, k in home_targets), (
            f"{label}: the single home {home_path} is missing from the serialized pack."
        )
        # Copy absent everywhere it could appear.
        for container, key in _iter_targets(out, copy_path):
            assert key not in container, (
                f"{label}: the folded-out copy {copy_path} reappeared in the serialized "
                f"pack — it must live only at {home_path}."
            )
