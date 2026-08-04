# The coach conversation is a relationship-scoped Thread, not a per-activity chat

Chat was built as a property of an activity. `coach_chat_messages.activity_id` is a non-null FK, `stream_chat_response(db, activity_id, message)` loads that activity's stored `CoachReport.context_pack` as its context, and the UI is a panel embedded in the activity detail page. That was the right shape when chat meant "ask a follow-up about the run you are reading".

It stopped being the right shape as soon as the relationship became the protagonist. `CONTEXT.md` has said for some time that the `Coaching relationship` is the unit and that "every coach touchpoint reads from and writes back to this one shared memory, so each touchpoint is a continuation rather than a fresh start" — but the storage said otherwise, so continuity had to be faked. `#339` did exactly that: `_build_cross_activity_block` injects a bounded digest of ~8 turns from the runner's *other* activities at read time, and its own commit notes record the real fix as the deferred **Fork 1**, "a true relationship-scoped thread + unified-thread frontend". `#685` then had to teach chat to work with no report at all, because under the receipt cadence a runner can ask a question before any report exists. Both are the same pressure: the conversation is not a property of a run.

The forcing function is the app-wide coach surface — a sheet reachable from every screen. Per-activity storage cannot express it. A question asked from the Trends page belongs to no activity, and a runner who walks from a run to Trends mid-conversation would cross a storage boundary in the middle of a sentence.

## Decision

The conversation unit is a **`Thread`**: runner-initiated, relationship-scoped, resumable, and anchored to nothing by default. Messages belong to a thread; a thread belongs to a user.

Four properties are load-bearing.

**1. A thread is a topic boundary, not a memory boundary.** Starting a new thread never resets the coach. What the coach carries between turns is `Durable memory` (the `Runner memory profile`) plus a bounded digest of the runner's other recent conversation — never the visible transcript of the current thread alone. This is the invariant that lets threads exist at all without fragmenting the relationship the way per-activity storage did.

**2. A thread is a sibling of `Exchange`, not a kind of it.** An `Exchange` is coach-initiated and anchored to an event (a completed `Block`, a `CheckIn`). A thread is runner-initiated and anchored to nothing. Neither contains the other. Where they meet, the thread **displays** the exchange rather than absorbing it: an activity-anchored thread renders that activity's `CoachReport` at its head as a **read-time projection**, never a copied message. So `coach_reports` remains the single store for report text, `force=true` regeneration updates what the thread shows for free, and the versioned `(activity_id, prompt_id, schema_version)` cache identity is untouched. The one concession: an activity-anchored thread may be brought into existence by the exchange it displays, so the runner's first message is not always what creates it.

**3. The anchor is a framing hint, never a data boundary.** A thread born on an activity page keeps that activity as its anchor, which decides two things only: which screen context is attached at turn one, and where the thread is listed. It gates nothing. The query tools stay scoped to the **owner**, exactly as `query_tools.py` already enforces, so an anchored thread can discuss any run in the runner's history and a cross-user id still returns empty.

**4. The coach proposes writes; the runner's tap performs them.** A thread can carry a typed, server-minted proposed action (check-in, stated intent, block split/merge) executed through the existing service path on confirmation. The model chooses *which* action with *which* arguments; it never authors the write. This is the `TappableOption` → Telegram inline button → `write_checkin` pattern moved in-app, and it keeps the sovereignty rule ADR 0012 protects: nothing about the runner changes because a model decided it should.

## Consequences

- **The per-activity chat panel is removed and its rows migrate.** Every activity with chat becomes one thread anchored to that activity. This is the one irreversible step in the work, so it lands in its own slice with the old UI still rendering the migrated rows as proof nothing was lost, and the panel is removed last and separately — it has no technical rollback, only a re-add.
- **`_build_cross_activity_block` retires as a threading mechanism.** Cross-thread continuity becomes a real digest across threads rather than a simulation across activities.
- **The stored report pack stops being chat's context source.** Chat inherited whatever the report pack happened to carry; a thread turn instead assembles a **relationship baseline** — the runner-and-now-anchored sibling of `build_b_baseline` — plus the current screen view, plus tools. Anything a thread genuinely needs from the pack is named explicitly rather than inherited by accident.
- **The memory update pass gains a trigger.** A thread that goes quiet enqueues the same `update_memory_job`, bounded to one pass per runner per interval. Safe to run more often precisely because ADR 0025's writer rebuilds from source and never reads its own prior output.
- **Deletion works structurally.** Because the memory writer re-derives from source every pass, deleting a thread's messages means the next pass simply does not see them — no cascade, no tombstone, no "forget this" mechanism. This holds only as long as nothing caches derived text from a thread outside that rewrite loop; anything that does silently breaks deletion.
