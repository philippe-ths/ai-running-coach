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

The whole row is flattened into `MetricsContext` (`build_focus_payload`,
`context.py:593-627`). MOST columns are **forwarded**, but the hop is NOT uniformly
verbatim — four exceptions:

- forwarded (verbatim): effort, duration_class, structure, is_hilly, is_race,
  time_in_zones, flags, confidence, confidence_reasons,
  interval_structure, workout_match, interval_kpis, risk_level, risk_score,
  risk_reasons, training_context, discount_signals.
- **effort_score / hr_drift / pace_variability → reduced (rounded).** Rounded to 1 dp
  into the pack; hr_drift / pace_variability are nulled when 0 (`context.py:600-606`).
- **efficiency_analysis → reduced (reshaped).** The stored column carries a 128-pt
  curve; `_summarize_efficiency_for_coach` (`context.py`, #441) drops the curve and adds
  a coarse `trend` descriptor before it enters the pack — not a verbatim copy.
- **stops_analysis → reduced (location stripped).** The stored column carries a per-stop
  `[lat, lng]` `location`; `_strip_stops_for_coach` (`context.py`, #460) drops `location`
  from each stop before it enters the pack — the LLM cannot use raw coordinates — keeping
  the timing/duration/distance fields and the summary scalars. The stored DerivedMetric
  and the activity-detail StopsPanel still read `location` directly.
- **stream_view (sv_points) → forwarded (separate deferred edge).** Unlike every other
  DerivedMetric column, this one does NOT flatten into `pack.metrics`. The ≤60-pt aligned
  downsample (`stream_view.py`) is written to a `deferred=True` column
  (`derived_metric.py:51`) at analysis time, then forwarded unchanged onto its OWN
  `pack.stream_view` section by `retrieval.fetch_stream_view` (a separate `undefer` query),
  gated on `deep=True`. Since #443, `deep = is_stream_view_prompt(prompt_id)`
  (`context.py:217`), so it rides EVERY report under a stream-view-aware prompt (v10/v11,
  including the live prod prompt v11) and is absent only under v9 and below. The diagram is
  captured under prod v11, so this edge is open and the view flows to the model. The
  ≤60-pt reduction is an analysis-time write, not a loss on this hop.

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
- **internal** (read-time detail view only — `splits.py` is imported by
  `api/activities.py` + `coach/chat.py`, NOT by `_orchestrator.py`):
  - grade_smooth — splits + stream_view (`splits.py:40`, `stream_view.py:52`)
  - cadence — splits + stream_view + detail-view smoothing (`splits.py:41`, `stream_view.py:53`, `smoothing.py`)
  - altitude — splits only (`splits.py:43`)
  - watts — splits only (`splits.py:42`)
  - latlng — stop locations inside `stops_analysis` (`stops.py:13,45`), rendered by the
    StopsPanel detail view. Since #460 the coach path is severed: `_strip_stops_for_coach`
    drops the per-stop `location` before stops_analysis enters the pack, so latlng reaches
    the detail view only, never the coach report.
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
