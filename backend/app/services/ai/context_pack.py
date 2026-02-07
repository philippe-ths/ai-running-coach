"""Unified AI ContextPack contract.

Single data structure that feeds both coach reports and chat prompts,
ensuring consistent signal availability and preventing hallucinations.
No business logic — only deterministic serialization.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── Sub-models ──────────────────────────────────────────────────────

class CPActivity(BaseModel):
    """Core activity data passed to the AI layer."""
    id: str
    start_time: str  # ISO-8601
    type: str
    name: Optional[str] = None
    distance_m: float
    moving_time_s: int
    elapsed_time_s: Optional[int] = None
    avg_pace_s_per_km: Optional[float] = None
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    avg_cadence: Optional[float] = None  # Normalized to Steps Per Minute (SPM) for runs
    elevation_gain_m: Optional[float] = None


class CPAthlete(BaseModel):
    """Semi-stable athlete profile context."""
    goal: Optional[str] = None
    experience_level: Optional[str] = None
    injury_notes: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None


class CPDerivedMetric(BaseModel):
    """One computed metric with provenance."""
    key: str
    value: float
    unit: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    evidence: str


class CPFlag(BaseModel):
    """One analysis flag with severity."""
    code: str
    severity: Literal["info", "warn", "risk"]
    message: str
    evidence: str


class CPLast7Days(BaseModel):
    """Rolling 7-day training summary."""
    total_distance_m: Optional[float] = None
    total_time_s: Optional[int] = None
    intensity_summary: str = ""
    load_trend: str = ""


class CPCheckIn(BaseModel):
    """User self-reported wellness signals."""
    rpe_0_10: Optional[int] = None
    pain_0_10: Optional[int] = None
    sleep_0_10: Optional[int] = None
    notes: Optional[str] = None


# ── Root model ──────────────────────────────────────────────────────

class ContextPack(BaseModel):
    """Unified context pack fed to every AI call (reports + chat).

    Deterministic serialization ensures reproducible prompts.
    """

    activity: CPActivity
    athlete: CPAthlete = Field(default_factory=CPAthlete)
    derived_metrics: List[CPDerivedMetric] = []
    flags: List[CPFlag] = []
    last_7_days: CPLast7Days = Field(default_factory=CPLast7Days)
    check_in: CPCheckIn = Field(default_factory=CPCheckIn)
    available_signals: List[str] = []
    missing_signals: List[str] = []
    generated_at_iso: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )

    # ── Serialization helpers ───────────────────────────────────────

    def to_prompt_json(self) -> dict:
        """Return a plain dict with keys in stable (sorted) order."""
        return json.loads(
            json.dumps(self.model_dump(), sort_keys=True, default=str)
        )

    def to_prompt_text(self) -> str:
        """Pretty-print JSON with sorted keys — ready to paste into a prompt."""
        return json.dumps(
            self.model_dump(), sort_keys=True, indent=2, default=str
        )
