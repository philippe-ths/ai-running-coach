# Coach schedule-aware (grouped_v9) cutover runbook

The owner-flip runbook for #830: flip the live coach prompt from
`coach_message_lean_grouped_v7` (current prod) to `coach_message_lean_grouped_v9`,
the first prompt that lets the coach read the runner's own training plan. This is
the **only** human action; the agent cannot and must not do it.

**Read this before flipping — v9 is two capabilities relative to what is live,
not one.** Prod is currently on `grouped_v7`. `grouped_v8` (#742, the runner's
stated build) shipped INERT and was never flipped into prod on its own. `v9` is
built on top of `v8` (`v9 = v8 + PromptFeature.SCHEDULE`), so flipping straight
from `v7` to `v9` activates **both** the BODY capability (`profile.body` +
the body clause, #742) **and** the SCHEDULE capability (`right_now.schedule` +
the schedule clause, #830) in the same step. Someone reading only "v9 adds the
schedule" would miss that the body signal comes along for the ride. If you want
BODY and SCHEDULE verified as separable A/Bs, flip to `grouped_v8` first,
verify, then flip to `grouped_v9` — but the single-step flip below is the
supported path and is what this runbook assumes.

**What grouped_v9 is:** `grouped_v8`'s prose plus one new disposition bullet —
the coach is told the runner's plan says what a session was FOR, that it is
intent and never a record of what happened, and that a missed session is
"information about the week, never a charge for them to answer." In the pack it
is one new field, `right_now.schedule` (`ScheduleContext`, built by
`services/schedule/coach_view.build_schedule_context`), carrying what this
activity was planned to be, what is still to come this week, the spacing rules
in play, and two counts (`committed_this_week`, `done_this_week`) — deliberately
**no** adherence label, percentage, or per-session hit/miss. The prose variant
set is unchanged from v8 (`{ProseVariant.PERSONALISATION}`); the BODY and
SCHEDULE clauses are derived from the version's declared `PromptFeature`s, not
from a separate prose declaration, so a version cannot claim a clause its pack
does not carry.

**Inert until the runner has a plan.** `build_schedule_context` returns `None`
when the runner has no active `TrainingPlan` (or the plan has nothing to say —
no matched session, nothing upcoming, no rules), and `None` drops the field
byte-stably. For every runner without a schedule the v9 pack differs from v8's
by exactly the BODY delta already live in v8's design — nothing schedule-shaped
is added.

The change ships **inert**: the code default stays `coach_message_v8`,
production keeps running `coach_message_lean_grouped_v7`, and `grouped_v9`
activates **only** under the env flip below. Rollback is a pure config flip
with zero code change; reports generated under v9 keep `prompt_id =
coach_message_lean_grouped_v9` forever, so a rollback does not touch them —
the display-safe read (#261) serves each report's own version.

---

## 0. Prerequisites

1. **The PR is merged** to `main` with CI (`backend-test` + `frontend-test`) green.
2. **No migration required by this flip.** The `training_plan`/`planned_session`
   tables that back the schedule feature ship with #830 itself and are applied
   by the normal Railway pre-deploy `alembic upgrade head` on merge — by the
   time this runbook is followed those tables already exist. This step is a
   `COACH_PROMPT_ID` flip only.
3. **Pack change, so diagram regen is required.** `right_now.schedule` is a new
   field the coach LLM receives (nested under `right_now`, inert until a runner
   has a plan). `docs/diagrams/flow-nodes.js` must reflect it before or
   alongside this flip, per the diagram-regen discipline — do not skip this
   because the field is usually `null`.
4. **Decide up front whether BODY and SCHEDULE ship together.** See the two
   capabilities note above. Default: flip straight to `v9` (both together).
   If you want a clean A/B on SCHEDULE alone, flip to `coach_message_lean_grouped_v8`
   first, let it settle, then flip to `v9` in a second pass.
5. **`COACH_RECEIPT_CADENCE=true` stays set** (grouped_v9 inherits grouped_v7/v8's
   cadence assumptions; do not change it as part of this flip).
6. **Both `SCHEDULE_ENABLED` and `COACH_SCHEDULE_ENABLED` default `true`.** If
   either was previously set to `false` in the environment (e.g. the schedule
   surface was disabled while #830 was being built out), decide deliberately
   whether it stays that way — see the two-switches note below.

---

## 1. The flip (Railway, two services)

Set on **both** the `web` and `worker` services (env vars are per-service; the
worker generates reports, the web serves the report + chat read paths):

```
COACH_PROMPT_ID=coach_message_lean_grouped_v9
```

Leave everything else as-is (`COACH_RECEIPT_CADENCE=true`, all `COACH_*_ENABLED`
switches, `COACH_MEMORY_ENABLED=true`, `SCHEDULE_ENABLED=true`,
`COACH_SCHEDULE_ENABLED=true`). Redeploy both services. Reports regenerate under
the new prompt id (new cache identity); all pre-flip history is retained.

**The two schedule switches are not the same lever — do not confuse them:**

| Switch | Off does | Off does NOT do |
|---|---|---|
| `COACH_SCHEDULE_ENABLED` (default `true`) | Stops the **coach** seeing the plan: `right_now.schedule` drops byte-stably from the pack. | Touch the runner's Schedule screen — it keeps reading/writing plans exactly as before. |
| `SCHEDULE_ENABLED` (default `true`) | Takes the runner's **Schedule screen** down: every `/api/schedule` route refuses with 503, no Schedule tab renders. | Touch the coach — a report generates exactly as if the runner had no plan (`build_schedule_context` still resolves `None` in the pack-builder path, independent of the screen's route gate). |

If the goal is "the coach stops citing the plan but the runner keeps editing
it," flip `COACH_SCHEDULE_ENABLED=false` only. If the goal is "take the whole
feature down," `SCHEDULE_ENABLED=false` is the one that matters to the runner;
the coach input switch is separate and both can be set independently.

---

## 2. Rollback (zero code change)

Flip back on both services and redeploy:

```
COACH_PROMPT_ID=coach_message_lean_grouped_v7
```

This restores the exact prior report prose and pack shape — no BODY clause, no
SCHEDULE clause, no `profile.body`, no `right_now.schedule`. Reports already
generated under v9 are untouched (they keep `prompt_id =
coach_message_lean_grouped_v9` and render via the display-safe read, #261); a
later re-flip to v9 resumes cleanly with no data migration. If you only want to
back out SCHEDULE while keeping BODY live, roll back to
`coach_message_lean_grouped_v8` instead of all the way to `v7`.

To stop the coach reading the plan without a prompt rollback (e.g. to isolate
whether a report defect traces to the schedule signal), set
`COACH_SCHEDULE_ENABLED=false` and leave `COACH_PROMPT_ID=coach_message_lean_grouped_v9`
in place — the pack section drops, the rest of v9 stays live.

---

## 3. Post-deploy verification

1. **Health gate:** `make post-deploy-verify` (`SMOKE_BASE_URL=<prod backend>`).
   The web process refuses to boot on a bad config, so a green health check is
   the first signal.
2. **Report wiring:** trigger a report (process a recent activity, or
   `POST /api/activities/{id}/coach-report/regenerate`) and confirm the new
   `CoachReport` has `prompt_id = coach_message_lean_grouped_v9`.
3. **Safety floor:** invariant by construction — the deterministic validator
   runs at generation on every report regardless of prompt id, and the schedule
   signal degrades to `None` on any internal fault rather than failing the
   report (`ctx._build_schedule_context` swallows the builder's exception; see
   `backend/tests/test_schedule_pack_section.py::test_a_schedule_fault_never_costs_the_runner_their_report`).
4. **A runner WITH an active plan:** find (or seed, on the deployed environment
   with a real account) a runner who has a `TrainingPlan` with at least one
   session near the activity being reported on, then read the generated
   report:
   - It should say **what the session was for** — e.g. reference the
     prescribed structure or intent ("the 800s you had down for today") rather
     than only describing what the stream shows.
   - It should **not** read as a compliance scorecard: no "you completed 3 of
     5 sessions," no percentage, no explicit hit/miss language. This is an
     **owner eyeball judgment, not something the deterministic eval covers**
     — the rubric has no tone sensor and none was added for #830 (the schedule
     framing is enforced structurally, by the section carrying no adherence
     field at all — see the "framing" claim in
     `test_schedule_pack_section.py` — not by an eval assertion that reads the
     LLM's prose).
5. **A runner WITHOUT a plan:** confirm their report is unchanged from what
   `grouped_v8` would have produced — no schedule-shaped content appears, and
   nothing in the report implies the runner should have a plan.
6. **No `is_fallback` regression:** compare the `is_fallback=True` rate on
   reports generated in the hours after the flip against the pre-flip
   baseline. A rise would point at the new pack field or clause surfacing a
   validator or parse failure that the fourteen-assertion eval and the v9
   prompt test did not catch pre-merge.
7. **Eval no-regression:** rebuild eval inputs and run `make eval`
   before/after. The existing fourteen deterministic rubric assertions still
   apply unchanged (led-with-headline, no-medical-overreach, the
   `*_preserved_safety_surface` sensors for voice/corpus/user-materials/memory,
   `coached_direction_not_nagged`, `body_not_made_the_subject`, and the rest);
   none of them reads the schedule section, and #830 adds no fifteenth
   assertion. A clean run here says the floor held, not that the schedule
   framing landed well in prose — that is step 4.

---

## 4. What this cutover does NOT touch

- The deterministic data layer (`RunnerBaseline`, training load, calibration,
  intensity) and the M7 adherence loop — unchanged; the schedule section is a
  separate, additive signal and does not feed or read from adherence.
- Cadence (`COACH_RECEIPT_CADENCE`), voice, stance, corpus, user-materials,
  memory, training-load/volume/history, recent-training — all carried forward
  unchanged from v8 (v9 is v8 + exactly the SCHEDULE capability).
- The runner's Schedule screen and its API (`/api/schedule/*`) — those are
  gated by `SCHEDULE_ENABLED`, not by `COACH_PROMPT_ID`; this flip changes
  only what the coach reads, never what the runner can plan or edit.
- The `COACH_*_ENABLED` kill switch family besides `COACH_SCHEDULE_ENABLED`
  itself — orthogonal, untouched by this cutover.
