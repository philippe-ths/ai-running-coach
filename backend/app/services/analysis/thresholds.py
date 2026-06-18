"""Shared numeric invariants for the analysis pipeline.

One home for thresholds that several stages must agree on. Coupling them by a
constant (an explicit import) rather than by duplicated literals and a comment
keeps the invariant in one place, so changing it cannot silently desync the
stages that read it.
"""

# The elevated-HR-drift threshold (percent). One invariant shared by:
#   - flags.py:           drift > ELEVATED_HR_DRIFT_PCT -> fatigue_possible
#   - discount_signals.py: a confounder only discounts drift above this (#176)
#   - calibration.py:     the LABELED population guideline ("~5.0%") fallback
# These three must agree: if flags fired fatigue at one threshold while
# discount_signals explained it at another, the coach would receive
# contradictory signals about the same run. Kept as a float so the user-facing
# f-strings in calibration.py render "~5.0%".
ELEVATED_HR_DRIFT_PCT = 5.0
