"""The version of what analysis stores, so a change to it reaches old activities.

A change to how analysis derives a stored `DerivedMetric` value only reaches
activities analysed after it, unless history is re-analysed. Bump
`ANALYSIS_VERSION` in the same change, and the worker re-analyses every runner's
activities that have stored streams once when that deploy boots, resuming on a
later boot if the pass is cut off
(`jobs.reanalyze_history.reanalyze_if_analysis_changed`). Summary-only
activities (a history import, or a backfill not yet run) are not reached: they
keep their stored values until their streams are fetched and analysed.

Bumps:
1. #1065 — zone time on the moving clock, not by counting samples.
2. #971 — a rep session is only a run's plan when the run shows reps, and a
   wrong auto-tick is withdrawn as each run is re-analysed. Before version 2
   first ran in production it also carried #1121 (a run marked Tempo or
   Intervals counts too), #1129 (a named sport is read from `sport_type`) and
   #1122 (a pass walks oldest first, so it converges), so no bump for those.
"""

ANALYSIS_VERSION = 2
