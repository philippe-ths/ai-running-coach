# Coach personalisation (grouped_v7) cutover runbook

The owner-flip runbook for the "coach this runner, not the median" personalisation:
flip the live coach prompt from `coach_message_lean_grouped_v5` (current prod) to
`coach_message_lean_grouped_v7`. This is the **only** human action; the agent cannot and
must not do it.

**What grouped_v7 is:** `grouped_v5`'s prose plus one disposition bullet in the coach's
"how I coach" list — the coach adapts method to *this* runner (their build, history, and
stated facts) and treats the standard playbook as a starting point, not a template. It is
a **system-prompt TEXT change only**: grouped_v7 carries the byte-identical feature set and
receives the byte-identical context pack as grouped_v5, so the flip is a clean A/B on the
prose alone. It is a **sibling** of the also-inert `grouped_v6` (the past-session laps
clause) — each isolates one change off `grouped_v5`, so flipping to `grouped_v7` moves
personalisation and nothing else.

The change ships **inert**: the code default stays `coach_message_v8`, production keeps
running `coach_message_lean_grouped_v5`, and `grouped_v7` activates **only** under the env
flip below. Rollback is a pure config flip with zero code change.

The chat prompt and the North Star carry the same personalisation disposition, but those
are **not** version-gated: they go live on the next deploy of this branch regardless of
`COACH_PROMPT_ID`. So after merge, the *chat* coach is already personalised; this flip
brings the *report* coach in line.

---

## 0. Prerequisites

1. **The PR is merged** to `main` with CI (`backend-test` + `frontend-test`) green.
2. **No migration.** grouped_v7 adds no table, column, or schema change — `alembic current`
   on prod does not need to move.
3. **No pack change, so no diagram regen.** The coach receives the same context pack.
4. **`COACH_RECEIPT_CADENCE=true` stays set** (grouped_v7 inherits grouped_v5's cadence
   assumptions; do not change it as part of this flip).

---

## 1. The flip (Railway, two services)

Set on **both** the `web` and `worker` services (env vars are per-service; the worker runs
the report pipeline, the web serves the report + chat read paths):

```
COACH_PROMPT_ID=coach_message_lean_grouped_v7
```

Leave everything else as-is (`COACH_RECEIPT_CADENCE=true`, all `COACH_*_ENABLED` switches,
`COACH_MEMORY_ENABLED=true`). Redeploy both services. Reports regenerate under the new
prompt id (new cache identity); all pre-flip history is retained.

---

## 2. Rollback (zero code change)

Flip back on both services and redeploy:

```
COACH_PROMPT_ID=coach_message_lean_grouped_v5
```

This restores the exact prior report prose. No stored data changes, so a later re-flip
resumes cleanly. (The chat-side personalisation is not affected by this flip.)

---

## 3. Post-deploy verification

1. **Health gate:** `make post-deploy-verify` (`SMOKE_BASE_URL=<prod backend>`). The web
   process refuses to boot on a bad config, so a green health check is the first signal.
2. **Report wiring:** trigger a report (process a recent activity, or
   `POST /api/activities/{id}/coach-report/regenerate`) and confirm the new `CoachReport`
   has `prompt_id = coach_message_lean_grouped_v7`.
3. **Safety floor:** invariant by construction — grouped_v7 minus the personalisation
   bullet is byte-identical to grouped_v5 (pinned by `test_message_prompt_lean_grouped.py`),
   the stored/canonical pack is unchanged, and the deterministic validator runs at
   generation on every report.
4. **B-vs-A ("does the coach adapt to the runner"):** the owner eyeball judgment — does the
   report lean on this runner's build/history/stated facts rather than median advice? The
   deterministic eval is blind to this by design (#164).
