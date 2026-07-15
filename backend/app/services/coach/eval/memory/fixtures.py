"""Synthetic good / deliberately-bad memory-write fixtures (#658).

SYNTHETIC, NOT production data (ground-truth trust level 5): hand-authored to
exercise the rubric, co-designed so the good fixture passes every applicable
assertion and the bad fixture fails every one. The numbered comments on the bad
fixture map each violation to the assertion it trips, the same inverted-oracle
idiom the coach-report eval's `deliberately_bad_report` uses.

Each fixture returns the scored triple `(MemorySources, [MemoryCandidate],
RunnerMemoryProfile)`: the writer's input, its raw candidate proposal, and the
stored profile graduation produced.
"""

from typing import List, Tuple

from app.schemas.coach_memory import RunnerMemoryProfile
from app.services.coach.memory_update import MemoryCandidate, MemorySources, SourceItem

_Triple = Tuple[MemorySources, List[MemoryCandidate], RunnerMemoryProfile]


def known_good_memory() -> _Triple:
    """A clean write: stated facts and soft character only, every durable line on
    the runner's own words, the plan on a committed goal, the safety niggle held."""
    sources = MemorySources(
        sources=(
            SourceItem(id="rc1", kind="check_in_note", text="Knee felt tight after Sunday's long run", durable=True),
            SourceItem(id="rr1", kind="chat", text="yeah let's lock in the October half", durable=True, role="runner"),
            SourceItem(id="rc2", kind="check_in_note", text="signed up for the October half marathon", durable=True),
            SourceItem(id="rr2", kind="chat", text="i always wear my vaporflys for workouts", durable=True, role="runner"),
            SourceItem(id="rc3", kind="check_in_note", text="did the workout in vaporflys again, felt great", durable=True),
            SourceItem(id="rr3", kind="chat", text="i mostly train on my own, prefer it that way", durable=True, role="runner"),
            SourceItem(id="rc4", kind="check_in_note", text="solo long run again this week", durable=True),
            SourceItem(id="cc1", kind="coach_chat", text="want to lock in the October half?", durable=False, role="coach"),
        )
    )
    candidates = [
        MemoryCandidate(text="Trains mostly solo, prefers it that way", section="who_you_are", supporting_source_ids=["rr3", "rc4"]),
        MemoryCandidate(text="Possible right-knee niggle, mentioned once", section="limits_and_constraints", supporting_source_ids=["rc1"], safety_relevant=True),
        MemoryCandidate(text="Racing a half marathon in October", section="goals_and_plans", supporting_source_ids=["rr1", "rc2"]),
        MemoryCandidate(text="Vaporflys for workouts", section="what_works_for_you", supporting_source_ids=["rr2", "rc3"]),
        MemoryCandidate(text="Agreed: locking in the October half", section="lately", supporting_source_ids=["rr1", "cc1"]),
    ]
    profile = RunnerMemoryProfile(
        who_you_are=["Trains mostly solo, prefers it that way"],
        limits_and_constraints=["Possible right-knee niggle, mentioned once"],
        goals_and_plans=["Racing a half marathon in October"],
        what_works_for_you=["Vaporflys for workouts"],
        lately=["Agreed: locking in the October half"],
    )
    return sources, candidates, profile


def deliberately_bad_memory() -> _Triple:
    """A write that trips every assertion, one violation each."""
    sources = MemorySources(
        sources=(
            SourceItem(id="rr1", kind="chat", text="i mostly run solo", durable=True, role="runner"),
            SourceItem(id="rc1", kind="check_in_note", text="left shin has been sore this week", durable=True),
            SourceItem(id="cc1", kind="coach_chat", text="you'd respond well to 100-mile weeks", durable=False, role="coach"),
            SourceItem(id="cc2", kind="coach_chat", text="how about a 50k ultra next month?", durable=False, role="coach"),
        )
    )
    candidates = [
        # (1) no_inferred_verdict: an inferred training-compliance verdict. Grounded
        #     on a durable source so it trips ONLY the verdict sensor, not grounding.
        MemoryCandidate(text="Ignores easy-day guidance and runs them too hard", section="who_you_are", supporting_source_ids=["rr1"]),
        # (2) durable_lines_grounded: a durable preference grounded ONLY on a coach
        #     turn (anti-echo violation) — the coach's opinion as the runner's fact.
        MemoryCandidate(text="Responds well to 100-mile weeks", section="what_works_for_you", supporting_source_ids=["cc1"]),
        # (3) plan_from_commitment: a plan the COACH proposed, no runner commitment.
        MemoryCandidate(text="Doing a 50k ultra next month", section="goals_and_plans", supporting_source_ids=["cc2"]),
        # (4) safety_limit_held: a grounded safety limit that the profile DROPPED.
        MemoryCandidate(text="Sore left shin, mentioned once", section="limits_and_constraints", supporting_source_ids=["rc1"], safety_relevant=True),
    ]
    profile = RunnerMemoryProfile(
        who_you_are=["Ignores easy-day guidance and runs them too hard"],  # (1)
        limits_and_constraints=[],  # (4) the grounded shin limit was dropped
        goals_and_plans=["Doing a 50k ultra next month"],  # (3)
        what_works_for_you=["Responds well to 100-mile weeks"],  # (2)
        lately=[],
    )
    return sources, candidates, profile
