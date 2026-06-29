# Coach Memory — build plan (defined blocks)

Execution plan for the runner-memory redesign described in
`coach-memory-implementation-brief.md`. This document is the contract a single
implementing agent (Claude Code, one-shot) executes from. It is code-aware:
every milestone names the real files, seams, and house patterns to follow.

The brief is the **what** and the **rules**. This plan is the **order**, the
**blocks**, and the **execution discipline**. Where the two ever conflict, the
brief wins on intent and this plan wins on sequencing.

---

## 0. Ground truth captured at planning time

Verified against the codebase (three exploration passes, 2026-06-29):

- **Alembic head:** `f8bddfa076e7` (merge commit). New migrations branch from
  here; re-check the head at build time and after any rebase (a migration-bearing
  branch can fork into two heads — `make backend-test` uses `create_all` and is
  blind to it, but the deploy runs `alembic upgrade head`).
- **Next free ADR number:** `0025`.
- **Live prod prompt:** `coach_message_v12` (#561 training-history, flipped
  2026-06-28). The new memory prompt is therefore `coach_message_v13`, built as
  `v13 = v12 + memory addendum`. **Verify the actual latest prompt id at build
  time** (`prompts.py` / `prompt_features.py`) and build `vN+1` on whatever is
  truly the head — `project-context.md` lags reality and must not be trusted for
  the version number.
- **Pack-section recipe (4 files):** `schemas/coach_context.py` (model field +
  `PackSection` descriptor in `PACK_SECTIONS`), `services/coach/context.py`
  (builder + `ReadTimeSignal` descriptor + `gather()` call), `prompt_features.py`
  (`PromptFeature` enum + `PROMPT_FEATURES` row), `prompts.py` (addendum constant
  + `SYSTEM_PROMPT_MESSAGE_V13[_OPENER]` + registry + `is_*_prompt` predicate).
  The import-time `_assert_descriptors_match_fields()` guard fails loudly on a
  mismatch.
