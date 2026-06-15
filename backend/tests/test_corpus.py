"""P1.2 corpus library (ADR 0014) — the code-resident house corpus.

corpus.py mirrors voice.py: frozen dataclasses, no LLM, no I/O, no table. These
tests pin the DATA CONTRACT (the house core is always present, the three
house-original schools resolve, every entry carries its short structured fields)
— not the authored prose, which is the owner's coaching-tone judgement.
"""

from __future__ import annotations

import dataclasses

from app.services.coach import corpus


def test_house_core_is_present_with_principles():
    """The always-present house core carries a non-empty set of short principles."""
    assert isinstance(corpus.HOUSE_CORE, corpus.HouseCore)
    assert len(corpus.HOUSE_CORE.principles) >= 3
    assert all(isinstance(p, str) and p.strip() for p in corpus.HOUSE_CORE.principles)


def test_five_house_original_schools_resolve_by_id():
    """The library carries exactly the five schools (the three P1.2 schools plus the
    two P1.3 schools surfaced by stance selection), each keyed by its id."""
    assert set(corpus.SCHOOLS) == {
        "aerobic-base",
        "polarized",
        "enjoyment-and-consistency",
        "strength-led",
        "periodization",
    }
    for school_id, school in corpus.SCHOOLS.items():
        assert school.id == school_id  # the key and the entry's own id agree


def test_aerobic_base_is_the_wired_default():
    """aerobic-base is the hardcoded default school for P1.2 (P1.3 makes it a choice)."""
    assert corpus.DEFAULT_SCHOOL_ID == "aerobic-base"
    assert corpus.DEFAULT_SCHOOL_ID in corpus.SCHOOLS


def test_every_school_carries_its_short_structured_fields():
    """Each school entry = id, name, one-line stance, principles[], method_framing,
    emphasis_hints[] — short structured fields, not long prose (ADR 0014 / Q4)."""
    for school in corpus.SCHOOLS.values():
        assert school.name.strip()
        assert school.stance.strip()
        assert len(school.principles) >= 2
        assert all(isinstance(p, str) and p.strip() for p in school.principles)
        assert school.method_framing.strip()
        assert len(school.emphasis_hints) >= 1
        assert all(isinstance(h, str) and h.strip() for h in school.emphasis_hints)


def test_corpus_dataclasses_are_frozen():
    """Frozen like voice.py's domain data — read-only at runtime, diff-reviewable."""
    assert dataclasses.is_dataclass(corpus.School)
    assert dataclasses.fields  # sanity
    a_school = corpus.SCHOOLS["aerobic-base"]
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        a_school.name = "mutated"  # type: ignore[misc]
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        corpus.HOUSE_CORE.principles = ()  # type: ignore[misc]
