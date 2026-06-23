# Strava sometimes reports cadence per single leg (~80 spm) instead of total
# steps/min (~160). A representative cadence below this threshold looks
# single-leg, so it is doubled to steps/min. This is the ONE definition of the
# per-leg normalization rule: the read paths (normalize_cadence_spm) and the
# consolidated stream-view builder both apply it through cadence_doubling_factor,
# so the threshold and the "below -> double" decision live in a single place
# (#442). Applies to all activity types (bike/walk cadence is handled the same).
CADENCE_SINGLE_LEG_THRESHOLD_SPM = 130


def cadence_doubling_factor(cadence: float | None) -> int:
    """The per-leg normalization factor for a representative cadence value: 2 when
    it looks single-leg (below the threshold), else 1.

    `cadence` is a representative figure — a scalar average for the read paths, or a
    series mean for the stream-view builder. None (no value to judge) yields 1, so a
    caller can pass a missing mean without its own guard. Decided from one
    representative value (not per sample) so a momentary low-cadence dip during a
    slowdown is never spuriously doubled.
    """
    if cadence is None:
        return 1
    return 2 if cadence < CADENCE_SINGLE_LEG_THRESHOLD_SPM else 1


def normalize_cadence_spm(activity_type: str, avg_cadence: float | None) -> float | None:
    """
    Normalizes cadence to Steps Per Minute (SPM).

    Strava API sometimes returns cadence in full strides per minute (e.g., ~80)
    instead of steps per minute (e.g., ~160). This doubles the value when it looks
    single-leg, applying the shared per-leg rule (cadence_doubling_factor) so the
    threshold is defined once.

    This applies to all activity types as per request to handle bike/walk cadence
    similarly.
    """
    if avg_cadence is None:
        return None
    return avg_cadence * cadence_doubling_factor(avg_cadence)
