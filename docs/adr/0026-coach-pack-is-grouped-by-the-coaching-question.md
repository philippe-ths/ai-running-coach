# The coach context pack is grouped by the coaching question, not the milestone that built it

The coach context pack grew one milestone at a time. Every capability — training load (#276), volume-vs-norm (#400), recent-training (#444), training-history (#561), intensity (#578), calibration (M9), perceived effort (M6), longitudinal contrast (M4) — arrived as its own top-level pack section, named after the code that produced it and shaped like the row it came from. The result is ~20 flat sections whose names encode the implementation, not the question each answers, handed to the model as one minified `json.dumps` blob.

Two structural failures follow from that history, and both degrade the read.

**The pack has two overlap swamps, each on a single axis.** "How is the runner right now" is answered four times — `training_load`, `training_volume`, `recent_training`, and `intensity.distribution` — each in its own units, with a rolling-7d window sitting next to a partial calendar week (the root of #653; #670 carves this one). Separately, "how hard was this run, really" is answered four times too — the HR-derived `effort` axis in `metrics`, `perceived_effort` (RPE vs HR), `calibration.hr_drift` (this drift vs the runner's typical), and `intensity.this_session` — so the coach assembles the intensity picture from four places. Sibling sections on the same axis invite the model to narrate each in isolation; the #636 "recovery collapsed to 6 bpm" misread was exactly that failure, in raw units.

**The envelope forces navigation by implementation name.** Because the top level is flat and the keys are milestone names (`training_load`, `discount_signals`, `recent_training_summary`), the model reads the pack by the shape of our codebase rather than by the shape of a coach's reasoning. A good running coach — a role the model already knows deeply — runs a finished run and this runner through a small, stable set of questions. The pack should present itself the same way, so the model's latent coaching knowledge navigates by intent.

This is an altitude problem (per `aiw-prompt-smith`): most of the pack sits at the altitude of the milestone that built it, when it should sit at the altitude of the coaching question it serves.

## Decision

Reorganize the context pack into **five groups named for the coaching question they answer**, plus the safety floor. The sections do not change what they mean; they move under the question they serve, and the two overlap swamps collapse into one read each.

```
this_run       — what was this session, and how hard was it really?
                 activity · metrics · stream_view · check_in · intensity_read · referral · block
right_now      — how is the runner currently?
                 readiness · recent_weeks
the_runner     — who are they, and where are they going?
                 profile · memory · training_history · typical_trend
our_thread     — what did I say last time, and did it land?
                 last_reports · adherence · continuity
how_to_coach   — delivery and philosophy, NEVER facts
                 corpus · stance          (voice stays in the system prompt)
safety_rules   — the floor, stays top-level
```

Four properties are load-bearing:

1. **Grouped by the question, so the model reads by intent.** The five group keys are the leading words — `this_run`, `right_now`, `the_runner`, `our_thread`, `how_to_coach` — and every future section has an obvious home. Two sections that straddled two questions are dissolved into the groups they actually belong to: `longitudinal` splits (its `prior_reports` are *what I said last time* → `our_thread.last_reports`; its `baseline_trend` is *the runner's trajectory* → `the_runner.typical_trend`), and `calibration` splits (its `hr_drift` comparison is *this run's drift* → `this_run.intensity_read`; its `referral` is a this-run safety nudge → `this_run.referral`, promoted to its own key because burying a safety surface invites misses). `intensity` naturally separates for the same reason — `this_session` is about this run, `distribution`/`trend` is about right now.

2. **The two overlap swamps become one read each.** `this_run.intensity_read` is the single "how hard, really" read, merging `perceived_effort` + `calibration.hr_drift` + `intensity.this_session` + `discount_signals` (HR band + within-run split + RPE-vs-HR gap + drift-vs-your-typical + confounders). `right_now.recent_weeks` is the single "recent training" read, merging `training_volume` + `recent_training` + `intensity.distribution` on one week model. The coach stops assembling a picture from four siblings.

3. **Shape moves before content.** The regroup ships FIRST as a **content-preserving** slice: the leaf facts are byte-identical, only the nesting changes, so it is testable by asserting the flattened fact set is unchanged. The swamp merges and the day-resolved `recent_weeks` week model (#670) follow as separate content slices *inside* the stable envelope. Separating the mechanical shape migration from the content redesign de-risks both and keeps each slice small and reversible.

4. **Coach-native leaves, but the JSON stays and the verdict stays the coach's.** Within the envelope, leaf framing follows the #637 template — coach-native units and reference frames and trends (pace not m/s, % of max not raw bpm, vs-your-norm not isolated points), because the recovery-6bpm bug was a *units-and-frame* failure. It is NOT a licence to pre-compute the coaching conclusion: a good-coach model reads splits and drift on its own (`aiw-prompt-smith`: trust the model for judgment, constrain it for interfaces). We fix the interface — units, frames, trends, and the envelope — and leave the call to the coach.

The pack stays a JSON object (byte-stable, cacheable, read by the deterministic validator and the eval harness). The `PACK_SECTIONS` byte-stable-drop registry extends to drop empty nested groups, and every path consumer — validator evidence paths (`corpus.*` → `how_to_coach.corpus.*`), eval extractors, the chat read path's strict re-parse, and the `flow-nodes.js` diagram — moves in lockstep with a new prompt version that reads the new paths. The regroup changes the pack fingerprint, so reports regenerate, exactly as every prior prompt-version pack change has.

## Considered options

- **Reshape sections in place, keep the flat top level (Option A).** Rejected: merging the two swamps in place fixes the worst reads but leaves the map — the model keeps navigating by milestone name, and every future section still lands as another flat sibling with no home. The envelope *is* the same altitude fix as the leaves; doing only the leaves pays the migration cost and still leaves the higher-altitude problem.
- **Compose a prose briefing instead of a JSON pack.** Rejected: prose reads most naturally, but it trades away the properties the pack exists to keep — byte-stable fingerprint caching, deterministic-validator readability, and the strict re-parse the chat path and eval harness depend on. The altitude fix is achievable *within* JSON (grouped envelope + coach-native leaves); we do not need to leave the format to get it.
- **Pre-compute the coaching verdict into each leaf.** Rejected: the recovery-6bpm misread was a units-and-frame failure, not a missing-conclusion failure. Pre-chewing the verdict starves the model's own coaching judgment — the whole reason we ground the coach as *a good running coach*. Give coach-native units, frames, and trends; leave the read to the coach, backed by the deterministic floor.
- **Land the envelope and #670's content in one migration.** Rejected: bundling the mechanical shape move with the `recent_weeks` content redesign makes one large, hard-to-verify change. Shipping the content-preserving regroup first (flattened fact set unchanged) gives a clean, independently-testable migration, after which the content slices are small moves within a stable shape.

## Consequences

- **Slicing, under the #638 coach-value-per-token umbrella:**
  1. **Slice 1 — the content-preserving regroup.** This ADR + the five group models in `coach_context.py`, `PACK_SECTIONS` extended to drop empty nested groups, builders emit the grouped shape, and every path consumer (validator, eval, chat strict-parse, diagram) plus a new prompt version updated in lockstep. Leaf facts byte-identical.
  2. **Slice 2 — `right_now` content** (#670, rescoped): the day-resolved `recent_weeks` and the single configurable week model, inside the envelope.
  3. **Slice 3 — `this_run.intensity_read` merge**: the four intensity lenses collapsed into one read.
  4. **Slice 4 — coach-frame the `metrics` leaves**: % of max not raw bpm, pace not m/s throughout, drop over-precise decimals.
  5. **Slice 5 — cleanups + flip**: `salience` drops from the fuller pack (it is a routing signal for opener depth and the safety force, consumed before the fuller turn is scheduled, so it steers nothing in the fuller pack); final prompt tune; eval; owner flip.
- **Issue structure:** #638 stays the audit umbrella; #670 is rescoped from "rename the three recent sections" to Slice 2 (its rename is subsumed by the envelope); Slice 1 and Slice 3 are their own issues.
- **The safety floor is invariant across the regroup.** Slice 1 changes only nesting, not facts, so the medical-scope validator (rule 5) and the eval `*_preserved_safety_surface` assertions hold by construction; the validator's evidence-path rules (corpus / user-materials-is-not-evidence) are re-anchored to the new paths in the same slice. `referral` is promoted, not weakened.
- **Every pack-shape slice keeps the existing disciplines:** byte-stable-drop for gated/empty sections (now including nested groups), a regenerated `flow-nodes.js` diagram in the same PR, and the eval preserved-safety-surface run.
- Future reviews should not re-suggest a flat milestone-named section, nor a section that answers a question another group already owns: a new capability joins the group whose question it serves, or it earns a new question. The five-question envelope is the design, not an accident of build order.
