"""RunnerMemoryProfile — the five-section runner memory profile (M1, ADR 0025).

The runner memory profile is the rewritten-from-source replacement for the retired
belief + narrative durable-memory loop. It holds only *stated* facts (what the
runner told the coach) plus soft, non-gating character — never an LLM behavioral
verdict — so the rest-day fixation incident's content cannot regenerate.

Storage format: a structured JSON document of five named sections, each a capped
list of short plain-language lines, strict-coerced (`extra="forbid"`, per-section
count cap + per-line length cap). Human-readable, diffable, machine-validatable,
and renderable to markdown for surfacing. Not opaque records, not free prose.

The five sections, by FUNCTION (how the coach must use a line), not by topic:
  1. who_you_are            — soft, non-gating character.
  2. limits_and_constraints — bounds or gates advice (an injury, "no morning runs").
  3. goals_and_plans        — forward-dated stated intentions (a race, a target).
  4. what_works_for_you     — stated preferences (gear, fuelling, tone, cues).
  5. lately                 — thread-state: the open thread / open question, the
                              one dense, recent-window section. Holds no outcome
                              verdict (that is re-derived deterministically).

Caps keep the whole profile to one screen regardless of how much history the
writer scanned.
"""

from __future__ import annotations

from typing import Annotated, List

from pydantic import BaseModel, ConfigDict, Field

# Caps: a generated profile is clamped so it fits one screen regardless of history
# size. Per-section line count and per-line character length.
MAX_LINES_PER_SECTION = 6
MAX_LINE_LENGTH = 200

# Canonical section order, used everywhere a profile is iterated (render, gather,
# the M3 pack projection).
MEMORY_SECTION_FIELDS = (
    "who_you_are",
    "limits_and_constraints",
    "goals_and_plans",
    "what_works_for_you",
    "lately",
)

# Human-readable headings for markdown rendering and surfacing.
MEMORY_SECTION_TITLES = {
    "who_you_are": "Who you are",
    "limits_and_constraints": "Limits & constraints",
    "goals_and_plans": "Goals & plans",
    "what_works_for_you": "What works for you",
    "lately": "Lately",
}

# Capped-list + strict-coercion idiom (mirrors `DistilledMaterial`): per-line
# length cap on the str, per-section count cap on the list.
_Line = Annotated[str, Field(max_length=MAX_LINE_LENGTH)]
_Section = Annotated[List[_Line], Field(max_length=MAX_LINES_PER_SECTION)]


class RunnerMemoryProfile(BaseModel):
    """The whole runner memory profile: five capped sections of short stated lines.

    Strict by construction — no extra keys, bounded fields — so an off-shape or
    over-long writer output fails coercion rather than storing anything off
    contract. An all-empty profile is valid (cold start).
    """

    model_config = ConfigDict(extra="forbid")

    who_you_are: _Section = []
    limits_and_constraints: _Section = []
    goals_and_plans: _Section = []
    what_works_for_you: _Section = []
    lately: _Section = []
