"""P1.2 cross-school invariance — the load-bearing AC3/AC4 gate (ADR 0014).

The deterministic gate (runs in CI, no API key): for one flagged activity, swapping
the keyed school changes ONLY the fact-free `corpus` pack section (AC4 — the
substrate is load-bearing, not dormant), while the run's facts, the surfaced safety
flags, and the deterministic policy outcome on a fixed report are byte-identical
across all three schools (AC3 — the school changes emphasis, never the floor or the
facts). The behavioural cross-school LLM run is the integration-tagged companion in
test_corpus_invariance_integration.py.
"""

from datetime import datetime
from uuid import uuid4

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.schemas.coach import CoachMessageReport
from app.services.coach import context as context_mod
from app.services.coach.context import build_context_pack
from app.services.coach.corpus import SCHOOLS
from app.services.coach.validator import validate_message_policy

V4 = "coach_message_v4"
_SCHOOLS = ("aerobic-base", "polarized", "enjoyment-and-consistency")


def _seed_flagged_activity(db) -> Activity:
    """An activity carrying a red-flag safety signal (so calibration.referral fires)."""
    user = User(email=f"corpus-inv-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Flagged run",
        distance_m=12000, moving_time_s=4000, elapsed_time_s=4000, elev_gain_m=30.0,
        avg_hr=165, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="moderate", structure="continuous",
        duration_class="long", effort_score=180.0,
        flags=["illness_or_extreme_fatigue", "high_drift"],
        hr_drift=9.5, confidence="high", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _packs_per_school(db, activity, monkeypatch):
    """Build the v4 pack once per school by swapping the wired default school id."""
    packs = {}
    for school in _SCHOOLS:
        monkeypatch.setattr(context_mod, "DEFAULT_SCHOOL_ID", school)
        packs[school] = build_context_pack(db, activity, prompt_id=V4)
    return packs


def test_school_changes_only_the_corpus_section(db, monkeypatch):
    """AC4 + AC3 (facts/flags): swapping the school changes the corpus section and
    NOTHING else — the run's facts and surfaced safety flags are byte-identical."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V4)
    activity = _seed_flagged_activity(db)
    packs = _packs_per_school(db, activity, monkeypatch)

    # Every pack carries the corpus section under v4, keyed to its school.
    for school in _SCHOOLS:
        assert packs[school].corpus is not None
        assert packs[school].corpus.school is not None
        assert packs[school].corpus.school.id == school
        assert packs[school].corpus.school.name == SCHOOLS[school].name

    # AC4: the corpus section genuinely DIFFERS across schools (load-bearing).
    dumps = {s: packs[s].corpus.model_dump() for s in _SCHOOLS}
    assert dumps["aerobic-base"] != dumps["polarized"]
    assert dumps["polarized"] != dumps["enjoyment-and-consistency"]
    assert dumps["aerobic-base"] != dumps["enjoyment-and-consistency"]

    # AC3 (facts/flags): the pack MINUS the corpus key is byte-identical across
    # schools — the safety flag and every fact stay put.
    def _without_corpus(pack):
        d = pack.to_serializable_dict()
        d.pop("corpus", None)
        return d

    base = _without_corpus(packs["aerobic-base"])
    assert "illness_or_extreme_fatigue" in packs["aerobic-base"].metrics.flags
    for school in _SCHOOLS:
        assert _without_corpus(packs[school]) == base, f"a fact moved under school {school}"
        assert packs[school].metrics.flags == packs["aerobic-base"].metrics.flags
        # the red-flag referral the safety floor depends on is school-invariant
        assert packs[school].calibration.referral == packs["aerobic-base"].calibration.referral


def test_validator_outcome_is_identical_across_schools(db, monkeypatch):
    """AC3 (floor): validate_message_policy takes (report, pack) and never reads the
    corpus, and the safety-relevant pack is school-invariant, so the deterministic
    policy outcome on a fixed report is identical whichever school steered framing."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V4)
    activity = _seed_flagged_activity(db)
    packs = _packs_per_school(db, activity, monkeypatch)

    def clean_message():
        return CoachMessageReport(
            message="Solid long run. Your pace held steady and the effort looked controlled.",
            headline="Steady long run", next_steps=[], risks=[], questions=[],
        )

    def overreach_message():
        return CoachMessageReport(
            message="This is overtraining syndrome — take 600mg of ibuprofen and rest.",
            headline="Overtrained", next_steps=[], risks=[], questions=[],
        )

    outcomes = []
    for school in _SCHOOLS:
        pack = packs[school]
        clean = [v.rule for v in validate_message_policy(clean_message(), pack)]
        bad = [v.rule for v in validate_message_policy(overreach_message(), pack)]
        outcomes.append((clean, bad))

    # Identical across every school...
    assert all(o == outcomes[0] for o in outcomes)
    # ...and the gate is real: the overreach message trips the medical rule.
    assert "medical_overreach" in outcomes[0][1]


def test_house_core_only_still_builds_when_no_school_resolves(db, monkeypatch):
    """AC5 at the pack level: with the wired school unresolvable the corpus degrades
    to house-core-only (school None) and the pack still builds with the floor intact."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V4)
    monkeypatch.setattr(context_mod, "DEFAULT_SCHOOL_ID", "no-such-school")
    activity = _seed_flagged_activity(db)

    pack = build_context_pack(db, activity, prompt_id=V4)
    assert pack.corpus is not None
    assert pack.corpus.school is None
    assert len(pack.corpus.house_principles) >= 3
    # the safety floor is untouched by the degradation
    assert "illness_or_extreme_fatigue" in pack.metrics.flags
    assert pack.calibration.referral is not None
