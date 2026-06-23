# Field fate inventory

Ground truth for the "second chip" on the flow diagrams: where each field GOES (or
doesn't) on the way to the coach report. Companion to `FIELD_SOURCE` (provenance =
where it came from) in `flow-nodes.js`.

Captured reference: activity `256ebb60` ("Afternoon Run", 2026-06-18), prompt
`coach_message_v7`. Verified against backend code at the cited file:line.

## Fate vocabulary (the five labels)

- **forwarded** — reaches the model's input (placed into the context pack, directly
  or as a derived value).
- **reduced** — reaches the model/store but lossily compressed (downsample, digest).
- **gated** — forwarded only under certain prompt ids / config flags; dropped otherwise.
- **dropped** — terminal: nothing downstream consumes it on the path to the report.
- **internal** — consumed by a later stage but never exported to the coach pack
  (e.g. feeds the read-time activity-detail view only, never the LLM).

## A. DerivedMetric row → pack.metrics

The whole row is flattened into `MetricsContext` (`context.py:496-525`), so every
column is **forwarded** EXCEPT one:

- forwarded: effort, duration_class, structure, is_hilly, is_race, effort_score,
  hr_drift, pace_variability, time_in_zones, efficiency_analysis, stops_analysis,
  flags, confidence, confidence_reasons, interval_structure, workout_match,
  interval_kpis, risk_level, risk_score, risk_reasons, training_context
  (`context.py:480,524`), discount_signals.
- **stream_view (sv_points) → reduced + deferred.** A ≤60-pt aligned downsample
  (`stream_view.py`), stored on a `deferred=True` column (`derived_metric.py:51`),
  loaded ONLY when `deep=True` (`context.py:534`). Absent from the default coach
  report; pulled only on an explicit deep-dive.

pack.metrics also adds three fields composed at read time (not DM columns), all
**forwarded**: headline (`context.py:497`), zones_calibrated / zones_basis
(computed from profile, `context.py:472-476`).

## B. ActivityStream (11 raw streams) → analyze

The raw per-sample series never reaches the model directly; what's forwarded is the
derived numbers. Fate is relative to the coach report.

- **forwarded** (as derived metrics, never raw):
  - heartrate — time_in_zones / hr_drift / efficiency / intervals (`metrics.py:26,103,140`, `intervals.py:53`)
  - velocity_smooth — pace_variability / efficiency / intervals (`metrics.py:79,104,139`)
  - time — zone binning / stops / intervals (`metrics.py`, `stops.py:12`, `intervals.py`)
  - distance — stops / intervals / workout_matching (`stops.py:14`, `intervals.py:100`)
  - moving — stops_analysis (`stops.py:11`, `metrics.py:113`)
  - latlng — stop locations inside stops_analysis (`stops.py:13,45`)
- **internal** (read-time detail view only — `splits.py` is imported by
  `api/activities.py` + `coach/chat.py`, NOT by `_orchestrator.py`):
  - grade_smooth — splits + stream_view (`splits.py:40`, `stream_view.py:52`)
  - cadence — splits + stream_view + detail-view smoothing (`splits.py:41`, `stream_view.py:53`, `smoothing.py`)
  - altitude — splits only (`splits.py:43`)
  - watts — splits only (`splits.py:42`)
- **dropped**:
  - **temp** — no analysis stage reads the temp STREAM. The coach's temperature
    comes from the scalar `raw_summary.average_temp`, not this series
    (`discount_signals.py:69` is only a comment).

## C. raw_summary keys → pipeline

- **forwarded**:
  - average_temp — discount_signals → pack + baseline/calibration bucket
    (`discount_signals.py:25,60,87`, `baseline.py:74`, `context.py:728`)
  - laps — recorded-laps interval detection → interval_structure / workout_match
    (`intervals.py:417`, `_orchestrator.py:224`)
- **internal**:
  - sport_type — classifier `_is_run` → classification (`classifier.py:76`)
- **dropped**:
  - **average_speed** — top-level unused; `intervals.py:294` reads a per-LAP
    `average_speed`, not this.
  - **total_elevation_gain** — redundant; the consumed copy is `Activity.elev_gain_m`
    → `pack.activity.elev_gain_m`.
  - **nlaps** — intervals reads the `laps` list, never the count.
  - **average_heartrate** — redundant; the consumed copy is `Activity.avg_hr`
    → `pack.activity.avg_hr`.

## D. Activity row → pack.activity

pack.activity (`context.py:483-495`) carries date, name, type, distance_m,
moving_time_s, avg_hr, max_hr, avg_cadence, elev_gain_m. On the row but NOT forwarded:

- **dropped**: elapsed_time_s (not in pack.activity), strava_activity_id
  (identifier; storage/dedup only).
- forwarded: start_date_local → `date` (local_start, `context.py:484`).

## E. Pack sections gated by prompt id (node-level)

Under `coach_message_v7` the following are present; under a plain single-shot prompt
they drop (`gated`):

- corpus, stance, training_load, user_materials sub-field — v4/v5/v6/v7 activate them.
- salience, continuity — two-stage only.
- training_volume — v9 only; absent under v7.

## F. Off-by-default memory (already greyed in the diagram)

believed_facts, narrative, preference_profile, longitudinal.prior_reports, adherence
— disabled by config (PR #403/#418). A config-gate, distinct from a structural drop;
the diagram already marks these. (`longitudinal.prior_reports` is also a **reduced**
digest by construction: headline/lead/next_steps only, never the full report body.)

## G. Model output

- LLM extended thinking → **dropped** (not persisted). `coach_reports.report` stores
  the prose `message` + the structured tail only.
