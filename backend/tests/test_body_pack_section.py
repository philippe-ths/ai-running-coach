"""#742 — the runner's stated build reaches the coach as `profile.body`.

The coach is meant to coach this runner rather than the median (North Star), but had
no structured read of the body it was coaching: the only channel that reached it was
free-text `injury_notes`, and an A/B probe under grouped_v7 showed a "~109 kg" note
there produced a substantively identical report.

Pinned here: the section carries the stated figures under grouped_v8 only; the pack
MINUS body is byte-identical to grouped_v7 (the signal moved no other fact); it drops
byte-stably under every prior prompt, with no profile row, and when the runner has
stated neither figure; and the structural no-duplicate guarantee holds for it.

That last one is this file's second job. `fullest_message_prompt_id()` ranks by
ADDITIVE feature count and so resolves to a grouped_v1-era prompt that does not carry
BODY, which means the pack-wide structural guards (test_pack_no_duplicate_content,
test_pack_single_home_facts) never see this section. test_prompt_features names this
file as the compensating guard; keep that promise here.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.schemas.coach_context import BodyContext, ProfileContext
from app.services.coach.context import build_context_pack

V7 = "coach_message_lean_grouped_v7"
V8 = "coach_message_lean_grouped_v8"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"body_{uid}@example.com"))
    db.flush()
    return uid


def _profile(db, user_id, **fields):
    db.add(UserProfile(
        user_id=user_id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, upcoming_races=[], **fields,
    ))
    db.flush()


def _activity(db, user_id):
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort="easy", duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=3.0,
        confidence="high", confidence_reasons=[], flags=[],
    ))
    db.flush()
    return a


def _pack(db, activity, prompt_id):
    return build_context_pack(db, activity, prompt_id=prompt_id).to_serializable_dict()


# --- the signal itself --------------------------------------------------------


def test_body_carries_the_stated_figures_under_v8(db):
    uid = _user(db)
    _profile(db, uid, weight_kg=109.0, height_cm=193.0)
    activity = _activity(db, uid)

    body = _pack(db, activity, V8)["profile"]["body"]

    assert body == {"weight_kg": 109.0, "height_cm": 193.0}


def test_one_stated_figure_is_enough_to_emit(db):
    # A runner who has given only their weight has still told the coach the thing that
    # most changes how they should be trained; abstaining on a partial answer would
    # throw that away.
    uid = _user(db)
    _profile(db, uid, weight_kg=109.0)
    activity = _activity(db, uid)

    body = _pack(db, activity, V8)["profile"]["body"]

    assert body == {"weight_kg": 109.0, "height_cm": None}


def test_no_derived_index_is_ever_emitted(db):
    # The whole point of #742 is to refuse the population formula. A BMI (or any ratio)
    # appearing here would trade our median for the textbook's, so the section carries
    # the stated facts and nothing computed from them.
    uid = _user(db)
    _profile(db, uid, weight_kg=109.0, height_cm=193.0)
    activity = _activity(db, uid)

    assert set(_pack(db, activity, V8)["profile"]["body"]) == {"weight_kg", "height_cm"}


# --- byte-stable absence ------------------------------------------------------


def test_body_is_absent_not_null_under_the_prior_prompt(db):
    uid = _user(db)
    _profile(db, uid, weight_kg=109.0, height_cm=193.0)
    activity = _activity(db, uid)

    profile = _pack(db, activity, V7)["profile"]

    assert "body" not in profile  # dropped entirely, never a null key


def test_v8_pack_minus_body_is_byte_identical_to_v7(db):
    # The signal must ADD one nested field and move nothing else. Anything different
    # would mean grouped_v8 is not a clean A/B against grouped_v7.
    uid = _user(db)
    _profile(db, uid, weight_kg=109.0, height_cm=193.0)
    activity = _activity(db, uid)

    v7, v8 = _pack(db, activity, V7), _pack(db, activity, V8)
    v8["profile"].pop("body")

    assert v8 == v7


def test_body_drops_when_the_runner_has_stated_neither_figure(db):
    # Null is NOT average: absent means absent, so the model is never invited to fill
    # the gap with a typical runner.
    uid = _user(db)
    _profile(db, uid)
    activity = _activity(db, uid)

    assert "body" not in _pack(db, activity, V8)["profile"]


def test_body_drops_with_no_profile_row_at_all(db):
    uid = _user(db)
    activity = _activity(db, uid)

    assert "body" not in _pack(db, activity, V8)["profile"]


def test_an_unstated_build_makes_v8_byte_identical_to_v7(db):
    # The cold-start case: a runner who has told us nothing gets the grouped_v7 pack
    # exactly, so the flip is inert for everyone until they fill the field in.
    uid = _user(db)
    _profile(db, uid)
    activity = _activity(db, uid)

    assert _pack(db, activity, V8) == _pack(db, activity, V7)


# --- the structural guard `fullest` cannot give us ----------------------------


def test_the_stated_figures_appear_exactly_once_in_the_whole_pack(db):
    """The no-duplicate-content guarantee, hand-rolled for this section.

    The pack-wide structural guards build their pack under `fullest_message_prompt_id()`,
    which does not carry BODY (see test_prompt_features' GROUPED_ONLY_ADDITIVE), so this
    section would otherwise be unguarded. One-fact-one-place is the pack's standing rule.
    """
    uid = _user(db)
    # Values chosen not to collide with any other pack number (distance 10000, HR 150/175,
    # cadence 170, effort 3.0), so a hit anywhere else is a real second home.
    _profile(db, uid, weight_kg=109.4, height_cm=193.7)
    activity = _activity(db, uid)

    def count(node, needle):
        if isinstance(node, dict):
            return sum(count(v, needle) for v in node.values())
        if isinstance(node, list):
            return sum(count(v, needle) for v in node)
        return 1 if node == needle else 0

    pack = _pack(db, activity, V8)
    assert count(pack, 109.4) == 1
    assert count(pack, 193.7) == 1


def test_stored_pre_742_packs_still_parse(db):
    # ProfileContext forbids extra keys, so a pack stored before #742 (no `body`) must
    # still round-trip: the chat read path and the eval harness strict-parse history.
    legacy = ProfileContext(
        goal_type="general", experience_level="intermediate",
        weekly_days_available=4, injury_notes=None, max_hr=None,
        max_hr_source=None, current_weekly_km=None,
    )

    assert legacy.body is None
    assert ProfileContext(**legacy.model_dump()).body is None


def test_body_context_rejects_an_unexpected_key():
    # extra="forbid" is what stops a future derived index being smuggled in.
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BodyContext(weight_kg=80.0, bmi=24.1)
