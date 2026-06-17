# The post-activity cadence is a swappable seam with one adapter per cadence

Three ADRs each added a way the coach responds after an activity lands: ADR 0010 the
two-stage opener/fuller Exchange, ADR 0011 the block-complete debounce that gates it,
ADR 0018 the instant-receipt-plus-deferred-full-report cadence (behind the orthogonal
`COACH_RECEIPT_CADENCE` flag). Each landed as config gates threaded through the
pipeline job. By #330 the decision "given the active config, what fires after an
activity?" was ~10 `is_two_stage_prompt` / `is_receipt_cadence` checks scattered
across six functions in `app/jobs/process_new_activity.py` (and the coach service).
No single place answered it; one cadence's whole lifecycle was smeared across the
file, and a new cadence meant a new gate in every event function.

This is a structural ADR. It changes **no behaviour** — the three cadences fire
exactly as before. It records how the dispatch is organized so future reviews do not
re-scatter it or mistake the organization for an accident.

**The decision.** The post-activity cadence is a seam: a `PostActivityCadence`
interface in `app/jobs/cadence.py` with one adapter per cadence —
`SingleShotCadence`, `OpenerFullerCadence`, `ReceiptCadence` — and a single
`get_active_cadence(settings)` resolver. Each post-activity event is one method on
the interface:

- `on_ingest` — a fresh activity has been ingested, analyzed, and block-assigned
- `on_block_complete` — the block-complete debounce fired (scheduled at +`BLOCK_GAP_SECONDS`)
- `on_reply` — the runner replied (a CheckIn or chat message) on a block member
- `on_done` — the runner tapped "done" (receipt cadence only)

The pipeline job's entry points (`process_new_activity`, `process_block_complete`)
and the reply/done functions (`maybe_enqueue_fuller_turn`, `mark_done_and_schedule`)
become thin dispatchers: each resolves the active cadence and calls one method. The
scattered config gates collapse to that single resolve. One cadence's full lifecycle
now reads in one adapter class; a new cadence is a new adapter, not a new gate in six
functions.

**The adapters are thin; the plumbing is unchanged.** The side-effect helpers — the
receipt send, the opener stage, the single-shot report, notification, scheduling,
sentinels, and the shared fuller generation (`process_fuller_turn`) — stay in
`process_new_activity.py` and are byte-unchanged. The adapters orchestrate them. The
only logic that moved is a pure extraction of the block-complete setup into
`_resolve_completed_block`, shared by the two two-stage adapters. This keeps the
behaviour-risky code (live prod Exchange path) untouched and the refactor's blast
radius confined to the dispatch layer.

**The rollback property is preserved by construction.** `get_active_cadence(settings)`
reads `COACH_PROMPT_ID` / `COACH_RECEIPT_CADENCE` AT DISPATCH TIME, never baking the
cadence into a scheduled job's args. A job scheduled under one cadence that fires
after a config flip resolves the now-current cadence — exactly the prior "decided at
fire time" behaviour (AC6). Flipping `COACH_PROMPT_ID` to a single-shot id, or
`COACH_RECEIPT_CADENCE` off, remains a zero-code-change rollback.

**The RQ entry points keep their import paths.** `process_new_activity_job`,
`block_complete_job`, `fuller_turn_job`, and `regenerate_report_job` are enqueued into
Redis by import path, so in-flight scheduled jobs reference
`app.jobs.process_new_activity.<name>`. They stay there with unchanged signatures;
only their bodies delegate. The reply/done functions stay too, as the stable imports
for `checkins.py` / `chat.py` / `webhooks.py`.

## Considered options

- **A swappable adapter-per-cadence seam (chosen).** One class per cadence, one method
  per event. Directly fixes the friction ("read one cadence's whole lifecycle in one
  place") and makes a new cadence a new adapter. More code moves, but as thin adapters
  over unchanged plumbing the moved surface is dispatch only.
- **Centralized dispatch without adapters** — a `Cadence` enum + a single `match` at
  the top of each event function. Rejected: it concentrates the *decision* but not the
  *lifecycle* — one cadence's behaviour stays spread across five functions, and a new
  cadence still edits all five. The friction is the lifecycle scatter, which only the
  adapter orientation removes.
- **Fold the coach service's generation-family gates (`get_or_generate_coach_report`,
  `generate_fuller`) into the seam.** Rejected as out of scope: those gate which
  *report shape* the on-demand UI path generates, a different axis from the post-activity
  cadence, and touching the on-demand path adds risk for little locality gain.
- **Leave it (do not refactor).** Rejected: the dispatch had absorbed three waves of
  accretion and the next cadence would have added a fifth gate to each event; the scan
  rated this the highest-locality-debt area.

## Consequences

- New `app/jobs/cadence.py` (the interface + three adapters + resolver). No migration,
  no schema change, no dependency change; behaviour is identical across all three
  cadences and both config axes.
- `process_new_activity.py` shrinks to thin dispatchers plus the unchanged helpers; the
  block-complete setup is extracted to `_resolve_completed_block`.
- Does NOT amend ADR 0010 / 0011 / 0018 — those decisions (what each cadence does) are
  unchanged; this records only how the dispatch between them is organized.
- A new cadence is added as a new adapter + a branch in `get_active_cadence`, not as a
  new gate in each event function. Future reviews should not re-scatter the cadence
  decision back into per-event config checks.
- Verified behaviour-preserving (Refactor): the full backend suite passes unchanged
  (the 114 cadence tests across all three config combinations encode the prior
  observable side effects), and the one extracted helper was confirmed identical on the
  real seeded prod snapshot.
