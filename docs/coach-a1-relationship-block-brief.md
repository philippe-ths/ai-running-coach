# A1 Relationship + Block Model Build Brief

> The what. The why and the decision live in `docs/adr/0011-block-is-the-event-unit.md`; the vision in `docs/vision/coach-north-star.md` (decision 10, roadmap A1). Vocabulary (`Block`, `Exchange`, `Coaching relationship`) is in `CONTEXT.md`. Owner forks ratified in the 2026-06-12 design session: new `exchanges` table (`coach_reports` stays the versioned artifact); gap-threshold debounce gates the opener (block-complete is the exchange trigger); late arrivals open-absorb / closed-start-new, split/merge API-only and never re-notifies; thin `coaching_relationship` singleton now (owner override of the defer-to-P1 recommendation); signals stay activity-level with a thin block aggregate.

## Goal

Temporally-contiguous activities group deterministically into one `Block`; the coach speaks once per block, not once per activity. The `Exchange` becomes a first-class row owning the two-stage lifecycle and its at-most-once sentinels, with `coach_reports` demoted to the versioned generation artifact (M0 cache identity untouched). A thin `coaching_relationship` singleton anchors the relationship. The A4 cadence, learning loop, eval gate, and rollback flip all survive unchanged.

## Deliverables

1. **`blocks` model + migration**: `id`, `user_id`, `start_date`, `end_date`, `primary_activity_id`, `user_corrected` (split/merge audit bit), `created_at`; `activities.block_id` FK (nullable, indexed). Backfill: every historical analysed activity becomes a block-of-one.
2. **`exchanges` model + migration**: `id`, `user_id`, `block_id` (unique — a closed exchange is never re-opened, so one exchange per block holds strictly), state timestamps (`opener_sent_at`, `fuller_sent_at`, equivalents of the A4 sentinels), `created_at`. Backfill from existing `Activity` sentinel columns so historical exchanges read as closed. `Activity.opener_notification_sent_at`/`coach_notification_sent_at` stop being written; their removal is a later, separately-approved delete.
3. **`coaching_relationship` model + migration**: thin singleton (`id`, `user_id` unique, `created_at`), auto-created alongside the user the way `UserProfile` is on first read.
4. **Block detection** (`app/services/blocks.py`): pure time-gap grouping — an activity joins the previous block when the gap from that block's end to its start is under `BLOCK_GAP_SECONDS`, else starts a new one; primary activity = the run, else the longest member; assignment runs at ingestion for new activities. Split/merge as pure functions over a block's members.
5. **Block-complete trigger** (`app/jobs/`): on activity processed (two-stage prompt only), `enqueue_in(BLOCK_GAP_SECONDS, block_complete_job)`. The job no-ops unless this activity is still the block's last member and the block's exchange has not opened (idempotency over cancellation, the A4 pattern). At block-complete: opener on the primary activity → notify → conditionally schedule the fuller turn, all per A4. Single-shot prompts keep the per-activity immediate path untouched (rollback gate).
6. **Late-arrival handling** (`app/jobs/` + `app/services/blocks.py`): an activity grouping into a block whose exchange is open joins it (the fuller covers the larger event; a pending fuller timer needs no change, the fuller reads current state at fire time); a closed exchange is never re-opened — the late activity starts a new block.
7. **Pipeline + service rework**: `process_new_activity_job` assigns the block and schedules the completion check instead of generating the opener inline; `generate_opener`/`generate_fuller`/`maybe_enqueue_fuller_turn` read and write exchange state instead of `Activity` sentinels and the opener-only report-shape probe; `coach_reports` keying stays `(primary_activity_id, prompt_id, schema_version)`.
8. **Block pack section** (`context.py`): for multi-member blocks only, a small `block` section (member list with type/duration/distance, combined totals) in the primary activity's pack; blocks-of-one emit nothing new (pack byte-stable for the solo-run case). Novelty, digests, baselines, adherence unchanged.
9. **Split/merge API** (`app/api/`): endpoints to split a block at an activity and merge adjacent blocks; corrections set `user_corrected`, reassign members, recompute `start_date`/`end_date`/primary, and never re-fire a notification. No frontend (I3).
10. **Config + docs**: `BLOCK_GAP_SECONDS` (default 1800); ADR 0011 (done); `project-context.md` (models, jobs, trigger, API); `CONTEXT.md` reconciliation (`Exchange` "immediate opener" → block-complete-gated; `Coaching relationship` materialised row).

## Acceptance criteria

- AC1: Two activities within the gap form one block with one exchange and one opener (on the primary activity); a third past the gap starts a new block. A solo run's opener fires ~`BLOCK_GAP_SECONDS` after processing, not instantly.
- AC2: The debounce is idempotent: a second activity joining the block makes the first completion check a no-op; exactly one opener fires per block regardless of arrival interleaving.
- AC3: A late activity into an open exchange joins the block and the fuller turn covers the whole event; into a closed exchange it starts a new block-of-one, and the closed exchange's sentinels never re-fire.
- AC4: Split/merge endpoints correct a grouping, set `user_corrected`, and send nothing.
- AC5: At-most-once per stage holds on the `exchanges` row exactly as it did on `Activity`; `force=true` regeneration re-fires nothing.
- AC6: Flipping `COACH_PROMPT_ID` to a single-shot id serves the per-activity immediate path with zero code change; a pending `block_complete_job` under a rolled-back prompt no-ops with a log line.
- AC7: Backfill leaves every historical analysed activity in a block-of-one with a closed exchange; `fetch_prior_digests`, `fetch_prior_commitments`, the M8 write-back, and `make eval` behave identically before and after (the learning loop never notices A1).
- AC8: Blocks-of-one produce a byte-stable context pack (no `block` section), pinned by test.

## Verification

TDD throughout; `make backend-test` green at every step. Weighted to grouping and cadence: unit tests for the pure grouping/split/merge/primary-activity functions (boundary gaps, sport mixes, out-of-order sync); job tests for the debounce idempotency, late-arrival paths, and rollback no-op (fake clock, in-memory queue); migration roundtrip + backfill assertions on local Postgres against `make seed-local` data; pack byte-stability test for blocks-of-one. End-to-end on seeded data: a synthetic back-to-back pair produces one exchange. Production flip needs nothing — `coach_message_v2` is already live; the new behaviour arrives with the deploy.

## Out of scope

Chat-as-continuation into shared relationship memory (I2); any split/merge or block UI (I3); block-level signals — axes, digests, baselines, adherence (future, evidence-driven); columns on `coaching_relationship` beyond the thin anchor (P1); the `Training load` block aggregate model (P3); dropping the deprecated `Activity` sentinel columns (separate approved delete).
