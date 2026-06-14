"""P1.1 Voice — read/write schemas for the runner's declared coach voice.

The runner edits a voice on their profile: a preset choice, four 1-5 dials, and an
optional free-text escape-hatch. The catalog (axes + presets + the moderate default)
is served alongside so the UI renders the preset cast and the radar from backend
truth rather than duplicating the DNA. Example messages are NOT exposed — they are a
prompt-internal steering ingredient, not user-facing copy.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.coach.voice import DIAL_AXES, DIAL_MAX, DIAL_MIN, PRESETS


class VoiceDials(BaseModel):
    """The runner's stored voice selection (all optional; null = moderate default)."""

    preset: Optional[str] = None
    warmth: Optional[int] = Field(default=None, ge=DIAL_MIN, le=DIAL_MAX)
    humor: Optional[int] = Field(default=None, ge=DIAL_MIN, le=DIAL_MAX)
    directness: Optional[int] = Field(default=None, ge=DIAL_MIN, le=DIAL_MAX)
    energy: Optional[int] = Field(default=None, ge=DIAL_MIN, le=DIAL_MAX)
    freetext: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, v):
        if v is not None and v not in PRESETS:
            raise ValueError(f"unknown voice preset: {v}")
        return v

    @field_validator("freetext")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None


class VoiceAxisInfo(BaseModel):
    key: str
    low_pole: str
    high_pole: str


class VoicePresetInfo(BaseModel):
    key: str
    name: str
    flavour: str
    warmth: int
    humor: int
    directness: int
    energy: int


class VoiceCatalog(BaseModel):
    """The static voice cast + axis labels, for rendering the picker and radar."""

    axes: List[VoiceAxisInfo]
    presets: List[VoicePresetInfo]
    default_warmth: int
    default_humor: int
    default_directness: int
    default_energy: int


class VoiceConfigRead(BaseModel):
    """The runner's current voice plus the catalog the UI needs to render it."""

    current: VoiceDials
    catalog: VoiceCatalog


def build_catalog() -> VoiceCatalog:
    from app.services.coach.voice import DEFAULT_DIALS

    return VoiceCatalog(
        axes=[
            VoiceAxisInfo(key=a.key, low_pole=a.low_pole, high_pole=a.high_pole)
            for a in DIAL_AXES
        ],
        presets=[
            VoicePresetInfo(
                key=p.key, name=p.name, flavour=p.flavour,
                warmth=p.dials.warmth, humor=p.dials.humor,
                directness=p.dials.directness, energy=p.dials.energy,
            )
            for p in PRESETS.values()
        ],
        default_warmth=DEFAULT_DIALS.warmth,
        default_humor=DEFAULT_DIALS.humor,
        default_directness=DEFAULT_DIALS.directness,
        default_energy=DEFAULT_DIALS.energy,
    )
