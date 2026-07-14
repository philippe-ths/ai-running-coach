# Coach grouped pack (grouped_v5) cutover runbook

The owner-flip runbook for the ADR 0026 finale (Slice 5, #682): flip the live coach
prompt from `coach_message_lean_v1` (flat pack) to `coach_message_lean_grouped_v5`
(the grouped, coach-native pack). This is the **only remaining human action** after
the Slice 5 PR is reviewed and merged. The agent cannot and must not do this.

The whole epic ships **inert**: the code default stays `coach_message_v8`, production
runs `coach_message_lean_v1`, and `coach_message_lean_grouped_v5` activates **only**
under the env flip below. Rollback is a pure config flip with zero code change.

**What grouped_v5 is:** the same context pack CONTENT lean_v1 already sends, re-nested
into the five coaching-question groups (`this_run` / `right_now` / `the_runner` /
`our_thread` / `how_to_coach` + top-level `safety_rules`) and served through the
completed coach-native LLM view — coach units (km, pace, %-of-max, M:SS), one merged
`this_run.intensity_read`, one merged `interval_read`, readiness reduced to its verdict,
the duplicated/misleading blocks collapsed, and the `salience` routing section dropped
from the fuller view. It is a **one-way view over the canonical pack**: nothing about
what is stored, validated, or re-parsed changes, so there is **no migration** and no
destructive step.

---

## 0. Prerequisites (before flipping)

1. **The Slice 5 PR (#682) is merged** to `main` with CI (`backend-test` +
   `frontend-test`) green.
2. **No migration to apply.** grouped_v5 adds no table, column, or schema change (the
   reshape is a serialization view over the existing pack). `alembic current` on prod
   does not need to move for this flip.
3. **A funded `ANTHROPIC_API_KEY`** is already set on both services (prod already runs
   the LLM coach on lean_v1); grouped_v5 needs nothing new. The runner-memory writer
   (a background Haiku call) is already active in prod and is unchanged — grouped_v5 is
   memory-aware exactly as lean_v1 is.
4. **`COACH_RECEIPT_CADENCE=true` stays set.** grouped_v5's salience drop is safe
   precisely because prod runs the receipt cadence, which fires the full report directly
   and never the on-the-fly LLM opener. Do not turn the cadence off as part of this flip.

---

## 1. The flip (Railway, two services)

Set on **both** the `web` and `worker` services (Railway env vars are per-service; the
worker runs the report pipeline, the web serves the report + chat read paths — both must
serve the grouped view):

```
COACH_PROMPT_ID=coach_message_lean_grouped_v5
```

Leave everything else as-is: `COACH_RECEIPT_CADENCE=true`, all `COACH_*_ENABLED` kill
switches unchanged (the pack audit that drove Slice 5 was done under the exact prod
kill-switch config), `COACH_MEMORY_ENABLED=true`. Redeploy both services.

What turns on (all view-only over the unchanged canonical pack):
- **Grouped envelope:** the pack serializes as the five coaching-question groups.
- **Coach-native leaves:** km/pace/%-max/M:SS units, trimmed precision (Slice 4).
- **Completed coach view (Slice 5):** `readiness` verdict-only; the four interval blocks
  collapsed into one `interval_read`; the plan-less `workout_match` dropped; `hr_drift`
  deduped to `intensity_read`; `training_history` sentinel/dupes cleaned; `recent_weeks`
  per-session HR as plain bpm; an empty `our_thread` dropped.
- **Salience dropped** from the fuller/full-report LLM view (the deterministic safety
  force still fires from the canonical pack object — the in-report safety surface is
  `this_run.referral`, unchanged).
- **Report and chat read an identical pack** (both seams share `coach_llm_view`).
- Reports regenerate under the new prompt id (new cache identity); all pre-flip history
  is retained.

---

## 2. Rollback (zero code change)

Flip back on both services:

```
COACH_PROMPT_ID=coach_message_lean_v1
```

Redeploy. This restores the exact prior behaviour: the flat pack, raw-unit leaves, the
four separate interval blocks, the full readiness numbers, and `salience` present in the
view. No stored data changes (the canonical pack was never grouped_v5-specific), so a
later re-flip resumes cleanly. Cadence and the kill switches are orthogonal and untouched.

---

## 3. Post-deploy verification

1. **Health gate:** `make post-deploy-verify` (`SMOKE_BASE_URL=<prod backend>`) — polls
   `/api/health` until healthy, then runs the deployed handshake smoke. The web process
   refuses to boot on a bad config, so a green health check is the first signal.
2. **Report wiring:** trigger a report (process a recent activity, or
   `POST /api/activities/{id}/coach-report/regenerate`) and confirm the new `CoachReport`
   has `prompt_id = coach_message_lean_grouped_v5`, and the message reads coherently off
   the grouped, coach-native pack (units in km/pace/%-max, intervals coached as one read).
3. **Chat wiring:** open the coach chat on that activity and confirm the chat coach reads
   the same framed/reshaped pack (it shares `coach_llm_view`) — no raw m/s or duplicated
   interval blocks leak into a chat answer.
4. **Eval no-regression:** a grouped_v5 eval sample scored this slice showed the safety
   floor holding (`no_medical_overreach` and `coached_direction_not_nagged` green, the
   `*_preserved_safety_surface` sensors untriggered). The floor is invariant by
   construction anyway: the canonical stored pack is byte-identical to grouped_v4, so the
   rubric reads unchanged facts, and the safety validator runs at generation on every
   report. A larger `make eval` regenerate pass is optional (~90s per report), not a gate.
5. **B-vs-A ("reads better"):** the owner eyeball judgment that the grouped, coach-native
   pack coaches better than the flat one — the deterministic eval is blind to this by
   design (#164).

---

## 4. What this cutover does NOT touch

- **The stored/canonical pack, the validator, the eval loader, and the chat re-parse** —
  all read the unchanged canonical grouped pack (byte-identical to grouped_v4).
- **The deterministic data layer** (`RunnerBaseline`, training load, calibration,
  readiness computation) — the reshape is presentational; the numbers are unchanged.
- **Cadence** (`COACH_RECEIPT_CADENCE`) — stays on; there is no LLM opener.
- **The `COACH_*_ENABLED` kill switches** and the runner-memory writer — orthogonal and
  unchanged.
