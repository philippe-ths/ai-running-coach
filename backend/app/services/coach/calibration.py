"""M9 self-calibrating correction + non-diagnostic referral.

Two deterministic, pipeline-owned signals computed at context-build time (the
RunnerBaseline is recomputed after the analysis pipeline, so individualisation
happens on read):

- `calibrate_drift`: turn HR-drift interpretation from a population rule of thumb
  (the ~5% band) into a per-runner anomaly — "your typical drift for THESE
  conditions is X%, today was Y%". Falls back to the LABELED population heuristic
  until enough comparable history accrues, so a confident personal claim never
  outruns the evidence.
- `assess_referral`: a non-diagnostic clinician nudge that fires only on
  deterministically-computable red-flag patterns (the multi-signal
  illness/extreme-fatigue flag; sustained notable pain across recent runs).
  Pipeline-owned and templated, NEVER a diagnosis — its text is written to pass
  the M0 medical-scope validator (no diagnosis verb, no condition, no dose), and
  it abstains rather than ever fabricating a health concern.

The brief's HR-based red flags (resting-HR rise, post-exercise HR) are not
computable from the data the app stores today (no resting HR, no continuous HR
recovery); the referral fires on the available fatigue/pain patterns and is
extensible, mirroring the M5 "documented set" discipline.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Enough like-for-like comparable runs before a personal expected drift is
# trusted; mirrors the M2 baseline abstention threshold.
MIN_SAMPLES_FOR_CALIBRATION = 4

# The legacy population heuristic (flags.py: drift > 5% -> fatigue_possible),
# surfaced as a LABELED fallback when personal history is too thin.
POPULATION_DRIFT_THRESHOLD_PCT = 5.0

# How far this run's drift must sit from the personal norm to read as notable
# (rather than within run-to-run noise).
_DRIFT_ANOMALY_MARGIN_PCT = 3.0

# Pain at or above this is "notable"; this many notable readings across recent
# runs is a sustained pattern worth a referral nudge (one painful run is not).
_NOTABLE_PAIN = 4
_MIN_PERSISTENT_PAIN_RUNS = 3


def calibrate_drift(
    observed_drift: Optional[float], comparable_drifts: List[Optional[float]]
) -> Dict[str, Any]:
    """Compare this run's HR drift to the runner's own typical drift for these
    conditions. Personal when enough comparable runs exist, else a labeled
    population heuristic. Degrades cleanly when no drift was measured."""
    if observed_drift is None:
        return {
            "calibrated": False,
            "expected_drift_pct": None,
            "observed_drift_pct": None,
            "basis": "no HR drift was measured for this run",
        }

    samples = [d for d in comparable_drifts if d is not None]
    observed = round(observed_drift, 1)

    if len(samples) >= MIN_SAMPLES_FOR_CALIBRATION:
        expected = round(sum(samples) / len(samples), 1)
        delta = round(observed - expected, 1)
        if delta >= _DRIFT_ANOMALY_MARGIN_PCT:
            comparison = "above"
        elif delta <= -_DRIFT_ANOMALY_MARGIN_PCT:
            comparison = "below"
        else:
            comparison = "in_line"
        # Retain the population guideline as a FLOOR: if the personal norm is
        # itself above it, "in line with your norm" must not be read as "fine" —
        # a sustained elevated drift is still worth flagging, not normalising.
        norm_elevated = expected > POPULATION_DRIFT_THRESHOLD_PCT
        return {
            "calibrated": True,
            "expected_drift_pct": expected,
            "observed_drift_pct": observed,
            "delta_pct": delta,
            "comparison": comparison,
            "sample_count": len(samples),
            "personal_norm_elevated": norm_elevated,
            "basis": (
                f"this runner's typical HR drift for these conditions is about "
                f"{expected}% across {len(samples)} comparable runs"
                + (
                    "; note this personal norm itself sits above the general "
                    f"~{POPULATION_DRIFT_THRESHOLD_PCT}% guideline"
                    if norm_elevated
                    else ""
                )
            ),
        }

    return {
        "calibrated": False,
        "expected_drift_pct": None,
        "observed_drift_pct": observed,
        "heuristic_threshold_pct": POPULATION_DRIFT_THRESHOLD_PCT,
        "basis": (
            f"not enough comparable runs yet ({len(samples)}); using the general "
            f"~{POPULATION_DRIFT_THRESHOLD_PCT}% drift guideline as a heuristic, "
            "not a personal norm"
        ),
    }


# Non-diagnostic nudges. Deliberately avoid the word "diagnos*" (the validator
# forbids it), condition names, medication nouns, and dose units, so the text
# survives the medical-scope validator even if the model surfaces it verbatim.
_NUDGES = {
    "multi_signal_fatigue": (
        "Several strain signals lined up on this run at once (high perceived effort, "
        "poor sleep, and pain together). If that pattern keeps recurring, consider "
        "checking in with a healthcare professional. General guidance, not a medical opinion."
    ),
    "persistent_pain": (
        "Notable pain has shown up across several of the recent runs. Pain that persists "
        "like that is worth a professional assessment. General guidance, not a medical opinion."
    ),
}


def assess_referral(
    *, flags: Optional[List[str]], pain_scores: Optional[List[int]]
) -> Optional[Dict[str, Any]]:
    """Return a non-diagnostic referral nudge for a computable red-flag pattern,
    or None. Never fabricates a concern: only fires on the multi-signal
    illness/extreme-fatigue flag or sustained notable pain."""
    flags = flags or []
    if "illness_or_extreme_fatigue" in flags:
        return {"pattern": "multi_signal_fatigue", "nudge": _NUDGES["multi_signal_fatigue"]}

    notable = [p for p in (pain_scores or []) if p is not None and p >= _NOTABLE_PAIN]
    if len(notable) >= _MIN_PERSISTENT_PAIN_RUNS:
        return {"pattern": "persistent_pain", "nudge": _NUDGES["persistent_pain"]}

    return None
