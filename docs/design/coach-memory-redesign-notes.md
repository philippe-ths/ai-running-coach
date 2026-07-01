# Coach memory redesign — working notes (IN PROGRESS)

Status: design exploration, paused 2026-06-29. Not yet an ADR (decisions still in flight).
Driver: the runner (Philippe) sketched a memory redesign; we are pressure-testing it before any build.
Related: ADR 0008 (current four-layer memory model), `docs/audit/knowledge-base-alignment-2026-06-28.md` (the KB audit that started this), `CONTEXT.md` (`Stated memory`, `Goals and plans` terms added during this discussion).

---

## ⛔ HARD CONSTRAINT — read before designing anything (owner directive, 2026-06-29)

The existing **CoachNarrative** and **CoachingContext belief** implementations are **RETIRED**. They will **never** be re-enabled in their current form. This is a **clean redesign that replaces them**, not a fix-and-reflip of the disabled code.

- Do **NOT** base the new memory/narrative architecture on the existing belief-store, narrative, or consolidation code (`belief_store.py`, `beliefs.py`, `narrative_store.py`, `consolidation.py`, the `coaching_context` / `coach_narrative` tables). Read them only to understand the incident, never as a template.
- The kill switches being "off" does **not** mean "flip back on once M7 is fixed." The stores and their write-back/consolidation design are being thrown out and replaced.
- ADR 0008's four-layer model is the *context that is being superseded for the durable-memory half*, not a spec to extend. The eventual ADR for this redesign should supersede it.
- The deterministic DATA layer (RunnerBaseline, training load, calibration) is fine and stays — it is NOT "beliefs/narrative" and is out of scope of this retirement.

---

## Where we left off (read this first to resume)

We reframed away from "design a database" toward "what does a good coaching relationship need," and that produced a clean scoping cut. The pending question on the table when we paused:

> After subtracting everything the deterministic data already does, does the memory job reduce to **three things** — (1) remember what you *said* that the numbers can't show, (2) keep it current, (3) hold a short condensing story — and is the runner happy that "the numbers already do that" covers *normal / blip-vs-trend / condition*?

Next step after that confirms: show how those three things work as **one simple thing** (the "notebook" framing, below), in plain language, not as tiers/clocks/regimes (those over-complicated it and lost the runner).

---

## Why this exists

- Durable memory (beliefs + narrative) is currently **OFF in production**: `COACH_BELIEFS_ENABLED` and `COACH_NARRATIVE_ENABLED` default `False` (`config.py`), disabled after an incident.
- **The incident:** the M7 adherence classifier's `easy_discipline` keyword list swallows "recovery day / recovery run / recovery activity" phrasing, so **rest-day advice ("take a rest day") was judged as easy-discipline against the next logged run** → a normal next run read as "ignored" → poisoned an "ignores easy guidance" belief → the narrative replayed that fixation into every report. Root cause = theme conflation (rest ≠ keep-easy), and rest advice has no fair comparable run. File: `backend/app/services/coach/adherence.py`.
- The existing belief + narrative implementation is **retired and being replaced** (see HARD CONSTRAINT above), not patched and re-enabled. The incident is *why the current form is rejected outright* — the KB's "confident stale memory" / Group-A provenance failure, lived. Per the prod post-mortem the echo came from **four** prior-report-driven loops (M8 beliefs, A2c narrative, M4 longitudinal prior-reports, M7 adherence), all now off by default; the redesign replaces that whole machinery, not one keyword list.

## The reframe that worked: what a good coaching relationship needs (memory-wise)

