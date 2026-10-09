# The chat coach reaches the whole stored record

A runner asked the chat coach for their weekly zone 2+ time and got "I can't confirm the weekly zone 2+ figure." Every one of their activities held its time in each heart-rate zone, and the challenge tracker already computed the weekly figure from it. No chat tool returned it.

The tools were built under a sound rule: the coach picks from a few fixed options and the server computes, never a query the model composes, because models are fluent in temporal and statistical language and unreliable at the arithmetic behind it (#648). But the tools were built for the questions foreseen when each one was written, and nothing checked what they left out. The rule about how the coach asks had quietly become a limit on what it could reach. A runner can ask anything, so a gap there is found by a runner, one question at a time.

## Decision

**The coach still chooses and the server still computes. What the coach can reach is the whole stored record, and a test holds it there.**

- `get_training_metric` measures any stored quantity over a named window, whole or per calendar week or month. Its metrics are declared once in `backend/app/services/coach/training_metrics.py`. The coach picks one by name.
- Each metric declares how it combines (sum, time-weighted mean, mean, max, pace, count by label). The coach never chooses. Summing average heart rate across sessions gives a number that looks right and means nothing, and a model offered that choice will eventually make it.
- A session that did not record a measure is left out of the figure and counted, never read as zero. A week where the heart-rate strap stayed home reads as incomplete, not as an easy week.
- `list_activities_in_range` and `get_session_detail` return every measure each session recorded.
- `test_training_metrics_1071.py` scans every stored per-activity field (`ActivityFact`, `Activity`, `DerivedMetric`). Each one is read by a metric or listed in `STORED_FIELDS_EXCLUDED` with where it is served or why it is not. A new stored field fails the build until someone makes that call.

Zone time comes from `norms.zone_seconds`, the same computation as the challenge tracker, which spreads each zone's share of heart-rate samples over moving time. The chat figure and the Schedule screen therefore agree.

## What was rejected

**A narrow tool per gap.** Consistent with the old design, and it fixes only the questions someone has already seen fail.

**SQL or code execution over the runner's data.** The most general option. It hands the arithmetic back to the model, puts raw columns without units in front of it, and makes owner scoping something every generated query has to get right.

**A live Strava connection for the coach.** Strava bins heart rate into its own zones, not the runner's calibrated ones, so the coach would hold two different "zone 2+" figures. It would also duplicate data the app already syncs and add per-user OAuth to the backend.

## Consequences

The structured analyses on `DerivedMetric` (stops, efficiency, workout match, interval KPIs, training context, discount signals) are listed as excluded and not yet served to chat. They are visible decisions in the exclusion list rather than a silent gap.
