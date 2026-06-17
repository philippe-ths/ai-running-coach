# The post-activity cadence is an instant deterministic receipt plus a deferred full report

ADR 0010 made the post-activity touchpoint a two-stage Exchange: an LLM **opener**
fired at block-complete (debounced by `BLOCK_GAP_SECONDS`, ADR 0011), then a
**fuller turn** at +`EXCHANGE_STAGE2_DELAY_SECONDS` (3h) or on a reply. In practice
the timing was wrong (#296): the "how did it feel?" prompt is welded to the opener,
so the runner got **silence after each activity** for the whole block gap, then a
**3h wait** for depth — and on a real walk→run→bike morning the opener itself
**fell back** (the LLM truncated at `max_tokens`, #295). The instant acknowledgement
(which should be immediate) was conflated with block-completion detection (which
legitimately must wait). This ADR amends 0010/0011's cadence; the Block model, the
Exchange row, and the report's content/versioning are unchanged.

**The decision.** The post-activity touchpoint becomes two things split by what each
needs:

1. An **instant, deterministic, block-aware receipt** fires per activity on ingest:
   a brief acknowledgement that reflects the session shape so far ("second one
   today, right after the walk") plus RPE/pain + a **"done"** tap. It is filled
   deterministically from the block facts, so **no LLM is on the hot path and it can
   never fall back**. Personality comes from voiced phrasings **pre-generated
   offline** through the runner's Voice (a house-default floor when none); the
   block facts are always filled deterministically, so the voice flexes delivery
   only — never the facts (ADR 0013).

2. The **full LLM report** (the deep coaching prose, unchanged) fires **~`BLOCK_GAP_SECONDS`
   after the session goes quiet** via the existing block-complete debounce — the
   no-tap path that always arrives — or when the runner taps **"done"**. The **3h
   fuller timer is retired**; the grouping gap doubles as the full-report timer.
   An RPE/pain reply only records the check-in; it no longer early-fires the report,
   so the report always covers the whole session.

**Gated by a flag orthogonal to the prompt.** The cadence is switched by
`COACH_RECEIPT_CADENCE`, independent of `COACH_PROMPT_ID`. The receipt has no prompt
and the full report reuses the configured prompt's **fuller mode**, so the cadence
and the prompt CONTENT roll back independently; flipping the flag off restores the
prior two-stage opener/fuller cadence with zero code change, and the flag is inert
under any single-shot prompt (no fuller mode to fire). The receipt-vs-opener choice
is made at job FIRE time, so a check pending across a flag flip does the now-current
thing.

**Exchange lifecycle mapping.** `opened_at` = the first receipt (anchors the reply
window, independent of delivery); a new `exchanges.done_at` records the "done" tap;
`fuller_sent_at` keeps its meaning as the CLOSED sentinel (the full report was sent);
`opener_sent_at` is unused under this cadence (retained). Receipts are per-activity
while the exchange is per-block, so the receipt dedup sentinel is a new
`activities.receipt_sent_at`. Late arrivals are unchanged from ADR 0011:
open-absorbs, closed-starts-new; a straggler joining an open block gets its own
receipt and resets the block-complete timer.

**Voiced receipts are the first untrusted-input auto-send, so containment is the
posture** (ADR 0013/0017): generation is structured-output-only (a forced tool, no
free-form channel), coerced through a strict schema (extra forbidden, counts/lengths
bounded), then per-variant validated — a variant may reference ONLY the deterministic
fact slots, and rendered with sample facts must clear the medical-scope floor
(`validator.check_medical_overreach`). A variant that fails is dropped; a situation
left with none falls back to the house floor. The untrusted Voice free-text rides
only the data channel.

**#295 folded in.** The full report's `max_tokens` truncation now retries at an
**escalated** token budget rather than the same one that just truncated (thinking can
eat a tight budget before any prose on a rich multi-activity pack), and a still-
truncated turn is logged loudly rather than silently shipped as canned text.

## Considered options

- **A new prompt id (`coach_message_v8`) instead of a flag.** Rejected: the cadence
  is orthogonal to prompt CONTENT (the receipt has no prompt; the full report reuses
  the configured prompt's fuller mode), so minting an id whose content duplicates
  another's would be misleading and would force a lineage choice (build on v6 prod,
  or v7 materials?). A dedicated flag keeps cadence and content decoupled.
- **Keep the LLM opener but fire it instantly per activity.** Rejected: it re-introduces
  the #295 fallback on the instant path (the most visible failure) and speaks about a
  partial event. A deterministic receipt is instant and cannot fall back.
- **Keep the 3h fuller timer.** Rejected: the owner's complaint is the wait; the block
  gap already detects "session over", so the full report at +`BLOCK_GAP_SECONDS` always
  arrives without a second long timer.
- **Let an RPE/pain reply early-fire the full report (the A4 behaviour).** Rejected
  under this cadence: it would fire the report off the first activity's reply and miss
  the rest of the session. The "done" tap is the explicit early-fire; the block-quiet
  timer is the guarantee.
- **Generate the voiced receipts on the hot path / lazily inline.** Rejected: an LLM
  call on ingest would make the receipt slow and able to fail — the whole point is an
  instant, can't-fall-back receipt. Generation is offline (on a voice change, or a
  background lazy refresh); the receipt always reads stored templates or the floor.

## Consequences

- This carries a migration: `exchanges.done_at`, `activities.receipt_sent_at`, and
  `coaching_relationship.receipt_templates` (+ voice key + generated-at) — all
  nullable, zero-backfill, so the prior cadence is byte-stable until the flag is on.
- A new deterministic `services/coach/receipt.py` (situation taxonomy + fact-slot
  contract + house floor) and `services/coach/receipt_voice.py` (offline generation
  + validation), a `generate_receipt_templates_job`, and a "done" callback kind.
- Amends ADR 0010 (the opener/fuller staging is replaced by receipt/full-report
  under this flag) and ADR 0011 (the block-complete debounce now fires the full
  report, not the opener). The Block model, the `exchanges`/`coach_reports` rows,
  and the report content/versioning are unchanged.
- Rollback is a single `COACH_RECEIPT_CADENCE=false` flip (restores A4's
  opener/fuller); a single-shot `COACH_PROMPT_ID` still serves the prior per-activity
  path. The config default ships the flag OFF.
- Future reviews should not re-suggest an instant LLM opener, a 3h fuller timer, a
  reply-fires-the-report rule, or a prompt-id gate for the cadence: the deterministic
  receipt, the block-gap full-report timer, the explicit "done" tap, and the
  orthogonal flag are the design, not accidents.
- **Receipt pain is intentionally location-agnostic (#303).** The pain tap records a
  `CheckIn` with `pain_score` and no `pain_location`. The coarse tap set has no place
  to capture a body location, and adding per-location buttons would be a UX change out
  of scope for the receipt. This means receipt pain feeds the location-agnostic M9
  referral / red-flag check (which only needs a score) but NOT the M6 per-location pain
  trend (which requires a location to avoid conflating distinct niggles). This is a
  deliberate choice, not a silent data drop. The in-app check-in form remains the
  place for a runner to supply a location and contribute to the location-scoped trend.
