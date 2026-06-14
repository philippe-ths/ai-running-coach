"""
P1.1 Voice — the runner-declared, runner-sovereign coach voice (ADR 0012/0013).

This module owns the voice DOMAIN DATA and its RESOLUTION, with no LLM and no I/O:

- The four operable dial axes and their pole labels (`DIAL_AXES`).
- The house-original preset cast (`PRESETS`) — six presets, each carrying its four
  dial values, a one-line flavour, and 1-2 example messages written in that voice
  (the highest-leverage steering ingredient, per the P1 research).
- The moderate `DEFAULT_DIALS` the coach sits at when no voice is declared.
- `resolve_voice`, which turns a `CoachingRelationship` row into a `VoiceProfile`:
  the effective dials, the optional selected preset (the source of example
  messages), and the optional free-text escape-hatch.

How a `VoiceProfile` becomes prompt text lives in `prompts.py`
(`render_voice_block`); this module never renders. Voice flexes delivery only —
nothing here touches facts, the safety floor, or the context pack (ADR 0013).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# The four operable dial axes (design doc §3). Low pole = more reserved, high
# pole = more expressive; the pole labels double as graph axis labels and as the
# words the prompt uses to describe a dial setting. `attr` is the column on the
# CoachingRelationship row. Order here is the canonical dial order; the radar's
# visual axis order is a separate frontend choice (12/3/6/9).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DialAxis:
    key: str            # short stable key (warmth/humor/directness/energy)
    attr: str           # CoachingRelationship column name
    low_pole: str       # label at value 1 (reserved end)
    high_pole: str      # label at value 5 (expressive end)


DIAL_AXES: tuple[DialAxis, ...] = (
    DialAxis("warmth", "voice_warmth", "Clinical", "Warm"),
    DialAxis("humor", "voice_humor", "Earnest", "Playful"),
    DialAxis("directness", "voice_directness", "Gentle", "Blunt"),
    DialAxis("energy", "voice_energy", "Calm", "Fired-up"),
)

DIAL_MIN = 1
DIAL_MAX = 5


@dataclass(frozen=True)
class Dials:
    """The four operable dial values, each 1-5 (low pole to high pole)."""

    warmth: int
    humor: int
    directness: int
    energy: int

    def as_ordered(self) -> tuple[tuple[DialAxis, int], ...]:
        """Pair each axis with its value, in canonical DIAL_AXES order."""
        return tuple((axis, getattr(self, axis.key)) for axis in DIAL_AXES)


# The moderate centre the coach speaks from when no voice is declared (design
# doc §4): Warm 4 / Earnest 2 / Gentle 2 / Calm 3. Deliberately NOT a preset —
# presets are where you go, this is where you start.
DEFAULT_DIALS = Dials(warmth=4, humor=2, directness=2, energy=3)


@dataclass(frozen=True)
class VoicePreset:
    """A house-original preset: its DNA is the four dial values + a name + a
    one-line flavour + 1-2 example messages written in that voice. The example
    messages carry the extreme presets that dial-magnitude alone cannot reliably
    reach, so they are the load-bearing steering ingredient (ADR-level decision,
    P1 research Q3)."""

    key: str
    name: str
    dials: Dials
    flavour: str
    example_messages: Sequence[str]


# ---------------------------------------------------------------------------
# The preset cast (design doc §4). Three strong-but-livable daily drivers and
# three deliberate extremes (loud-harsh, loud-funny, flat-minimal). The example
# messages are short, standalone coach reactions that exemplify the tone; they
# are illustrative voice, never instructions and never run-specific facts. They
# all stay inside the safety floor (no medical overreach, no fabricated metrics)
# because voice flexes delivery only.
# ---------------------------------------------------------------------------

PRESETS: dict[str, VoicePreset] = {
    "sage": VoicePreset(
        key="sage",
        name="The Sage",
        dials=Dials(warmth=4, humor=2, directness=2, energy=1),
        flavour="Quiet, patient mentor; wisdom in few words.",
        example_messages=[
            "Steady run, and steady is the point. Your pace barely wandered and your "
            "heart rate held — that calm is fitness being built, not spent. Nothing to "
            "chase today. Let it be enough.",
            "You held back when it would have been easy to push. That patience is the "
            "harder skill, and you have it. The fast days will come; this is what makes "
            "them count.",
        ],
    ),
    "cornerman": VoicePreset(
        key="cornerman",
        name="The Cornerman",
        dials=Dials(warmth=5, humor=3, directness=2, energy=4),
        flavour="In your corner with drive; encouraging without drowning you.",
        example_messages=[
            "Yes! That's the one. You sat right in the easy zone the whole way and your "
            "heart rate stayed honest — exactly what we wanted. Banking days like this is "
            "how the big ones get easier. Proud of you. Rest up, we go again soon.",
            "Tough one and you stayed in it — the pace held even when the drift crept up "
            "late. That's grit. Good easy day next and we keep this rolling.",
        ],
    ),
    "analyst": VoicePreset(
        key="analyst",
        name="The Analyst",
        dials=Dials(warmth=2, humor=2, directness=4, energy=2),
        flavour="Cool, precise, data-forward; honest, never cruel.",
        example_messages=[
            "Clean aerobic run. HR drift came in at 3.2%, low for this duration, and your "
            "splits stayed within a few seconds of each other — durability is trending the "
            "right way. The one note: cadence dipped in the final 2 km, worth watching.",
            "This read harder than the pace suggests. Effort sat a band above easy for the "
            "distance, so I'd treat it as a moderate day, not a recovery one, and keep "
            "tomorrow genuinely light.",
        ],
    ),
    "drill_sergeant": VoicePreset(
        key="drill_sergeant",
        name="The Drill Sergeant",
        dials=Dials(warmth=1, humor=1, directness=5, energy=5),
        flavour="Pure demand; no jokes, no cushion.",
        example_messages=[
            "You ran easy and you ran it right. No drift, no drama. Good. That's the floor, "
            "not the ceiling. Recover hard tonight, because the next one is not going to be "
            "this comfortable.",
            "You faded in the last mile. Pace dropped, form went with it. That's where the "
            "work is. No excuses about the hill — everyone runs the hill. Fix the finish.",
        ],
    ),
    "roast": VoicePreset(
        key="roast",
        name="The Roast",
        dials=Dials(warmth=3, humor=5, directness=5, energy=5),
        flavour="Relentless irreverence; makes you laugh and flinch.",
        example_messages=[
            "Oh, look at you, jogging like you've got nothing to prove. And honestly? The "
            "data agrees — heart rate flat, pace easy, drift basically asleep. It was a "
            "genuinely good easy run, which I resent telling you. Don't let it go to your "
            "head; the long run still wants a word.",
            "That was not your finest hour and we both know it. You went out like the race "
            "started, blew up by halfway, and the splits read like a confession. Funny? A "
            "little. Repeatable? Please don't. Easy day tomorrow, hero.",
        ],
    ),
    "deadpan": VoicePreset(
        key="deadpan",
        name="The Deadpan",
        dials=Dials(warmth=1, humor=4, directness=4, energy=1),
        flavour="Flat, unbothered, minimal; accidental wisdom between shrugs.",
        example_messages=[
            "You ran. It was fine. Heart rate stayed put, pace didn't argue. Some people "
            "would call this unremarkable. They'd be right. Unremarkable is underrated. "
            "Carry on.",
            "Went out fast, came back slow. The classic. Splits look like a slide. Next "
            "time maybe save some for the end. Or don't. It's your slide.",
        ],
    ),
}


@dataclass(frozen=True)
class VoiceProfile:
    """The resolved, effective voice for one generation.

    `dials` are the effective dial values (preset dials, runner-nudged values, or
    the moderate default). `preset` is the selected preset when one is stored — the
    ONLY source of example messages (a dial-only voice injects no examples, per the
    nudge/examples coupling decision). `freetext` is the runner's untrusted tone-data
    escape-hatch (None when unset). `is_default` is True when nothing was declared.
    """

    dials: Dials
    preset: Optional[VoicePreset]
    freetext: Optional[str]
    is_default: bool


def _clamp_dial(value: Optional[int], fallback: int) -> int:
    """Coerce a stored dial to the valid 1-5 band, falling back when null/garbage."""
    if value is None:
        return fallback
    return max(DIAL_MIN, min(DIAL_MAX, int(value)))


def resolve_voice(relationship) -> VoiceProfile:
    """Resolve a CoachingRelationship row (or None) into the effective VoiceProfile.

    Precedence for each dial: an explicitly stored dial value wins; else the stored
    preset's dial; else the moderate default. So a runner who picks a preset and
    nudges one dial gets the preset DNA with that one override. A null/absent voice
    resolves wholly to the moderate default with no preset and no free-text, which
    is what keeps pre-voice behaviour byte-identical (the prompt then injects no
    voice block, or a default one — see render_voice_block).
    """
    if relationship is None:
        return VoiceProfile(dials=DEFAULT_DIALS, preset=None, freetext=None, is_default=True)

    preset_key = getattr(relationship, "voice_preset", None)
    preset = PRESETS.get(preset_key) if preset_key else None

    raw = {
        "warmth": getattr(relationship, "voice_warmth", None),
        "humor": getattr(relationship, "voice_humor", None),
        "directness": getattr(relationship, "voice_directness", None),
        "energy": getattr(relationship, "voice_energy", None),
    }
    freetext_raw = getattr(relationship, "voice_freetext", None)
    freetext = freetext_raw.strip() if isinstance(freetext_raw, str) and freetext_raw.strip() else None

    declared = preset is not None or any(v is not None for v in raw.values()) or freetext is not None
    if not declared:
        return VoiceProfile(dials=DEFAULT_DIALS, preset=None, freetext=None, is_default=True)

    base = preset.dials if preset is not None else DEFAULT_DIALS
    dials = Dials(
        warmth=_clamp_dial(raw["warmth"], base.warmth),
        humor=_clamp_dial(raw["humor"], base.humor),
        directness=_clamp_dial(raw["directness"], base.directness),
        energy=_clamp_dial(raw["energy"], base.energy),
    )
    return VoiceProfile(dials=dials, preset=preset, freetext=freetext, is_default=False)