- **Memory is a stored-artifact read.** Unlike the read-time history scans
  (calibration, volume, readiness), memory is *written* by a background pass and
  *read* by the coach. The read still fits the `ReadTimeSignal` seam (#492) as a
  DB read `(db, activity, as_of) -> section | None` — exactly the stored-artifact
  adapter the seam was designed to accept.
- **Retirement coupling risk:** `preference.py` (M10) consumes
  `adherence_pattern` beliefs from the belief store. Retiring beliefs strands it.
  **M10 is retired outright and its function is dropped, not replaced** (grill G4):
  its job was derived behavioral reranking ("lead with what you act on, reframe
  what you ignore"), the exact verdict-driven, nag-adjacent behavior being
  eliminated. §4 "What works for you" holds *stated* preferences (a different,
  citable tier) and does NOT do M10's reranking. M7 `adherence.py` is independent
  of the store and stays (its consumption governed by the G2/G3 non-nag discipline).
- **Stored-pack parse risk:** the pack uses `extra="forbid"` and historical
  stored packs are strict-parsed by the eval harness and the chat read path.
  Removing a field outright can break old-pack parsing. Retired sections must be
  kept as **Optional deprecated schema stubs** (never populated, never serialized)
  rather than deleted from the model — the same idiom used for
  `recent_training_summary` after #451.

### Proposed names (agent may refine; keep consistent once chosen)

| Thing | Name |
|---|---|
| Model / table | `RunnerMemory` / `runner_memory` (one row per user) |
| Profile schema | `RunnerMemoryProfile` (five capped sections) |
| Store module | `services/coach/memory_store.py` |
| Update pass | `services/coach/memory_update.py` |
| Background job | `app/jobs/memory_update.py` → `update_memory_job(user_id)` |
| Pack section | `memory` |
| Prompt feature | `PromptFeature.MEMORY` |
| Prompt | `coach_message_v13` |
| Predicate | `is_memory_prompt` |
| Kill switch | `COACH_MEMORY_ENABLED` (default `True` per #522 convention) |
| ADR | `docs/adr/0025-runner-memory-is-a-rewritten-profile.md` |

---

## 1. Resolved open questions (brief §12)

These were the open questions; here is the position the plan builds to. The agent
confirms them with the human at plan-review, then builds.

- **Storage format.** A structured JSON document of five named sections, each a
  capped list of short plain-language lines, strict-coerced through
  `RunnerMemoryProfile` (`extra="forbid"`, per-section length/count caps). It is
  human-readable, diffable, and machine-validatable, and renders to markdown for
  surfacing. Not opaque records, not free prose.
- **Update-pass idempotency.** Idempotency is **structural, not byte-exact** (an
  LLM pass can't be byte-stable). The bar: re-running the pass on unchanged
  sources produces no section-membership drift, no monotonic growth (caps hold),
  and no new fact absent from the sources. Pinned by a re-run test asserting the
  invariants, not string equality.
- **Cold start.** A brand-new runner gets the five headings, all near-empty. The
  first few cycles populate **Lately** only; nothing graduates to sections 1–4
  until it clears the graduation threshold. The writer never errors on empty
  sources; it writes (or leaves) an empty-but-valid profile.
- **Graduation threshold.** A line earns a permanent section only when it is
  supported by **≥2 distinct source exchanges** at write time. Crucially this is
  **re-derived from the raw sources every pass** — there is no stored counter that
  can drift or poison (the structural fix for the incident). The threshold is a
  property of the current source set, not of memory's own history.
- **Test strategy.** The messy-user replay corpus (self-contradiction, goal
  switching, a safety fact buried in noise, venting, a fabricated fact) plus the
  original rest-day incident, run against the writer as the milestone-2 oracle.
  This is the design's proof, not an afterthought.

---

## 1a. Resolved during grilling (2026-06-29)

- **G1 — Memory holds no LLM behavioral verdicts.** Memory is the citable
  `Stated memory` tier rendered as a profile: what the runner *told* the coach
  plus soft, **non-gating** character. §4 "What works for you" holds *stated*
  preferences, cues, and tone learnings — never an inferred behavioral verdict
  ("ignores easy days"). Verdicts are exactly what fixated and nagged in the
  incident. This makes the anti-echo guarantee structural: memory cannot *contain*
  an inferred behavioral conclusion, so the incident's content cannot regenerate.
  The runner's ADHD medication lands as a stated §2 health flag (context) **and**
  operationally as the existing `stimulant_use` `discount_signal` (down-weights
  the HR read) — not as a memory verdict.
- **G2 — The directional "easy-running" read stays deterministic.** A real coach
  *should* track whether the runner is trending toward more easy running — but as
  *direction and proportion* (easy/moderate/hard share, time-in-zone vs recent
  comparable runs), re-derived every run, confounder-adjusted, never a binary
  acted/ignored verdict and never nagged. This lives in the deterministic data
  layer, not memory. **In scope for M3:** a v13 prompt discipline — "read training
  direction from `time_in_zones` + `recent_training` + `discount_signals`; speak
  it as confounder-adjusted direction; never nag, never a binary verdict" — plus a
  matching eval assertion (`coached_direction_not_nagged` or similar). **Follow-up
  issue (out of scope):** a dedicated deterministic intensity-distribution-and-trend
  signal (easy/mod/hard share this run vs recent comparable runs); `baseline_trend`
  today tracks EF and HR-drift, not the zone distribution itself.

- **G3 — Memory is verdict-free at every timescale.** §5 "Lately" stores
  *thread-state* — the open thread ("last time we agreed you'd try a metronome on
  easy runs — still open") and the open question waiting on the runner — **not**
  outcomes. "Whether the last advice worked" is re-derived at exchange time
  (deterministic: M7's outcome and/or the G2 directional read), confounder-adjusted,
  spoken fresh under the non-nag discipline, and **never written into memory as a
  stored verdict**. M7 `adherence.py` stays but its *consumption* is governed by
  the v13 discipline (treat a rest day or a fired `discount_signal` as exculpatory,
  never nag). The specific M7 rest-day → `ignored` misread is filed as a **separate
  classifier issue** (the brief walls the classifier off), not fixed in this plan.

- **G4 — M10 preference is dropped, not replaced.** Its job was derived behavioral
  reranking of next_steps by inferred preference — the verdict-driven, nag-adjacent
  behavior being eliminated. No replacement in this plan (not by memory, not by a
  new deterministic signal). The coach keeps M7's raw adherence outcome (non-nag
  discipline) for the legitimate follow-through signal; it loses auto-reranking. If
  preference-conditioned framing is ever wanted back, it is a deliberate separate
  feature. The CONTEXT.md `Preference profile` entry is retired alongside.

- **G5 — Memory is citable; no "memory-is-not-evidence" validator rule.** Memory
  is the `Stated memory` tier, which is grounded and citable (the coach may say
  "you mentioned your knee") — unlike the non-grounding narrative/corpus/materials.
  Its authority tiering (yields to measured `DerivedMetric` on factual conflict,
  never lowers the safety floor) is **prompt-enforced** via the v13 addendum, the
  house precedent for every authority-tier rule. The deterministic backstops are
  the **existing medical-scope floor (validator rule 5)** plus the eval
  `memory_preserved_safety_surface` assertion. M3 adds NO memory not-evidence rule.

- **G6 — Writer activation gates on the memory-aware prompt.** The update-pass
  enqueue fires only when `is_memory_prompt(COACH_PROMPT_ID) and
  COACH_MEMORY_ENABLED`. So M1–M4 merge with zero prod change (prod is v12); the
  single v13 cutover flip turns on writer + reader together; rollback is the same
  one flip. `COACH_MEMORY_ENABLED` stays the conventional default-True kill switch.

- **G7 — M2's bar is two-tier.** Structural anti-incident invariants (anti-echo
  input construction, graduate-or-drop, caps, cold-start, idempotency invariants,
  enqueue gating) are deterministic and **CI-gated**. Judgment-quality replay cases
  (supersede / surface-question / no-fixation / hold-but-not-harden) are **eval-
  validated** as a tracked real-LLM scorecard, not a flaky merge gate. Anti-echo is
  CI-gated because it is structural; messy-input behaviour is eval-measured because
  it is LLM judgment.

- **G8 — The writer's gather is section-shaped retrieval over the raw store**, not
  a since-last-update window. Durable sparse facts (§1–§3) are re-retrieved from the
  full raw history each pass; dense §5 Lately uses a bounded recent window; the
  deterministic layer supplies numeric character. Reconciles rewrite-from-source +
  the persistence contract + ten-year scale without ever reading the prior profile
  (anti-echo) or loading the raw store wholesale. **This supersedes the brief's §7
  "over the source material since the last update" wording** — the plan is
  authoritative on the gather mechanics.

## 2. Milestone map (defined blocks)

```
M1 Foundation ──► M2 Update pass ──► M3 Coach consumption ──► M5 Validation
  (contract,        (the writer;        (pack + v13 +            & cutover
   storage, ADR)     replay corpus)      validator + eval)        readiness
                                              │
                          M4 Retirement ◄─────┘
                       (delete old durable
                        memory; human-merge
                          gated, destructive)
```

Build order: **M1 → M2 → M3 → M4 → M5.** New system is proven and inert before
the old one is torn out, so there is never a capability gap. Each milestone is one
GitHub issue and one branch/PR.

---

## M1 — Foundation: decision, contract, storage

**Description.** Land the architecture decision (ADR 0025) and the inert
substrate: the `RunnerMemoryProfile` five-section schema, the `runner_memory`
table + model + migration, and the `memory_store.py` get/upsert layer. Nothing
reads or writes a real profile yet.

**Intent.** Freeze the contract every later block depends on, in isolation, so the
risky writer (M2) and the prompt change (M3) build on a settled, tested base. A
reviewer can approve the data model and the ADR without judging LLM behaviour.

**Scope.**
- ADR `0025`, superseding the durable-memory half of ADR 0008. State the
  rewrite-from-source anti-echo rule as the load-bearing decision and reference
  the incident (brief §3).
- `RunnerMemoryProfile` Pydantic model: five sections (Who you are / Limits &
  constraints / Goals & plans / What works for you / Lately), each a capped list
  of short lines, `extra="forbid"`, per-section count + per-line length caps.
- `RunnerMemory` model (`runner_memory`, one row per user, `user_id` unique +
  indexed, profile JSON, provenance: `model_id`, `source_report_count`,
  `grounded_through`, timestamps). Mirror `coach_narrative.py` shape.
- Alembic migration off head `f8bddfa076e7`; barrel `models/__init__.py`;
  `account_deletion.py` `_USER_OWNED` entry.
- `memory_store.py`: `get_memory(db, user_id)`, `upsert_memory(...)`, and a pure
  `render_profile_markdown(profile)` for surfacing/debug. No LLM here.

**Out of scope.** The update pass / writer (M2). Any coach read (M3). Any deletion
of old code (M4). Frontend.

**Linking.** Foundation for M2 (writer targets this store), M3 (reader projects
this schema), M5 (validation rebuilds these rows). Independent of M4.

**TDD.**
- Red: schema rejects over-cap sections / over-long lines / unknown keys; accepts
  a valid five-section profile and an all-empty cold-start profile.
- Red: store round-trips a profile; `upsert` replaces in place (unique-per-user);
  `get` returns `None` for an unknown user.
- Red: `render_profile_markdown` emits all five headings even when empty.
- Migration applies and downgrades cleanly against a scratch DB; single alembic
  head after it lands.

**Success criteria.**
- `make backend-test` green; new unit tests cover schema caps + store round-trip.
- `alembic upgrade head` / `downgrade` clean; `alembic heads` shows one head.
- ADR 0025 merged-ready, explicitly superseding ADR 0008's durable-memory half.
- Zero behaviour change to the live coach (nothing reads/writes the table yet).

---

## M2 — The update pass (the writer)

**Description.** Build `memory_update.py`: the rewrite-from-source pass that
gathers the runner's source material (prior coach reports, conversations,
check-in notes) **plus** the deterministic data layer, and produces the whole
five-section profile from scratch each run, writing it via `memory_store`. Wire it
as a background job enqueued after each non-fallback report, behind
`COACH_MEMORY_ENABLED`.

**Intent.** This is the heart of the design and the only place the incident can
recur, so it is built test-first against the messy-user replay corpus. The
structural guarantee — the writer is **never handed memory's own prior text** —
lives and is enforced here.

**Scope.**
- Source gather — **section-shaped retrieval over the raw store, NOT a naive
  since-last-update window (G8).** For the durable, sparse sections (§1 character,
  §2 limits, §3 goals) retrieve stated constraints/goals across the **full** raw
  history (check-in notes + chat are short, sparse, filterable — cheap even at ten
  years); for the dense §5 Lately use a **bounded recent window** (reuse
  `retrieval.py` seams — `fetch_recent_user_digests` and peers); the deterministic
  data layer (RunnerBaseline, training load) supplies long-horizon numeric
  character. This keeps the writer re-grounded **from sources** (never from its
  prior profile — anti-echo intact) while durable facts survive because they are
  re-retrieved every pass (the "raw store is truth, retrieve on demand" principle),
  never loading the raw store wholesale. A durable fact ages out only when a later
  source supersedes/retires it (D3), never because it scrolled out of a window.
- The pass (one Haiku call, hardcoded `claude-haiku-4-5` per the auxiliary-path
  convention; record token spend on the per-user budget via the `*_with_usage`
  sibling, #472): produce a `RunnerMemoryProfile`, strict-coerced. Apply: caps,
  graduate-or-drop (≥2-distinct-source threshold, re-derived from sources),
  supersede-on-newer, surface-as-question on simultaneous conflict, safety-hold
  (a safety-relevant limit is held on first mention, hardens to gating only on
  confirmation).
- Fact re-check: user-stated factual lines (limits, goals) yield to measured data
  on conflict (brief §7 rule 4).
- Background job `update_memory_job(user_id)` + decoupled enqueue in
  `service._fire_learning_loop` after a non-fallback report (fire-and-forget,
  idempotent single-row rewrite, no sentinel — the `enqueue_consolidation`
  pattern). **Activation gates on the active prompt being memory-aware**:
  `is_memory_prompt(settings.COACH_PROMPT_ID) and settings.COACH_MEMORY_ENABLED`
  (G6). While prod runs v12 the writer is inert — merging M2 changes nothing in
  prod; the single v13 cutover flip turns on writer + reader together.
  `COACH_MEMORY_ENABLED` (default True, #522 convention) is the independent kill
  switch on top.
- Cold-start behaviour (empty sources → valid near-empty profile).

**Out of scope.** The coach reading the profile (M3). The prompt change (M3).
Removing beliefs/narrative (M4). The writer must not import the retired modules.

**Linking.** Consumes M1's store/schema. Produces the rows M3 reads and M5
validates. Must land before M3 is useful, but M3's pack read degrades safely to
"no memory yet" if a profile is absent.

**TDD — two tiers (G7).** The replay corpus splits by what can actually be
guaranteed. **CI-gated (deterministic, stubbed LLM, must be green to merge)** — the
structural anti-incident invariants: anti-echo input construction, caps, cold-start,
enqueue gating, idempotency invariants. **Eval-validated (real-LLM,
integration-marked and/or folded into the eval harness as a tracked scorecard, NOT
a flaky merge gate)** — the judgment-quality replay cases (does the LLM actually
supersede / surface-a-question / not-fixate / hold-but-not-harden). The anti-echo
guarantee lives on the CI side because it is structural; "behaves well on messy
input" lives on the eval side because it is LLM judgment.

*CI-gated structural cases:*
- **Anti-echo (the critical one):** a test that fails if the gather ever includes
  memory's own prior profile text as an input. Assert by construction — the input
  bundle the writer receives contains only sources + data, never the stored
  profile.
- **Graduate-or-drop (structural half of the incident):** a line supported by one
  source stays out of sections 1–4; the ≥2-distinct-source threshold is computed
  from the source set, with no stored counter. Asserted on the deterministic
  apply-logic, stub LLM.
- **Idempotency invariants:** re-run on identical sources → no section-membership
  drift, no growth past caps, no key the sources do not contain.
- **Caps:** generated profile is clamped so it fits one screen regardless of
  history size.
- **Cold start:** empty sources → valid empty profile, no error, no enqueue loop.
- **Durable-fact persistence (G8):** a limit/goal stated long ago and absent from
  the recent window still appears after a rewrite (the section-shaped gather
  re-retrieves it); it drops only when a later source retires/heals it. Asserted on
  the gather (the durable retrieval returns the old stated fact), stub LLM.

*Eval-validated judgment cases (real-LLM scorecard, not a merge gate):*
- **The original incident:** rest-day advice + normal run history → no "ignores
  easy guidance"-style fixation in the profile (its structural half — nothing
  hardens from a single signal — is also covered CI-side by graduate-or-drop).
- **Self-contradiction:** runner says X then not-X → newer supersedes, no stale
  line survives (D3).
- **Simultaneous conflict:** two live assertions both true → surfaces as one
  focused question, not a silent pick.
- **Goal switching:** old goal retired/expired, new active → only the new in §3.
- **Safety fact buried in noise:** a niggle mentioned once among venting → *held*
  (§2, not lost) but not yet hardened to a gating limit.
- **Fabricated / unsupported fact:** a one-off claim with no support stays out of
  sections 1–4 (drops; the raw source still holds it).

**Success criteria.**
- **CI-gated:** the structural invariants above are green in `make backend-test` —
  anti-echo (a `test_memory_writer_is_source_grounded`-style test proving the prior
  profile is never an input), graduate-or-drop, idempotency invariants, caps,
  cold-start, enqueue gating.
- **Eval-validated:** the judgment replay corpus is run as a tracked scorecard
  (real-LLM, integration-marked / eval harness); the incident + messy-user cases
  are reviewed as the evidence the design holds, and re-run on real data in M5. Not
  a flaky merge gate.
- Enqueue fires only under a memory-aware prompt (v13) **and**
  `COACH_MEMORY_ENABLED`; under v12 / flag off → no job, no write.
- Token spend recorded on the per-user budget counter.

---

## M3 — Coach consumption (pack + prompt v13 + guards)

**Description.** Surface the whole profile to the coach: a `memory`
`ReadTimeSignal` adapter that reads the stored profile, a `memory` pack section
(byte-stable drop), `coach_message_v13 = v12 + memory addendum` (carrying the
authority tiering + the G2/G3 non-nag directional discipline), eval rubric
assertions, and the regenerated flow diagram. Ships **inert in prod** (v13 not
flipped; gate default-on but prod stays on v12 until the owner flips).

**Intent.** Close the loop — the report the coach writes becomes a source for the
next update pass — while binding memory's authority by prompt (the addendum: memory
is citable but yields to measured data and never lowers the floor) with the
existing medical-scope validator (rule 5) and the eval as the deterministic
backstops, so a written line can never override re-derived data or the safety floor.

**Scope.**
- `_build_memory_context` builder + `_MEMORY_SIGNAL = ReadTimeSignal("memory",
  ...)` + registry + `gather(...)` in `build_context_pack`. Surfaces the **whole**
  profile — no retrieval/ranking/top-k (brief §8). Degrades to `None` when no
  profile exists (cold start) → section dropped byte-stably.
- Schema: `MemoryContext` field on `CoachContextPack` + `PackSection("memory",
  PromptFeature.MEMORY)`. Byte-stable for v≤12.
- `PromptFeature.MEMORY` + `PROMPT_FEATURES["coach_message_v13"]` = v12's set +
  `MEMORY`. `_MEMORY_ADDENDUM` carries the **authority tiering** (G5): memory is
  the runner's *stated* tier — **citable** ("you mentioned your knee"), but it
  **yields to this run's measured `DerivedMetric` on a factual conflict** and
  **never lowers the safety floor**; goals are stated intent, never data-inferred.
  It **also carries the G2/G3 non-nag directional discipline**: read training
  direction from `time_in_zones` + `recent_training` + `discount_signals`; speak it
  as confounder-adjusted direction (treat heat, hills, `stimulant_use`, or a rest
  day as exculpatory); never nag, never a binary acted/ignored verdict.
  `SYSTEM_PROMPT_MESSAGE_V13[_OPENER] = V12[_OPENER] + addendum`; registries;
  `is_memory_prompt`. Pin with a `test_message_prompt_v13` byte test (v13 == v12
  prefix + addendum).
- Validator: **no "memory-is-not-evidence" rule** (G5) — memory is citable, unlike
  narrative/corpus/materials. Authority tiering is prompt-enforced (the addendum);
  the deterministic backstop is the **existing medical-scope floor (rule 5)**,
  which polices memory-grounded prose like any other. Keep the `narrative.*` guard
  only until M4 deletes the narrative section.
- Eval: add rubric assertion(s) — `memory_preserved_safety_surface` (a fired
  referral nudge still relayed regardless of memory content) and
  `coached_direction_not_nagged` (when a `discount_signal` fired or the prior
  advice was easy-discipline, the report does not nag toward "easier" and renders
  no binary ignored-verdict). Update fixtures (good + deliberately-bad).
- **Regenerate `docs/diagrams/flow-nodes.js`** via
  `docs/diagrams/generate_flow_nodes_data.py` in this PR (mandatory on any
  pack/prompt change) and verify it renders.
- Add `memory` to the `/api/coach/feature-flags` map so a gated-off memory greys
  its (future) UI surface.

**Out of scope.** Flipping prod to v13 (owner action, M5 runbook). Retiring old
memory (M4). A user-facing profile viewer UI (spin-off issue).

**Linking.** Consumes M2's rows. Its v13 report becomes a source for M2's next
pass (the loop). Validator/eval guards here are the authority enforcement the
brief §7 demands. Independent of M4 ordering, but M4's stub-keeping rules must not
clobber the `memory` field.

**TDD.**
- Byte-stability: a v12 pack is byte-identical before/after this change (memory
  field absent/None dropped); pinned like the existing `*_byte_stable` tests.
- v13 pack carries `memory`; v13 prompt carries the addendum (prefix test).
- Validator does **not** block legitimate memory citation ("you mentioned your
  knee"); the medical-scope floor (rule 5) **still** fires on memory-grounded
  overreach. No memory-specific not-evidence rule exists (G5).
- Eval self-test green (`make eval-selftest`); new assertions pass on the good
  fixture and fail on the bad one (incl. `coached_direction_not_nagged`).
- Pack with no stored profile (cold start) → no `memory` key, no error.

**Success criteria.**
- `make backend-test` + `make eval-selftest` green.
- v12 byte-stability proven; v13 addendum + section proven.
- Flow diagram regenerated and committed in the same PR, renders clean.
- Prod behaviour unchanged (still v12) — this PR is inert until the owner flips.

---

## M4 — Retire the old durable-memory system

**Description.** Remove the retired belief + narrative + M10-preference
infrastructure: `beliefs.py`, `belief_store.py`, `narrative_store.py`,
`consolidation.py` (service + `jobs/consolidation.py`), `preference.py`, the
`CoachingContext` and `CoachNarrative` models/tables (drop migration),
`Activity.beliefs_written_at`, the `COACH_BELIEFS_ENABLED` /
`COACH_NARRATIVE_ENABLED` switches, the `_fire_learning_loop` belief/narrative
branches, the `believed_facts` / `preference_profile` / `narrative` reads, the
`enable_durable_memory` fixture, and the pinning tests. Repurpose validator rule 6
fully to `memory.*` and drop its `narrative.*` coverage with the section.

**Intent.** Clean replacement, not a parallel graveyard. The brief is explicit:
read the old code only to understand the incident, never extend or re-enable it.

**⚠️ Gates (ai-workflow ASK-FIRST).** This block deletes files/modules/classes and
changes DB schema (dropping tables) — both are ASK-FIRST and the table drop is
hard-to-reverse. The one-shot agent does **not** self-approve: it implements on a
branch and opens the PR with these flagged explicitly at the top of the PR body
and in the handoff. **Merging the PR is the human's approval.** The drop-table
migration is the destructive step; call it out by name. **Owner decision: drop
without export** — the rows are retired/superseded and the raw sources
(reports/chat/check-ins) still hold the truth, so no backup/dump step is needed.

**Scope.**
- Delete the modules/jobs/tests listed above; remove dangling imports and barrel
  entries; remove `account_deletion.py` entries for the dropped tables.
- **Keep `believed_facts` / `preference_profile` / `narrative` as Optional
  deprecated schema stubs on `CoachContextPack`** (never populated, never
  serialized) so historical stored packs still strict-parse under `extra="forbid"`
  in the eval harness and chat read path. Do **not** delete these fields. Add a
  test that an old stored pack containing these keys still parses.
- Drop-table migration off the then-current head; barrel + `__init__` cleanup.
- Remove the two kill switches and any UI/feature-flag references to them.

**Out of scope.** The new memory system (M1–M3) — untouched here. Any change to
M7 `adherence.py` (independent, stays) or the deterministic data layer (RunnerBaseline,
training load, calibration — explicitly out per brief §2).

**Linking.** Depends on M1–M3 having shipped the new memory system. M10 is
**dropped outright, not replaced** (G4) — §4 holds stated preferences, a different
tier, and does not do M10's behavioral reranking. Must not touch the `memory`
field M3 added. Last code block before validation.

**TDD.**
- Red→green: suite compiles and passes with the modules gone (no dangling refs);
  a grep-guard test or import check confirms the retired symbols are absent.
- Old-pack parse test: a stored pack JSON carrying `believed_facts` / `narrative`
  still loads (stub fields).
- `_fire_learning_loop` no longer enqueues consolidation or writes beliefs; the
  only post-report enqueue is `update_memory_job`.
- Drop migration applies; `downgrade` documented (recreates empty tables) or the
  irreversibility is stated explicitly.

**Success criteria.**
- `make backend-test` + `make eval-selftest` green with the old system gone.
- No import of a retired module anywhere (`grep` clean).
- Single alembic head; migration reviewed as the destructive step in the PR body.
- Coach output for v12/v13 unchanged by the removal (the switches were already
  default-off; this proves removal is behaviour-preserving for the live path).

---

## M5 — Validation & cutover readiness

**Description.** Prove the system on real data and hand the owner a clean cutover.
Rebuild eval inputs against a real-data seed, generate memory profiles for seeded
users, produce a B-vs-A (v12 vs v13) pack/report comparison, and write the
owner-flip runbook. End state: integration verified, issues + PRs open, nothing
flipped in prod.

**Intent.** Earn confidence by testing, not by assuming (brief §13). The only
remaining step after this is the owner's environment flip, which the agent cannot
and must not do.

**Scope.**
- Run the writer over a local prod seed (`make seed-local`, then a one-shot script
  invoking `update_memory_job` per seeded user) and eyeball the profiles against
  the four acceptance goals (reads like coach's notes / one screen / constant size
  / same sections everyone). Capture samples in a verification note.
- `make eval` before/after (v12 baseline vs v13) using the real-data rebuild path
  (`docs/testing/coach-report-eval.md` / `reanalyze_all.py`); confirm no rubric
  regression and the new safety assertions hold.
- B-vs-A artifact: for a few seeded activities, the v12 vs v13 pack + report diff,
  written to a file for the owner's "feels more human / memory adds value" human
  judgment (the deterministic eval is blind to that by design).
- Owner-flip runbook: the exact Railway env change
  (`COACH_PROMPT_ID=coach_message_v13` on web + worker), the rollback (flip back —
  zero code change), the `COACH_MEMORY_ENABLED` switch, and the post-deploy
  verification (`make post-deploy-verify`).

**Out of scope.** The prod flip itself (owner action). A profile-viewer UI
(spin-off). Any further coach capability.

**Linking.** Consumes everything (M1–M4). Produces the human decision inputs and
the runbook. Terminal block.

**TDD.** Validation milestone — verification over new unit tests, but: the seed
script path must run end-to-end without error, and the eval before/after must be
reproducible. Any bug found here loops back to the owning milestone via
aiw-failure-analysis, not a patch-in-place.

**Success criteria.**
- Real-data profiles generated for seeded users and reviewed against the four
  goals; sample captured.
- `make eval` shows no regression v12→v13; new safety assertions green on real
  reports.
- B-vs-A artifact and owner-flip runbook written.
- Final integration: all PRs rebased on a single alembic head, full suite green,
  flow diagram current.

---

## 3. Execution protocol (for the one-shot implementing agent)

This plan is built to be run by **one agent, one shot**, protecting its context
window and the human's quota, using the AI workflow and sub-agents, verifying its
work, and ending with **issues + open PRs ready for review**. The agent must
follow these rules.

### 3.1 Per-milestone loop (one issue, one branch, one PR)

For each of M1→M5, in order:

1. **aiw-github:** create the GitHub issue **directly** (no draft-for-approval),
   titled for the milestone, body = this milestone's description/intent/scope/
   out-of-scope/success-criteria. Branch from `main` (`feature/coach-memory-mN-*`).
2. **aiw-planning:** run the baseline check (smoke + suite green, alembic single
   head) before writing code. Classify modality (M1 New, M2 Feature, M3 Feature,
   M4 Delete+Refactor, M5 Investigate/Verify).
3. **aiw-ground-truth + tdd:** write the milestone's tests first (the TDD section
   above is the spec). For M2 the replay corpus *is* the oracle — build it before
   the writer.
4. Implement to green. **aiw-testing** for mechanics, **aiw-security-testing** for
   M2/M3 (untrusted-ish text into an LLM pass; the brief's containment posture).
5. **aiw-verification:** run the justification step before claiming done — name
   what was and wasn't checked. Do not declare done on exit codes; read runtime
   output.
6. **aiw-github:** commit, push, open the PR linking the issue. Flag ASK-FIRST
   items (M4's deletions + schema drop) at the top of the PR body. Do **not**
   merge — the open PR is the human's review gate.

### 3.2 Protecting context and quota

- **Delegate to sub-agents for breadth, keep conclusions in the main thread.**
  Use `Explore`/`general-purpose` agents for "where is X / does anything depend on
  Y" sweeps (as this plan was built) so file dumps never land in the main context.
  Spawn a fresh verification sub-agent to run a suite and report pass/fail + the
  failing lines, not the whole log.
- Read and search **narrowly** — never pull a whole large file when a symbol range
  will do. The recipes above name exact symbols to jump to.
- One PR per milestone keeps each review (and each context window) bounded. Do not
  batch milestones into a mega-PR.
- Run sub-agents for **independent** milestone-internal work in parallel (e.g.
  M3's diagram regen + eval-fixture update), but keep DB-migration and
  alembic-head work serial.

### 3.3 Verification discipline

- After M2 and M3, verify LLM-touching paths with a **real-call integration test**
  (integration-marked) at least once — `TestClient` buffers responses and is blind
  to streaming/timing (the #340 lesson). For structural assertions, stub the LLM.
- Validate any heuristic that reads run data against **real Strava/seed data**, not
  only synthetic fixtures (house rule).
- After every rebase, **re-check `alembic heads`** — a migration branch can fork
  into two heads that `create_all`-based tests won't catch but the deploy will.
- Regenerate `docs/diagrams/flow-nodes.js` in the **same PR** as any pack/prompt
  change (M3), and verify it renders.

### 3.3a Glossary maintenance (CONTEXT.md)

The grill resolved the vocabulary; the glossary edits land **in the milestone PRs
that make them true in code**, not up front (docs derive from code in this repo):

- **M1** introduces the new canonical term **Runner memory profile** (the
  five-section, rewritten, citable `Stated memory`-tier profile) and reshapes the
  **Stated memory** entry to point at it.
- **M4** retires the dead terms in the same PR that deletes the code: **Belief**,
  **CoachingContext**, **Preference profile**, **Consolidation** (or re-points it
  at the update pass), and rewrites **Durable memory** (the LLM narrative + belief
  layers are gone; the deterministic data layer stays but is just the data layer).
- **M3**'s addendum/authority wording should match the **Authority tiering** entry
  (memory = stated tier: citable, yields to measured data, never lowers the floor).

Keep CONTEXT.md a glossary only — no implementation detail.

### 3.4 Boundaries the agent must honour

- **ASK-FIRST → surface in the PR, don't self-approve:** the M4 deletions and the
  table-drop migration. The human merges; that is the approval.
- **No new dependency** — the design reuses `anthropic` (Haiku) and existing
  seams. If the agent thinks one is needed, stop and ask.
- **Do not touch the deterministic data layer** (RunnerBaseline, training load,
  calibration) — out of scope per brief §2.
- **Do not build a retrieval/ranking layer** — surfacing is whole-profile by
  design (brief §8). If the profile is ever too big, tighten the writer, never add
  retrieval.
- If a "done" claim is ever contradicted (a test that passed now fails, runtime
  disagrees), **stop and run aiw-failure-analysis** before any new fix.

### 3.5 Expected end state (the deliverable)

- **5 GitHub issues** (M1–M5), created directly.
- **5 open PRs**, each linked to its issue, each green on CI, none merged.
- **Spin-off issues** filed (not built) for deferred work. The grill surfaced
  these as definite:
  - The **deterministic intensity-distribution-and-trend signal** (easy/mod/hard
    share this run vs recent comparable runs) — the data-layer half of G2.
  - The **M7 adherence rest-day misread** (rest-day advice read as `ignored`) —
    the classifier bug the brief walls off (G3).
  - A **runner-facing memory-profile viewer UI** (owner deferred).
  - **Preference-conditioned framing**, if ever wanted back as a deliberate clean
    feature (G4 dropped M10; this is its only sanctioned return path).
  - Reconciling any offline/online writer duplication; anything else discovered.
- **ADR 0025** in the M1 PR; **owner-flip runbook** + **B-vs-A artifact** in the
  M5 PR.
- A short handoff naming the one remaining human action: review/merge the PRs,
  then flip `COACH_PROMPT_ID=coach_message_v13` (and `COACH_MEMORY_ENABLED`) in
  prod per the runbook.

---

## 4. Risk register (carry into every block)

| Risk | Where | Mitigation |
|---|---|---|
| Echo loop returns (the incident) | M2 | Anti-echo structural test: prior profile is never a writer input; graduation re-derived from sources, no stored counter. |
| Old stored packs fail to parse after retirement | M4 | Keep retired sections as Optional deprecated stubs; old-pack parse test. |
| M10 preference stranded by belief retirement | M4 | Retire M10 outright; function dropped, not replaced (G4). §4 is stated prefs, a different tier. |
| Prompt version drift (docs say v11/v12) | M3 | Read the real head prompt at build time; build vN+1 on the true head. |
| Two alembic heads after rebase | all | Re-check `alembic heads` after every rebase. |
| LLM idempotency can't be byte-exact | M2 | Define idempotency structurally (no drift/growth/unsourced lines), not as string equality. |
| Destructive migration / data loss | M4 | Flag in PR. Owner decision: drop without export (rows retired, raw sources hold truth). |
| Runner-facing profile viewer | spin-off | Out of scope; file a spin-off issue. Backend-only build. |
| Pack change ships without diagram update | M3 | Diagram regen is a success criterion, same PR. |
