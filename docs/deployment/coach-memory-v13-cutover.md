# Coach memory (v13) cutover runbook

The owner-flip runbook for the coach-memory redesign (ADR 0025; build plan
`docs/design/coach-memory-build-plan.md`). After the five PRs (M1-M5) are
reviewed and merged, this is the **only remaining human action**: flip the live
prompt to `coach_message_v13` and turn the writer on. The agent cannot and must
not do this.

The whole redesign ships **inert**: the code default stays `coach_message_v8`,
production runs `coach_message_v12`, and `coach_message_v13` + the writer activate
**only** under the env flips below. Rollback is a pure config flip with zero code
change.

---

## 0. Prerequisites (before flipping)

1. **All five PRs merged** in order: M1 (#572) -> M2 (#574) -> M3 (#576) -> M4
   (#582) -> M5 (#583's PR). Each base auto-retargets to `main` as its parent
   merges; confirm CI (`backend-test` + `frontend-test`) is green on each as it
   reaches `main`.
2. **M4's drop migration is applied** to the production database. The Railway
   release command runs `alembic upgrade head`, so merging M4 to `main` and
   redeploying applies `2f8b6d3a1c90_drop_durable_memory_tables` automatically.
   This is the **destructive step** (drops `coaching_context`, `coach_narratives`,
   `activities.beliefs_written_at`); it is intentional and export-free (the raw
   sources hold the truth). Confirm the deploy ran it: `alembic current` on the
   prod DB should read `2f8b6d3a1c90` (or later).
3. **A funded `ANTHROPIC_API_KEY`** is set on the **worker** service (the writer
   is a background Haiku call). Without credit the writer degrades to a no-op
   (logged, never crashes) and every runner simply has no memory profile yet, so
   the `memory` pack section stays absent and v13 behaves like v12-plus-addendum.

---

## 1. The flip (Railway, two services)

Set on **both** the `web` and `worker` services (Railway env vars are per-service;
the worker runs the report pipeline + the writer, the web serves the read path):

```
COACH_PROMPT_ID=coach_message_v13
COACH_MEMORY_ENABLED=true     # default is already true; set explicitly for clarity
```

`COACH_MEMORY_ENABLED` defaults to `true`, so setting `COACH_PROMPT_ID` alone is
enough to activate both the reader (the `memory` pack section) and the writer (the
post-report `update_memory_job` enqueue) together — they share the
`PromptFeature.MEMORY` gate, so it is a single-flip cutover. Redeploy both
services after setting the vars.

What turns on:
- **Reader:** every v13 report's context pack carries the `memory` section once a
  runner has a graduated profile (absent until then, byte-stable).
- **Writer:** after each non-fallback v13 report, `update_memory_job` rewrites that
  runner's profile from source (a cheap `claude-haiku-4-5` call, budget-recorded).
- **Prompt:** the v13 system prompt = v12 + the runner-memory addendum (authority
  tiering + the anti-nag directional discipline).

The first few v13 reports per runner will have **no** memory section (the profile
is built *after* a report, from that report's sources onward). Memory accrues over
subsequent runs; this is expected, not a fault.

---

## 2. Rollback (zero code change)

Flip back on both services:

```
COACH_PROMPT_ID=coach_message_v12
```

Redeploy. This restores the exact prior behaviour: no `memory` section, no
addendum, no writer enqueue (the enqueue gates on a memory-aware prompt). Stored
`runner_memory` rows are left untouched and simply go unread, so a later re-flip
to v13 resumes from the existing profiles. To also stop the writer while staying
on v13 (e.g. to pause Haiku spend), set `COACH_MEMORY_ENABLED=false` instead.

Cadence (`COACH_RECEIPT_CADENCE`) and the `COACH_*_ENABLED` kill switches are
orthogonal and unchanged by this cutover.

---

## 3. Post-deploy verification

1. **Health gate:** `make post-deploy-verify` (`SMOKE_BASE_URL=<prod backend>`) —
   polls `/api/health` until healthy, then runs the deployed handshake smoke. The
   web process refuses to boot on a bad config, so a green health check is the
   first signal the flip did not break boot.
2. **Reader wiring:** trigger a report (sync/process a recent activity, or
   `POST /api/activities/{id}/coach-report/regenerate`) and confirm a v13 report
   is produced (`prompt_id` = `coach_message_v13` on the new `CoachReport`).
3. **Writer wiring:** after that report, confirm a `runner_memory` row exists for
   the runner and `model_id = claude-haiku-4-5` (the writer fired). On the next
   report, confirm the pack's `memory` section is present and reads like a coach's
   notes (see the acceptance goals below).
4. **Eval no-regression:** rebuild eval inputs and run `make eval` before/after
   (v12 baseline already captured in `docs/testing/coach-memory-v13-bva.md`);
   confirm no rubric regression and that `memory_preserved_safety_surface` +
   `coached_direction_not_nagged` stay green on the v13 reports.

### Acceptance goals for a generated profile (eyeball check)
- **Reads like a coach's notes** about this person, not a data dump.
- **One screen** — capped sections, no wall of text.
- **Constant size** — it does not grow unbounded run over run (rewrite-from-source,
  not append).
- **Same five sections for everyone** (`who_you_are`, `limits_and_constraints`,
  `goals_and_plans`, `what_works_for_you`, `lately`).
- **No behavioral verdict** — it states what the runner told the coach, never an
  inferred "ignores easy days" judgment (the incident this redesign prevents).

---

## 4. What this cutover does NOT touch

- The deterministic data layer (`RunnerBaseline`, training load, calibration) and
  M7 adherence — unchanged.
- Cadence, voice, stance, corpus, user-materials, training-load/volume/history,
  recent-training — all carried forward unchanged by v13 (it is v12 + memory).
- The `COACH_*_ENABLED` kill switches and `COACH_RECEIPT_CADENCE` — orthogonal.
