"""The version of what analysis stores, so a change to it reaches old activities.

A change to how analysis derives a stored `DerivedMetric` value only reaches
activities analysed after it, unless history is re-analysed. Bump
`ANALYSIS_VERSION` in the same change, and the worker re-analyses every runner's
stored history once when that deploy boots
(`jobs.reanalyze_history.reanalyze_if_analysis_changed`).

Bumps:
1. #1065 — zone time on the moving clock, not by counting samples.
"""

ANALYSIS_VERSION = 1
