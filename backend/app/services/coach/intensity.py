"""#578: the deterministic intensity-distribution-and-trend signal.

The retired belief loop replayed a stored LLM verdict ("this runner drifts toward
easy / ignores quality"), which hardened and nagged. ADR 0025 deliberately keeps NO
behavioural verdict in memory: the coach must read training DIRECTION live from the
data each run. The v13 memory addendum already instructs that ("read the easy/
moderate/hard share this run against recent comparable runs, confounder-adjusted,
never a binary acted/ignored verdict"). This module is the DATA-LAYER half of that
discipline: it does the easy/moderate/hard intensity-share computation deterministically
so the read is auditable and consistent rather than re-inferred in prose each time.

It reports two things, re-derived every read (never stored), confounder-adjusted:

  - THIS SESSION's intensity: its dominant band (collapsed from the HR-derived `effort`
    axis) plus, when available, the easy/moderate/hard split of time WITHIN the run
    (from `time_in_zones`).
  - THE RECENT DISTRIBUTION + TREND: the easy/moderate/hard share of the runner's recent
    comparable sessions (session-count share over a 28-day window), and whether that
    distribution is shifting harder or easier (the recent window's hard-share vs the
    prior equal window's). Both are computed on a confounder-EXCULPATED view: a session
    whose HR drift fired a discount signal (heat, hills, stimulant) does not count its
    apparent hardness as genuine intensity, so a hot week is not misread as drift.

Every figure is a deterministic FACT the coach may cite; none is an intensity verdict,
and none overrides this run's re-derived DerivedMetric or the safety floor. The section
abstains (directions `no_norm`, distributions null) when history is too thin, mirroring
volume / recent_training.

# Band definition (the three-zone collapse)

The canonical intensity-distribution model (Seiler's three-zone / polarized framing)
collapses the five HR zones at the two physiological breakpoints — below the first
lactate threshold (easy), the threshold "grey zone" (moderate), and above the second
threshold (hard). That maps exactly onto the codebase's existing 5-band `effort` axis
(`classifier._ZONE_TO_EFFORT`) and HR zones, so we reuse those primitives rather than
inventing a new intensity definition:

  - easy     <- effort in {recovery, easy}   /  HR zones Z1, Z2
  - moderate <- effort == moderate           /  HR zone  Z3
  - hard     <- effort in {tempo, hard}      /  HR zones Z4, Z5

Pure functions over already-fetched `ActivityFact`s plus a set of confounded activity
ids (no DB, no LLM); the DB reads and pack wiring live in `context.py`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, List, Optional, Set, Tuple

from app.schemas.coach_context import (
    IntensityBandShare,
    IntensityContext,
    IntensitySession,
)

# The five-band effort axis collapsed to the three intensity bands (see module docstring).
_EFFORT_TO_BAND = {
    "recovery": "easy",
    "easy": "easy",
    "moderate": "moderate",
    "tempo": "hard",
    "hard": "hard",
}

# The five HR zones collapsed to the three intensity bands.
_ZONES_FOR_BAND = {
    "easy": ("Z1", "Z2"),
    "moderate": ("Z3",),
    "hard": ("Z4", "Z5"),
}

_BANDS = ("easy", "moderate", "hard")
# Ordinal scale for the this-run-vs-recent comparison (easy < moderate < hard).
_BAND_ORDINAL = {"easy": 0.0, "moderate": 1.0, "hard": 2.0}

# The recent window for the distribution, and the equal prior window for the trend.
# 28 days = four clean weeks (the natural unit for an intensity-distribution read).
_WINDOW_DAYS = 28
# Below this many classifiable comparable sessions in a window, the distribution is too
# thin to call — directions abstain as `no_norm` (mirrors the M9/volume abstain tiers).
_MIN_SESSIONS = 4
# This run's band must sit this far (on the 0-2 ordinal) from the recent window's mean
# band before it reads as harder/easier; inside the band it is `in_line`.
_VS_RECENT_DEADBAND = 0.5
# The recent-vs-prior hard-share must move more than this many percentage points before
# the distribution reads as shifting harder/easier; inside it is `in_line`.
_TREND_DEADBAND_PCT = 10.0


def _collapse_effort(effort: Optional[str]) -> Optional[str]:
    """The session's dominant intensity band from the HR-derived effort axis, or None
    when the run has no HR-derived effort (so it cannot be placed in the distribution)."""
    if effort is None:
        return None
    return _EFFORT_TO_BAND.get(effort)


def _within_run_share(time_in_zones: Optional[dict]) -> Optional[IntensityBandShare]:
    """The easy/moderate/hard split of time WITHIN one run, from its zone histogram, or
    None when no usable zone time is present."""
    if not time_in_zones:
        return None
    total = sum(v for v in time_in_zones.values() if isinstance(v, (int, float)))
    if total <= 0:
        return None
    band_secs = {
        band: sum(time_in_zones.get(z, 0) or 0 for z in zones)
        for band, zones in _ZONES_FOR_BAND.items()
    }
    return IntensityBandShare(
        easy_pct=round(band_secs["easy"] / total * 100.0, 1),
        moderate_pct=round(band_secs["moderate"] / total * 100.0, 1),
        hard_pct=round(band_secs["hard"] / total * 100.0, 1),
    )


def _is_race(fact: Any) -> bool:
    """Best-effort race exclusion from the runner's declared intent. The activity NAME
    is not projected into ActivityFact (#367), so name-based race detection is not
    available here; a race captured only by name still counts. This is a known minor
    limitation — a single deliberately-hard race only nudges the share, and the coach
    addendum treats races as exculpatory anyway."""
    return "race" in (getattr(fact, "user_intent", "") or "").lower()


def _comparable(fact: Any, this_activity_id: Any) -> bool:
    """A session counts toward the distribution when it is not this run, is not a race,
    and carries an HR-derived intensity band to classify."""
    if getattr(fact, "activity_id", None) == this_activity_id:
        return False
    if _is_race(fact):
        return False
    return _collapse_effort(getattr(fact, "effort", None)) is not None


def _effective_band(fact: Any, confounded_ids: Set[Any]) -> str:
    """A session's band for the distribution, confounder-EXCULPATED: a session whose HR
    drift fired a discount signal (heat / hills / stimulant) does not count its apparent
    moderate/hard intensity as genuine — its hardness is explained away, so it reads as
    easy. The raw (un-exculpated) band is used for the un-adjusted distribution."""
    band = _collapse_effort(fact.effort)
    assert band is not None  # guarded by _comparable
    if getattr(fact, "activity_id", None) in confounded_ids and band != "easy":
        return "easy"
    return band


def _share(counts: dict, total: int) -> Optional[IntensityBandShare]:
    if total <= 0:
        return None
    return IntensityBandShare(
        easy_pct=round(counts["easy"] / total * 100.0, 1),
        moderate_pct=round(counts["moderate"] / total * 100.0, 1),
        hard_pct=round(counts["hard"] / total * 100.0, 1),
    )


def _window_counts(
    facts: List[Any], confounded_ids: Set[Any], *, adjusted: bool
) -> Tuple[dict, dict, int]:
    """(raw_counts, adjusted_counts, n) of comparable sessions in `facts`. The raw view
    uses each session's measured band; the adjusted view exculpates confounded sessions."""
    raw = {b: 0 for b in _BANDS}
    adj = {b: 0 for b in _BANDS}
    for f in facts:
        raw[_collapse_effort(f.effort)] += 1
        adj[_effective_band(f, confounded_ids)] += 1
    return raw, adj, len(facts)


def _hard_share_pct(counts: dict, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return counts["hard"] / total * 100.0


def _direction(delta: Optional[float], deadband: float, harder: str, easier: str) -> str:
    if delta is None:
        return "no_norm"
    if delta > deadband:
        return harder
    if delta < -deadband:
        return easier
    return "in_line"


def build_intensity(
    facts: List[Any],
    confounded_ids: Set[Any],
    this_activity_id: Any,
    as_of: date,
) -> Optional[IntensityContext]:
    """Build the intensity-distribution-and-trend signal as of `as_of`.

    `facts` are duck-typed `ActivityFact`s spanning at least the trailing ~56 days
    (the 28-day recent window plus its equal prior window); each needs `activity_id`,
    `local_date`, `effort`, `time_in_zones`, and (for race exclusion) `user_intent`.
    `confounded_ids` is the set of activity ids whose stored `discount_signals` fired a
    real HR-inflation confounder (heat / terrain / stimulant).

    Returns None — the section is then dropped from serialization (byte-stable) — only
    when there is nothing at all to say: this run has no HR band AND no comparable recent
    session exists. Otherwise the section is emitted and degrades internally (null
    distributions, `no_norm` directions) when history is thin, mirroring volume /
    recent_training.
    """
    this_fact = next(
        (f for f in facts if getattr(f, "activity_id", None) == this_activity_id), None
    )
    this_band = _collapse_effort(getattr(this_fact, "effort", None)) if this_fact else None
    this_session = IntensitySession(
        band=this_band,
        within_run=_within_run_share(getattr(this_fact, "time_in_zones", None))
        if this_fact
        else None,
        hr_confounded=this_activity_id in confounded_ids,
    )

    recent_start = as_of - timedelta(days=_WINDOW_DAYS - 1)
    prior_end = recent_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=_WINDOW_DAYS - 1)

    recent = [
        f
        for f in facts
        if recent_start <= f.local_date <= as_of and _comparable(f, this_activity_id)
    ]
    prior = [
        f
        for f in facts
        if prior_start <= f.local_date <= prior_end and _comparable(f, this_activity_id)
    ]

    # Nothing to say at all: no band for this run and no comparable recent session.
    if this_band is None and not recent:
        return None

    raw, adj, n = _window_counts(recent, confounded_ids, adjusted=True)
    has_distribution = n >= _MIN_SESSIONS

    distribution = _share(raw, n) if has_distribution else None
    distribution_adjusted = _share(adj, n) if has_distribution else None

    # This run vs the recent distribution: this run's band against the window's mean band
    # (on the confounder-exculpated view), with a deadband so a near-typical run is in_line.
    this_run_vs_recent = "no_norm"
    if has_distribution and this_band is not None:
        mean_ordinal = (
            sum(adj[b] * _BAND_ORDINAL[b] for b in _BANDS) / n if n else 0.0
        )
        this_run_vs_recent = _direction(
            _BAND_ORDINAL[this_band] - mean_ordinal,
            _VS_RECENT_DEADBAND,
            "harder",
            "easier",
        )

    # Trend: the recent window's hard-share vs the prior equal window's, exculpated.
    # Abstains unless BOTH windows hold enough comparable sessions.
    _, adj_prior, n_prior = _window_counts(prior, confounded_ids, adjusted=True)
    trend_delta: Optional[float] = None
    if n >= _MIN_SESSIONS and n_prior >= _MIN_SESSIONS:
        recent_hard = _hard_share_pct(adj, n)
        prior_hard = _hard_share_pct(adj_prior, n_prior)
        if recent_hard is not None and prior_hard is not None:
            trend_delta = round(recent_hard - prior_hard, 1)
    trend_direction = _direction(
        trend_delta, _TREND_DEADBAND_PCT, "harder", "easier"
    )

    return IntensityContext(
        this_session=this_session,
        window_days=_WINDOW_DAYS,
        session_count=n,
        distribution=distribution,
        distribution_adjusted=distribution_adjusted,
        confounded_session_count=sum(
            1 for f in recent if getattr(f, "activity_id", None) in confounded_ids
        ),
        this_run_vs_recent=this_run_vs_recent,
        trend_direction=trend_direction,
        trend_hard_share_delta_pct=trend_delta,
        prior_session_count=n_prior,
        has_distribution=has_distribution,
    )