Forget LLMs. A good ongoing relationship: remembers what matters and forgets the noise; holds onto what *you said* (and that you said it); keeps up to date (doesn't freeze you in the past); picks up where you left off; knows what it *knows* vs what it's *guessing*; takes a correction; says the relevant thing, not everything it remembers.

A good coach adds: hold the destination (goal); know your normal; respect your limits; remember the last advice and whether it worked; tell a blip from a trend; hold the long arc.

The runner's original sketch (chronological-from-reports+conversations / older=more-condensed / importance-to-what-the-user-said / archival cutoff / Goals-and-Plans-with-dates) maps almost 1:1 onto this list. The sketch *was* a correct description of coaching memory.

## The scoping cut (the key insight)

Run the needs list through "is this already in the deterministic data?":

- **Already handled by the data — do NOT rebuild:** know your normal (baselines), blip-vs-trend (the system already abstains on one data point), current condition / long-arc fitness (training load).
- **The advice→outcome loop (a real need, NOT surviving code):** "remember what they told you and whether it worked" is a genuine need, but the existing M7 + belief-store *implementation* of it is inside the retired zone — redesign the loop from scratch, do not re-enable it.
- **What only memory can do (the actual job):**
  1. **Remember what you *said*** that the numbers can't show (the knee, the metronome at 166, "no quality in 35°C heat", a goal). Exists only because it was said.
  2. **Keep it current** (update, take corrections, don't freeze).
  3. **Carry a short story** that condenses with age, so the coach picks up where it left off.

Everything over-built earlier (function-tags, clocks, "two regimes", "decoupling") was machinery around those three. Keep the three; reintroduce machinery only if a real failure demands it.

## Decisions so far

LOCKED:
- **D1.** User-stated content is its own **grounded** durable tier, not folded into the voice-only narrative. It is citable and can drive advice, but per `Authority tiering` yields to measured `DerivedMetric` on a factual conflict (a stated fact can be wrong/fabricated). The glossary's `Authority tiering` already reserved a "user-asserted facts" slot; this fills it. (Q1 = option b.)
- **D2.** Tag stated records by **function** (how the coach uses it), not by topic: **Constraint** (bounds/gates advice), **Goal or plan** (forward-dated), **stated Preference** (frames choices), **Open thread** (pending question). Topic taxonomies (shoes/sleep/nutrition) explode on a talkative user; function does not. (Reinforced by RUT-Bench + the KB's procedural-memory "store by function not surface".)
- **D3.** Conflict handling = **option (c)**: a *time-ordered update* silently supersedes the old; *two things asserted true at once* surface as one focused question. Directly patches Claude's documented RUT-Bench weakness on contradictory constraints.

PROPOSED (leaning accepted, not locked):
- **D4.** Extraction trust: **confirmation scales with stakes** — a safety-relevant Constraint gets a visible read-back, a Goal gets a light confirm, a stated Preference auto-extracts silently, an Open thread is just surfaced. Plus the house rule **nothing hardens on a single signal** (mirrors adherence `_MIN_OPPORTUNITY_RUNS`, belief `observation_count >= 2`, calibration `MIN_SAMPLES`). Plus **read-backs are batched/folded** into the next report, never per-item pings.
- **D5.** Goals and plans are **conversation-sourced, never inferred** from data. (Validated by the real data: no race/goal anywhere in 477 activities.)
- **D6 (the one guardrail).** A free-form "notebook" is fine if its **fact lines are re-checked against the data every rewrite** while its **story lines stay free**. This is the single rule that prevents the incident (a written line hardening into believed fact), instead of a whole tier system.

OPEN / PARKED:
- The condensation ladder granularity (how far back stays block-level vs weekly vs monthly). The runner is high-frequency (~16 sessions/wk ≈ 800 blocks/yr), so the timeline needs *some* ladder, but the conversational memory is sparse and precious and should condense gently. Granularity unresolved.
- The "conversation ages on the ladder but extracted facts live on their own clock" framing did **not** land ("I'm not seeing it") — parked, do not lead with it.
- The "notebook as one page" framing was the last concrete artifact shown; the runner had not yet reacted to it before reframing to first principles.

## Inputs used

### Real data (local seed snapshot — prod may hold more)
- 477 activities, 74 coach reports (all non-fallback), 36 chat messages (18 each way), 30 check-ins (only **2** with notes).
- The 2 notes (the entire stated-injury substrate): `"Felt good, easy. Right knee small pain."` (rpe 4); `"Light tightness in left leg, eased up as I walked around."` (rpe 1).
- Chat themes: a 35-36°C heat wave (coach flips the week to easy; reads RPE-easy/HR-high as the heat confound); a cadence experiment (metronome set 166, averaged 161 spm, "easier to find the rhythm"); scheduling a quality session around the heat.
- Activity profile: high-frequency, multi-modal — Walk ~40% / Run ~25% / Ride ~15% / Row ~8% / Weights ~8%; ~16 sessions/wk; runs are daily ~3.5km easy + ~weekly 7-8km harder (relative effort 85-99); **no race, no long run >10km, no stated goal**.
- Persona: a **fourth** persona beyond the veteran / newcomer / talker we war-gamed — a *high-frequency, multi-modal, steady-state* athlete whose volume is frequency-driven. Memory must (a) treat the **Block** as the event unit or the log doubles, (b) condense on **frequency** not just age, (c) allow **modality-scoped** constraints (a knee gates runs, not rows).
- Substrate finding: conversations are **sparse but precious** (a niggle mentioned once, never recoverable from data), so extraction must be **eager and recall-biased** — there is no second mention to catch a miss.

### KB pages this leaned on (Obsidian wiki)
- `concept-agent-memory` — four memory types; staleness in high-confidence memory is where systems fail; **actor attribution** (tag user-stated vs agent-inferred).
- `concept-silent-failure-mechanisms` / `concept-trusting-the-surface` — Group-A provenance failures; the incident is "confident stale memory" realised.
- `src-sleep-time-compute` — background consolidation is right, but can bake a stale conclusion into confident memory (the trap the consolidation job already avoids by re-grounding from facts).
- `src-confident-wrong-document` — relevance ≠ reliability; **surface disagreement rather than silently pick a side** (motivates D3).
- `src-when-user-is-messy` (RUT-Bench) — six messy-user types; under-specification is the killer (and memory is its antidote); **Claude is specifically fragile on contradictory constraints**; cure is scaffolding not a smarter model.
- `concept-procedural-memory` — store by **function**, not surface (motivates D2).

### RUT-Bench → memory-mechanism mapping (the stress-test matrix)
| Messy user | What it does to memory | Mechanism needed |
|---|---|---|
| Self-contradicting | two constraints conflict | supersede + **surface** (Claude's weak spot) |
| Goal-switching | a goal is abandoned | goal *retirement*, not just date-expiry |
| Detail-burying | a safety fact buried in noise | recall-biased extraction; Constraint = pull-always |
| Under-specifying | "what should I do next?" | **memory fills the gap** (its best case) |
| Impatient/hostile | venting | do not promote venting to a durable belief |
| Fabricating | a stated fact is false | user-asserted **yields to measured data** |

## Glossary terms added during this discussion (in `CONTEXT.md`)
- **Stated memory** — the third durable-memory layer: grounded record of what the runner explicitly told the coach, function-tagged, provenance + clock, supersede-or-surface on conflict.
- **Goals and plans** — the forward-looking kind of Stated memory; conversation-sourced, dated, retired/superseded/expired, never inferred.
- Flagged collision: **stated Preference** (the runner said it) vs the existing **Preference profile** (derived from behaviour). Kept distinct.
