# Activity classification is orthogonal axes, not one label

The classifier previously assigned each activity a single `activity_class` string (Easy Run, Long Run, Tempo, Intervals, Hills, Race, Recovery). That enum conflates four or five independent properties — how hard (effort), how long (duration), the shape of the effort (continuous vs intervals), the terrain, and whether it was a race — into one mutually-exclusive choice, so it could only ever record one and discarded the rest. On real data this collapsed almost everything to "Easy Run": every run sat at 81-89% of max HR yet was labelled easy, because the classifier had no effort logic and fell through to a default. The reported bug (a long run and an interval session both labelled "Easy Run") was a symptom of this single-label model, not of two missing thresholds.

We replace the single `activity_class` with independent classification axes on `DerivedMetric`: **Effort** (from HR zones), **Duration class** (relative to the runner's recent efforts), **Structure** (continuous vs intervals), and the **Terrain** and **Race** modifiers. A human-readable **Headline** (e.g. "Long run (tempo)") is composed from the axes at read time for the UI and coach; it is not stored. See `CONTEXT.md` for the vocabulary.

The axes are **sport-agnostic by intent**: the long-term goal is a single cross-modality training-effort timeline (runs, rides, rows, swims) the coach can reason over. Effort is implemented universally now (any activity with HR); Duration, Structure, and Terrain ship calibrated for runs, with other sports' thresholds to follow.

## Considered options

- **Keep one enum, add effort-aware precedence.** Rejected: still lossy — a long run executed at threshold can only be "Long" or "Tempo", never both, which is the exact information the coach needs.
- **Keep `activity_class` as a derived headline alongside new axis fields.** Rejected in favour of a clean break: removing the stored field forces every consumer (coach context, policy validator, flags, API schema, frontend) onto the axes and prevents the old lossy label from lingering as a parallel source of truth.

## Consequences

- `DerivedMetric.activity_class` is removed; consumers read the axes (or the derived headline). This is a schema migration plus coordinated changes across coach, validator, flags, API, and frontend.
- **Stated intent** (`user_intent`) no longer overwrites the measured class. It overrides only the headline the runner sees; the measured axes always reflect the data, so an intent-vs-executed disagreement becomes coaching signal instead of erasing the measurement.
- Duration class is **indeterminate at cold start** (the first few efforts in a runner's history have nothing to compare against); it resolves to relative once enough history exists.
- Future reviews should not re-suggest folding the axes back into one label: the orthogonality is the point.
