# The Block is the event unit the coach speaks about

A4 shipped the two-stage `Exchange` on a deliberate stopgap: every activity is its own block (block-of-one), the exchange lifecycle squats on the `coach_reports` row, and its at-most-once sentinels live on `Activity` (ADR 0010). That is true by construction for solo runs but wrong for the real shape of the owner's training: a walk→run→bike morning is one training event, and today it produces three separate exchanges, three openers, three pings. The north-star (decision 10) names the fix: deterministically group temporally-contiguous activities into one `Block`, and the coach reasons and speaks about the block, one exchange per block. A1 adopts it.

**The decision.** Three first-class rows replace the stopgaps.

A **`blocks` table** groups a user's temporally-contiguous activities by time-gap clustering: an activity whose start follows the previous activity's end by less than `BLOCK_GAP_SECONDS` joins that block, otherwise it starts a new one. Detection is deterministic and auditable; per-activity analysis is unchanged underneath. Each block has a **primary activity** (the run, else the longest member) and the runner can correct a wrong grouping by **split/merge** (API-level in A1; corrections adjust grouping and aggregates but never re-fire a notification).

An **`exchanges` table** owns the exchange lifecycle: one row per block, carrying the opener/fuller state and the per-stage at-most-once notification sentinels that A4 parked on `Activity`. `coach_reports` is demoted to what it really is, the versioned generation artifact: its M0 cache identity `(activity_id, prompt_id, schema_version)` is untouched (keyed by the block's primary activity), prior prompt/schema versions are retained exactly as before, and cutover/rollback stays a `COACH_PROMPT_ID` flip.

**The block-complete gap doubles as the exchange trigger.** When an activity finishes processing, a block-complete check is scheduled at +`BLOCK_GAP_SECONDS`; if another activity has joined by then, the stale check no-ops and a fresh one is already scheduled (idempotency over cancellation, the A4 timer pattern). The opener fires at block-complete, so it genuinely speaks about the event unit — a solo run's opener arrives ~the gap after sync rather than instantly, which lands on the owner's own observation that cognitive capacity immediately post-run is low. The fuller-turn timer and reply window are unchanged from A4, anchored on the opener.

**Late arrivals: open-absorbs, closed-starts-new.** An activity syncing into a block whose exchange is still open (opener sent, fuller not) joins the block, and the fuller turn covers the whole, now-larger event. Once the exchange is closed (fuller sent), it is never re-opened: a late activity starts a new block with its own exchange, preserving A4's at-most-once delivery semantics exactly.

**Signals stay activity-level; the block adds a thin aggregate.** Novelty, digests, baselines, and the M4-M10 learning loop keep their activity keying through the primary activity. A multi-member block adds a small `block` section to the context pack (member list, combined duration/distance/load) so the coach can speak about the morning as a whole. Block-level signal rework waits for evidence it is needed (P3's training-load model is the natural consumer).

**A thin `coaching_relationship` singleton ships now** (owner-ratified fork, overriding the defer-to-P1 recommendation): one row per user (`id`, `user_id` unique, `created_at`), the anchor P1's voice/stance dials and later relationship state will extend, following the `RunnerBaseline`/`CoachNarrative` singleton pattern.

## Considered options

- **Keep block-of-one (A4 status quo).** Rejected: multi-activity sessions are real in this user's training, and "one exchange per block" stays a fiction the moment one happens. A4 explicitly deferred this debt to A1; A1 is where it comes due.
- **Extend `coach_reports` with block/lifecycle columns instead of an `exchanges` table.** Rejected: lifecycle state is per-event and must be unique, while report rows are deliberately plural (prior prompt/schema versions retained). Lifecycle on versioned rows means a prompt flip duplicates or orphans exchange state.
- **Rework `coach_reports` into `exchanges` wholesale.** Rejected: unwinds the A3/A4 versioned-cache design and the config-flip rollback story for no behavioural gain.
- **Instant opener on the first activity, only the fuller block-gated.** Rejected (kept as the fallback if the delay grates in practice): the opener would knowingly speak about a partial event, which contradicts the point of the block as the event unit. The gap is one config value if the trade-off needs revisiting.
- **One opener per activity, one fuller per block.** Rejected: multi-activity mornings get multiple pings, the most visible contradiction of one-exchange-per-block.
- **Always absorb late arrivals, re-opening closed exchanges.** Rejected: breaks the at-most-once sentinels and re-pings the runner about an event the coach already covered.
- **Strict cutoff (anything post-opener starts a new block).** Rejected: the fuller turn would describe a stale partial event while a sibling exchange covers the rest of the same morning; absorbing into the still-open fuller is free with the machinery A4 built.
- **Full block-level signals (axes, digests, baselines, adherence) in A1.** Rejected: a large rework of the M4-M10 reads ahead of any evidence that activity-level signals misbehave on multi-activity blocks, in a dataset where almost every block is a block-of-one.
- **No `coaching_relationship` table until P1.** The recommendation, overridden by the owner: the thin row ships now so the relationship has a durable anchor from the start; P1 alters rather than creates.

## Consequences

- A1 carries migrations: `blocks`, `exchanges`, `coaching_relationship`, `activities.block_id`, and a backfill that wraps every historical analysed activity in a block-of-one with a closed exchange, so reads never special-case pre-A1 rows. The `Activity` sentinels stop being written but are retained until a later cleanup (their removal is a separate, owner-approved delete).
- The opener is no longer instant; it arrives ~`BLOCK_GAP_SECONDS` after the last activity of the block syncs. This is a deliberate trade ratified against the owner's stated taste for immediacy; the fallback (instant opener, block-gated fuller) is named above if living with it changes the call.
- The block-complete trigger replaces "activity processed" as the exchange trigger only under the two-stage prompt (`coach_message_v2`); single-shot prompts keep the per-activity immediate path, so the A4 rollback flip still needs zero code change.
- Chat stays per-activity (anchored to the primary activity's report) until I2; split/merge stays API-only until I3 gives it a surface.
- "One exchange per block" becomes literally true, closing the construction-only caveat in ADR 0010.
- Future reviews should not re-suggest instant openers, lifecycle columns on `coach_reports`, re-openable exchanges, or block-level signal rework: the debounced opener, the separate `exchanges` table, closed-is-closed, and activity-level signals with a thin aggregate are the design, not accidents.
