"""P4 (#285) User materials — request/response schemas + the strict distilled shape.

`DistilledMaterial` is the containment-critical contract (ADR 0017): the corpus
`School` shape, minus the house identity fields. Every record the distiller stores
is coerced through this model, so `extra="forbid"` plus bounded lengths/counts mean
an injection payload can at most fill these four fields with bounded text — it can
never add a rogue key or unbounded blob. The strict-tool JSON schema cannot express
maxLength (strict subset), so the size bound lives HERE, the validation layer.

`MaterialKind` is the runner-set light label (ADR 0017 fork 4): organisation +
distiller framing only, no downstream behavioural branching.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MaterialKind(str, Enum):
    """The runner-set kind label. A light steer for the distiller and the runner's
    own organisation; deliberately NOT coupled to the safety floor or workout
    matching (ADR 0017 fork 4)."""

    philosophy = "philosophy"
    plan = "plan"
    protocol = "protocol"
    race_plan = "race_plan"
    other = "other"


# Defence-in-depth size bounds on the distilled record. Generous enough that a
# faithful, compact distillation of a real plan/philosophy fits, tight enough that
# a payload cannot bloat the eventual pack. Mirrors the rough size of a house
# `School` entry in corpus.py.
_StanceStr = Annotated[str, Field(max_length=1000)]
_FramingStr = Annotated[str, Field(max_length=2000)]
_PrincipleStr = Annotated[str, Field(max_length=500)]
_HintStr = Annotated[str, Field(max_length=300)]


class DistilledMaterial(BaseModel):
    """The compact, corpus-`School`-shaped record the distiller produces and the
    pack (slice 2) reasons over. Strict by construction: no extra keys, bounded
    fields. This is the only representation of a material that ever reaches an
    exchange; the raw text never does."""

    model_config = ConfigDict(extra="forbid")

    stance: _StanceStr
    principles: Annotated[List[_PrincipleStr], Field(max_length=15)] = []
    method_framing: _FramingStr
    emphasis_hints: Annotated[List[_HintStr], Field(max_length=15)] = []


class UserMaterialRead(BaseModel):
    """API view of one material. Deliberately omits `raw_text` — the untrusted
    source is pull-on-demand only and is never echoed in the list/detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    title: str
    filename: str
    status: str
    distilled: Optional[DistilledMaterial] = None
    distill_model: Optional[str] = None
    created_at: Optional[datetime] = None
    distilled_at: Optional[datetime] = None
