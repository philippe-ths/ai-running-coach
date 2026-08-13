"""#830: the drafted-plan contract — containment on the second generative surface.

The material distiller's spine (ADR 0017) applied to the coach's plan: no
free-form channel, one forced tool, and strict coercion before anything reaches a
column. This file pins what the schema REFUSES (a rogue key, rep structure on a
session that was never meant to have reps, an inverted window, an off-vocabulary
discipline, an implausible count), the exact `structure()` shape
`workout_matching.match_planned_to_detected` has been waiting for, and the design
pin that matters most: the tool offers the model NO field by which it could
supply a load number.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import re
from datetime import date

import pytest
from pydantic import ValidationError

from app.services.schedule.draft_contract import (
    MAX_SESSIONS_PER_WEEK,
    RECORD_TRAINING_PLAN_TOOL,
    DraftedPlan,
    DraftedSession,
    DraftedWeek,
    SketchedWeek,
)

MON = date(2026, 8, 10)
SAT = date(2026, 8, 15)
SUN = date(2026, 8, 16)


def _session(**overrides) -> dict:
    payload = {
        "window_start": MON,
        "window_end": MON,
        "intent": "easy",
        "discipline": "run",
        "title": "Easy 8k",
        "target_distance_m": 8000,
    }
    payload.update(overrides)
    return payload


# --- containment ------------------------------------------------------------


def test_a_key_the_contract_does_not_name_is_refused_rather_than_ignored():
    """`extra="forbid"`: an off-shape answer fails the draft instead of storing
    something off-contract."""
    with pytest.raises(ValidationError):
        DraftedSession(**_session(rpe_target=7))

    with pytest.raises(ValidationError):
        DraftedWeek(week_start=MON, sessions=[], notes="a field nobody declared")

    with pytest.raises(ValidationError):
        DraftedPlan(weeks=[], sketch_weeks=[], philosophy="improvised")


def test_the_model_cannot_smuggle_a_load_number_in_through_the_session_schema():
    """The one field the contract deliberately does not have. If it is ever added,
    it must be added on purpose — not arrive because a model asked for it."""
    for field in ("target_effort_score", "effort_score", "trimp", "load"):
        with pytest.raises(ValidationError):
            DraftedSession(**_session(**{field: 55}))


def test_rep_structure_on_a_session_that_was_never_meant_to_have_reps_is_refused():
    """Rep structure on an easy run would let the interval matcher judge a session
    the coach never prescribed reps for — a fabricated failure to hit a target
    that did not exist."""
    for intent in ("easy", "long", "rest", "strength"):
        with pytest.raises(ValidationError) as exc:
            DraftedSession(
                **_session(intent=intent, reps_planned=8, rep_distance_m=400)
            )
        assert "quality" in str(exc.value)

    ok = DraftedSession(
        **_session(intent="quality", reps_planned=8, rep_distance_m=400, rest_s=60)
    )
    assert ok.reps_planned == 8


def test_an_inverted_window_is_refused():
    """A window whose end precedes its start reads as pinned (width <= 0) while
    having no day left at all, so a session could be pinned and missed at once."""
    with pytest.raises(ValidationError) as exc:
        DraftedSession(**_session(window_start=SUN, window_end=SAT))
    assert "window_start is after window_end" in str(exc.value)

    # The degenerate equal case is the legitimate pin, not an inversion.
    assert DraftedSession(**_session(window_start=SAT, window_end=SAT)).intent == "easy"


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "recovery"},
        {"discipline": "swim"},
        {"commitment": "maybe"},
        {"title": ""},
        {"target_distance_m": -1},
        {"target_duration_s": 200_000},
        {"reps_planned": 0, "intent": "quality"},
        {"rep_distance_m": 0, "intent": "quality", "reps_planned": 4},
    ],
)
def test_an_off_vocabulary_or_out_of_bounds_session_never_coerces(payload):
    with pytest.raises(ValidationError):
        DraftedSession(**_session(**payload))


# --- the interval-matcher contract -----------------------------------------


def test_structure_is_exactly_the_shape_the_interval_matcher_expects():
    """`{"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60}` — the keys
    `match_planned_to_detected` has documented since the beginning and has never
    once been handed."""
    session = DraftedSession(
        **_session(intent="quality", reps_planned=8, rep_distance_m=400, rest_s=60)
    )

    assert session.structure() == {
        "reps_planned": 8,
        "rep_distance_m": 400,
        "rest_s": 60,
    }
    assert set(session.structure()) == {"reps_planned", "rep_distance_m", "rest_s"}


def test_the_warmup_and_cooldown_ride_into_the_structure_as_distances():
    """#876: they are part of the session, and they are METRES.

    Written as "10 min easy" in the detail they were unrecoverable — a distance
    only after multiplying by a pace nobody stated — so an interval session the
    runner had agreed as 4.5 km counted as its 2.4 km of reps.
    """
    session = DraftedSession(
        **_session(
            intent="quality",
            reps_planned=6,
            rep_distance_m=400,
            rest_s=90,
            warmup_distance_m=1100,
            cooldown_distance_m=1000,
        )
    )

    assert session.structure() == {
        "reps_planned": 6,
        "rep_distance_m": 400,
        "rest_s": 90,
        "warmup_distance_m": 1100,
        "cooldown_distance_m": 1000,
    }


def test_a_tempo_session_may_state_a_warmup_without_any_reps():
    """#878: the shape a live draft was refused on, and the plan died for it.

    A tempo run has a warm-up. So does almost every quality session. #876 tied
    the warm-up and cool-down to a rep COUNT along with the rep arguments proper,
    which made the commonest quality session in coaching unrepresentable — and
    since a draft gets two attempts, one such session ended the whole plan.

    The rep ARGUMENTS still need a count, because they describe reps. The edges
    describe the session, and a session can have edges without having reps.
    """
    session = DraftedSession(
        **_session(
            intent="quality",
            title="Tempo: 5 km @ 4:50/km",
            target_distance_m=7000,
            warmup_distance_m=1000,
            cooldown_distance_m=1000,
        )
    )

    assert session.structure() == {
        "warmup_distance_m": 1000,
        "cooldown_distance_m": 1000,
    }


def test_a_tempo_measured_in_minutes_may_still_state_its_warmup():
    """Nothing here demands a session be sized in the same unit as its edges.

    Coaches write tempos in minutes — "warm up 10 min easy, 4 x 5 min at
    comfortably hard" is an ordinary prescription — and a contract that refused
    the metres alongside those minutes would be #878 one shape further on, for
    the same reason and at the same cost: a draft gets two attempts, so one
    refused session loses the whole plan.

    Such a session simply contributes the distances that were stated. That is
    already how a duration-sized session behaves: it contributes nothing to the
    week's kilometres and nothing rejects it, so stating a warm-up can only
    improve on that.
    """
    session = DraftedSession(
        **_session(
            intent="quality",
            title="Tempo: 4 x 5 min",
            target_distance_m=None,
            target_duration_s=2400,
            warmup_distance_m=1000,
            cooldown_distance_m=1000,
        )
    )

    assert session.structure() == {
        "warmup_distance_m": 1000,
        "cooldown_distance_m": 1000,
    }


def test_the_warmup_and_cooldown_are_still_guarded_on_the_intent_and_the_value():
    """What #876's containment rule keeps: on an easy run the edges are noise,
    and a non-positive distance is not a distance."""
    with pytest.raises(ValidationError, match="quality session"):
        DraftedSession(**_session(warmup_distance_m=1000))

    with pytest.raises(ValidationError):
        DraftedSession(
            **_session(intent="quality", reps_planned=6, warmup_distance_m=0)
        )


def test_a_session_with_neither_reps_nor_edges_carries_no_structure_at_all():
    """None rather than an empty dict: a session with no structure at all must
    not reach the matcher as a plan it can score."""
    assert DraftedSession(**_session()).structure() is None
    assert DraftedSession(**_session(intent="quality")).structure() is None


def test_rep_arguments_are_guarded_symmetrically_not_just_the_count():
    """The containment rule binds every rep argument, not only `reps_planned`.

    It used to bind only the count, so `rest_s` rode through on an easy run, and
    a quality session that said "400s off 60" with no count lost that instruction
    silently — `structure()` keys off the count, so it was dropped with no failure
    and no log line. An instruction the coach wrote and nothing stored is worse
    than a rejected plan, so both are now rejected at the boundary.
    """
    with pytest.raises(ValidationError, match="quality session"):
        DraftedSession(**_session(rep_distance_m=400, rest_s=60))

    with pytest.raises(ValidationError, match="need reps_planned"):
        DraftedSession(**_session(intent="quality", rest_s=60))


def test_a_rep_count_with_no_distance_or_rest_still_yields_only_what_was_said():
    session = DraftedSession(**_session(intent="quality", reps_planned=6))

    assert session.structure() == {"reps_planned": 6}


# --- the sketched week ------------------------------------------------------


def test_a_sketched_week_refuses_a_discipline_outside_the_vocabulary():
    with pytest.raises(ValidationError) as exc:
        SketchedWeek(week_start=MON, sessions_by_discipline={"swim": 2})
    assert "unknown discipline" in str(exc.value)


def test_a_sketched_week_refuses_an_intent_outside_the_vocabulary():
    with pytest.raises(ValidationError) as exc:
        SketchedWeek(week_start=MON, intent_counts={"recovery": 2})
    assert "unknown intent" in str(exc.value)


@pytest.mark.parametrize(
    "counts", [{"run": MAX_SESSIONS_PER_WEEK + 1}, {"run": -1}, {"run": 40}]
)
def test_a_sketched_week_refuses_an_implausible_session_count(counts):
    with pytest.raises(ValidationError) as exc:
        SketchedWeek(week_start=MON, sessions_by_discipline=counts)
    assert "implausible session count" in str(exc.value)


def test_a_plausible_sketch_coerces():
    sketch = SketchedWeek(
        week_start=MON,
        phase="build",
        target_running_distance_m=45000,
        sessions_by_discipline={"run": 4, "strength": 2},
        intent_counts={"easy": 3, "long": 1, "quality": 1, "strength": 2},
    )

    assert sketch.sessions_by_discipline["run"] == 4
    assert sketch.phase == "build"


# --- the design pin: no load field is OFFERED ------------------------------


def _property_names(node) -> list:
    """Every property name anywhere in the tool's input schema."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                found.extend(value.keys())
            found.extend(_property_names(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_property_names(item))
    return found


def test_the_tool_offers_the_model_no_way_to_supply_a_load_score():
    """THE design pin of this slice.

    The North Star names `effort_score` as the hard case: a model reads a load
    number as an intensity verdict, and one it ESTIMATED for a session it is
    inventing would be a guess with no provenance, drawn as a bar the runner reads
    as fact. The division is structural — the coach says what the session IS and
    `effort.py` prices it — so the guarantee is that the tool never even ASKS.

    If a field matching this pattern is ever added, that is a design change and
    this test is where it must be argued, not a line to delete.
    """
    names = _property_names(RECORD_TRAINING_PLAN_TOOL["input_schema"])
    forbidden = re.compile(r"effort|load|trimp|score|intensity", re.IGNORECASE)

    assert names, "the tool schema declares no properties at all"
    assert [name for name in names if forbidden.search(name)] == []


def test_the_tool_is_the_only_channel_and_closes_every_object_it_declares():
    """Containment, not detection: no free-form answer, and no object in the
    schema accepts a key it did not declare."""
    assert RECORD_TRAINING_PLAN_TOOL["name"] == "record_training_plan"
    # The tool tells the model, in its own description, not to price a session.
    assert "Do not estimate training load" in RECORD_TRAINING_PLAN_TOOL["description"]

    def _objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                yield node
            for value in node.values():
                yield from _objects(value)
        elif isinstance(node, list):
            for item in node:
                yield from _objects(item)

    schema_objects = list(_objects(RECORD_TRAINING_PLAN_TOOL["input_schema"]))
    assert len(schema_objects) >= 4
    for obj in schema_objects:
        assert obj.get("additionalProperties") is False


def test_the_tools_declared_vocabularies_match_the_ones_that_are_coerced():
    """A schema that offered a value the coercion rejects would spend a whole
    attempt producing something guaranteed to fail."""
    session_schema = RECORD_TRAINING_PLAN_TOOL["input_schema"]["properties"]["weeks"][
        "items"
    ]["properties"]["sessions"]["items"]["properties"]

    assert set(session_schema["intent"]["enum"]) == {
        "rest",
        "easy",
        "long",
        "quality",
        "strength",
    }
    assert set(session_schema["discipline"]["enum"]) == {
        "run",
        "walk",
        "bike",
        "strength",
        "row",
        "other",
    }
    assert set(session_schema["commitment"]["enum"]) == {"committed", "suggested"}

    rule_kinds = RECORD_TRAINING_PLAN_TOOL["input_schema"]["properties"]["rules"][
        "items"
    ]["properties"]["kind"]["enum"]
    from app.services.schedule.rules import RULE_KINDS

    assert set(rule_kinds) == set(RULE_KINDS)


def test_a_zero_rep_distance_is_read_as_absent_not_rejected():
    """"Timed reps, no distance" spelled as zero must not cost a whole plan.

    A live draft returned `rep_distance_m: 0` on a 3 x 8 minute session — which
    is what "these reps are measured in time" looks like when the field is a
    number. `gt=0` rejected it and took the twelve-week plan with it. It is not
    a fact being repaired: it is "no distance given", written differently.
    """
    from app.services.schedule.draft_contract import normalise

    raw = {
        "rules": [],
        "sketch_weeks": [],
        "weeks": [
            {
                "week_start": MON.isoformat(),
                "sessions": [
                    _session(
                        window_start=MON.isoformat(),
                        window_end=MON.isoformat(),
                        intent="quality",
                        title="3 x 8 min",
                        target_duration_s=2400,
                        reps_planned=3,
                        rep_distance_m=0,
                        rest_s=120,
                    )
                ],
            }
        ],
    }

    plan = DraftedPlan.model_validate(normalise(raw))
    session = plan.weeks[0].sessions[0]

    assert session.rep_distance_m is None
    assert session.structure() == {"reps_planned": 3, "rest_s": 120}


def test_zero_rest_is_left_alone_because_it_is_a_real_instruction():
    """Unlike a zero distance, "no rest between reps" is something a coach means."""
    from app.services.schedule.draft_contract import normalise

    raw = {
        "weeks": [
            {
                "week_start": MON.isoformat(),
                "sessions": [
                    _session(
                        window_start=MON.isoformat(),
                        window_end=MON.isoformat(),
                        intent="quality",
                        title="continuous reps",
                        target_duration_s=1800,
                        reps_planned=4,
                        rest_s=0,
                    )
                ],
            }
        ],
        "rules": [],
        "sketch_weeks": [],
    }

    plan = DraftedPlan.model_validate(normalise(raw))

    assert plan.weeks[0].sessions[0].structure() == {"reps_planned": 4, "rest_s": 0}
