"""#655: what grouped_v10's fuller LLM view is served of `salience`, in the REAL pack.

Salience is computed deterministically on every run and, since ADR 0026 Slice 5, thrown
away before the fuller turn. Under the production receipt cadence there is no opener, so
that made it thrown away before the only LLM call that happens at all — the report writer
had no read of whether this session was a first of its kind or one more Tuesday, which is
exactly the read a coach varies depth on.

Pinned here, as an observable contract rather than a claim about flags:
  - v9's fuller view carries NO `salience` key at all (unchanged, byte-stable).
  - v10's fuller view carries `salience` holding `novelty` and NOT `safety_override` —
    the routing bit stays out, because `force_fuller: true` in front of a report model
    reads as "this one is serious" rather than as "a second turn is due".
  - the OPENER view is identical under both, whole section included: the opener prose
    reads `salience.novelty` and its scheduling reads `safety_override`.
  - the canonical stored pack is byte-identical between the two, so the deterministic
    safety force, the validator, the re-parse and the eval all read what they always did.

All row data is synthetic test setup (exercises code paths; represents no real runner).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import build_context_pack
from app.services.coach.service import _llm_pack_message

V9 = "coach_message_lean_grouped_v9"
V10 = "coach_message_lean_grouped_v10"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.add(UserProfile(user_id=uid, goal_type="general_fitness", experience_level="intermediate",
                       weekly_days_available=5, max_hr=190, max_hr_source="user", current_weekly_km=40))
    db.flush()
    return uid


def _activity(db, user_id, *, days_ago=0, effort="easy"):
    start = datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=start, start_date_local=start,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.4, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort=effort, duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=42.0,
        hr_drift=8.0, pace_variability=12.8, confidence="high", confidence_reasons=[],
        flags=[], time_in_zones={"Z1": 12, "Z2": 436, "Z3": 521}, discount_signals=None,
    ))
    db.flush()
    return a


def _seed(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    for i in range(6):
        _activity(db, uid, days_ago=2 + i, effort="easy")
    return subject


def test_v10_canonical_pack_equals_v9(db):
    """The lineage invariant: a view flag changes what is SENT, never what is STORED.

    If this ever diverged, the flip would stop being an experiment on one variable —
    the re-parse, the validator, the safety force and the eval all read this object."""
    subject = _seed(db)

    pack9 = build_context_pack(db, subject, prompt_id=V9)
    pack10 = build_context_pack(db, subject, prompt_id=V10)

    assert pack10.to_grouped_dict() == pack9.to_grouped_dict()
    assert pack10.to_serializable_dict() == pack9.to_serializable_dict()
    # ...and it is a pack that HAS salience to argue about, so the equality above is a
    # real claim rather than two empties matching.
    assert "salience" in pack9.to_grouped_dict()


def test_v9_fuller_view_still_has_no_salience_at_all(db):
    """The version being rolled back to is untouched. Stated as its own test because
    "v10 shows salience" is only interesting against a v9 that shows none."""
    subject = _seed(db)
    canonical = build_context_pack(db, subject, prompt_id=V9).to_grouped_dict()

    assert "salience" not in json.loads(_llm_pack_message(canonical, V9))


def test_v10_fuller_view_carries_novelty_and_not_the_routing_bit(db):
    """The whole point of the version: the depth signal is handed over, and the routing
    bit is not. `force_fuller` answers "does a second turn fire" — a question this turn
    is already the answer to, and a phrase a model reads as a verdict about the run."""
    subject = _seed(db)
    canonical = build_context_pack(db, subject, prompt_id=V10).to_grouped_dict()

    fuller = json.loads(_llm_pack_message(canonical, V10))

    assert "salience" in fuller
    assert set(fuller["salience"]) == {"novelty"}
    assert "safety_override" not in fuller["salience"]
    # The novelty read arrives intact, not as an empty shell.
    assert set(fuller["salience"]["novelty"]) == {"first_of_kind", "has_history"}

    # The canonical dict the caller stores was not mutated by the view.
    assert "safety_override" in canonical["salience"]


def test_the_opener_view_is_identical_under_both_versions(db):
    """Neither flag touches the opener. Its prose reads `salience.novelty` and its
    scheduling decision reads `safety_override`, so it needs the whole section — and a
    version that quietly narrowed it would break the two-stage cadence rather than the
    report."""
    subject = _seed(db)
    canonical = build_context_pack(db, subject, prompt_id=V10).to_grouped_dict()

    opener9 = json.loads(_llm_pack_message(canonical, V9, mode="opener"))
    opener10 = json.loads(_llm_pack_message(canonical, V10, mode="opener"))

    assert opener9 == opener10
    assert set(opener10["salience"]) == {"novelty", "safety_override"}


def test_the_deterministic_safety_force_still_reads_the_whole_section(db):
    """The trim is a VIEW. The force that can never be talked out of firing reads the
    typed pack object, which still carries `safety_override` under v10."""
    subject = _seed(db)

    pack = build_context_pack(db, subject, prompt_id=V10)

    assert pack.salience is not None
    assert pack.salience.safety_override.force_fuller is False
    assert pack.salience.safety_override.reasons == []
