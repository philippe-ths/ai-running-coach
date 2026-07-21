"""Typed intermediates for the analysis composition (#701).

The orchestrator composes ~a dozen pure stages into one `DerivedMetric`. This
module gives that composition two typed surfaces so the wiring — not just the
individual stages — is expressible and testable:

* :class:`IntervalSession` — the SINGLE owner of the "is this an interval
  session" gate. Previously that concept was smeared across the classifier's
  structure axis, an orchestrator null-out, and truthiness re-checks. It is now
  derived once, here, from the classification structure axis plus the probed
  interval structure; every downstream interval-only stage keys off this one
  value.

* :class:`DerivedMetricFields` — the typed stage-to-stage accumulator that
  replaces the string-keyed ``metrics_data`` bag. Its field names match the
  ``DerivedMetric`` columns exactly, so :meth:`DerivedMetricFields.to_columns`
  reproduces the exact dict the upsert consumed before (same values, same
  object identity — no copy), keeping the refactor behaviour-preserving.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Optional


@dataclass(frozen=True)
class IntervalSession:
    """The single owner of the "is this an interval session" gate.

    `structure` is the kept interval structure (the probe's output) when the
    classifier resolved the structure axis to ``"intervals"``, else ``None``.
    Downstream interval-only stages (workout matching, KPIs, the interval
    confidence sanity checks) gate on :attr:`structure` / :attr:`is_session`.
    """

    structure: Optional[dict]

    @property
    def is_session(self) -> bool:
        return self.structure is not None

    @classmethod
    def gate(
        cls, classification_structure: Optional[str], probed_structure: Optional[dict]
    ) -> "IntervalSession":
        """Apply the one gate: keep the probed structure only when the
        classifier's structure axis resolved to intervals.

        This is intentionally the ONLY place the gate is computed. It preserves
        the prior orchestrator behaviour byte-for-byte: ``interval_structure``
        (the ``lap_structure or stream_structure`` probe) was nulled unless
        ``classification.structure == "intervals"``.
        """
        if classification_structure != "intervals":
            return cls(structure=None)
        return cls(structure=probed_structure)


@dataclass
class DerivedMetricFields:
    """Typed accumulator for the fields that become a ``DerivedMetric`` row.

    Field names and defaults mirror the ``DerivedMetric`` columns the
    orchestrator writes, so :meth:`to_columns` yields exactly the dict the
    upsert used to build directly.
    """

    # Base metrics (from compute_derived_metrics_data).
    effort_score: float
    pace_variability: Optional[float] = None
    hr_drift: Optional[float] = None
    time_in_zones: Optional[dict] = None
    stops_analysis: Optional[dict] = None
    efficiency_analysis: Optional[dict] = None

    # Classification axes (ADR 0007).
    effort: Optional[str] = None
    duration_class: Optional[str] = None
    structure: Optional[str] = None
    is_hilly: Optional[bool] = None
    is_race: Optional[bool] = None

    # Interval session (gated by IntervalSession).
    interval_structure: Optional[dict] = None
    workout_match: Optional[dict] = None
    interval_kpis: Optional[dict] = None

    # Flags / risk / training context.
    flags: list = field(default_factory=list)
    training_context: Optional[dict] = None
    risk_level: Optional[str] = None
    risk_score: Optional[int] = None
    risk_reasons: Optional[list] = None

    # Confidence.
    confidence: Optional[str] = None
    confidence_reasons: list = field(default_factory=list)

    # Deterministic annotations.
    discount_signals: Optional[dict] = None
    stream_view: Optional[dict] = None

    @classmethod
    def from_base_metrics(cls, metrics: dict[str, Any]) -> "DerivedMetricFields":
        """Seed the accumulator from ``compute_derived_metrics_data``'s output."""
        return cls(**metrics)

    def to_columns(self) -> dict[str, Any]:
        """The exact column dict the upsert consumes.

        Shallow by design: values keep their identity (the JSON columns receive
        the very objects the stages produced), matching the pre-refactor
        ``DerivedMetric(**metrics_data)`` / ``setattr`` behaviour.
        """
        return {f.name: getattr(self, f.name) for f in fields(self)}
