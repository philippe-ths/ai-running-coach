# A3 Output-Reframe Build Brief

> The what. The why and the decision live in `docs/adr/0009-coach-output-is-a-prose-message.md`; the full design and its evidence trail in `docs/vision/a3-output-reframe-design-synthesis.md`. Owner forks ratified 2026-06-10: medical-overreach-forces-fallback adopted; Exchange table deferred to A1; ship on `claude-sonnet-4-6`.

## Goal

The coach's post-activity output becomes a human prose message generated before any structure (thinking -> message -> thin strict-tool tail), stored under a new versioned-cache identity (`coach_message_v1`, schema 2.0) in the existing `coach_reports` table, policed by the existing six-rule validator over the full prose surface, rendered as a message in the frontend and Telegram/email, with the M4-M10 learning loop and the M5 eval gate intact across the cutover. Folds in #164's concern only as far as re-baselining the deterministic thresholds; the LLM-judge layer stays its own issue.

## Deliverables

1. **Output contract** (`app/services/coach/output_contract.py`): hand-frozen strict tool JSON (`record_coach_tail`: headline; 0-3 next_steps `{action, details, why, evidence}`; risks `{flag, explanation, mitigation}`; 0-4 questions with typed `TappableOption {id, label, kind: rpe|pain|reply|dispute|custom, payload}`), order-agnostic parser over text + single tool_use blocks, merge into `CoachMessageReport {message, headline, next_steps, risks, questions}`. Headline length bound lives in the prompt (strict subset forbids maxLength).
2. **LLM call** (`llm.py`): new method built to the strictest parameter surface: no sampling params, `thinking={"type":"adaptive"}` with explicit `display`, `tool_choice: auto` (mandatory with thinking), `messages.stream().get_final_message()`, `max_tokens` 8-16K, re-baselined timeout, distinct handling of `stop_reason` `end_turn` (tail skipped -> one corrective retry), `max_tokens` (truncated -> retry), `refusal` (-> fallback). Existing `generate_json`/chat paths byte-stable.
3. **Prompt** `coach_message_v1`: fresh prompt carrying every substantive discipline of rules 2-24 rewritten for prose, plus the output discipline (reason privately; write the message; call the tail once; the tail restates only what the message says). `coach_report_v1`..`v10` byte-stable; playbooks append unchanged.
4. **Validator** (`validator.py`): six rule bodies refactored into shared functions; legacy `validate_policy` reassembled byte-equivalently (pinned by an equivalence test); new `validate_message_policy` policing message + all tail text, rule 1 on structured questions, rule 3 on structured risks, rule 6 on tail evidence paths. Medical overreach surviving the retry forces `is_fallback=True` (the one behaviour change, strictly stronger).
5. **Storage/service** (`service.py`, `app/schemas/coach.py`): `SCHEMA_VERSION` becomes a prompt-family map (`coach_report` 1.2, `coach_message` 2.0) with `active_schema_version()` from the prompt-id prefix; store path branches on family; `CoachReportRead.report` becomes a discriminated union; `meta.tail_degraded`; digest branch in `digest.py` (lead_argument from the message's first sentence). Fallback and degraded-tail semantics per the ADR. No alembic migration.
6. **Render**: `CoachReportPanel` branches on `message` (markdown + option chips prefiling chat; legacy panel untouched); Telegram/email templates render headline + prose with paragraph-boundary truncation under 4096 chars; frontend types updated. `chat.py` needs no change.
7. **Eval** (`eval/`): extractor branches (`_report_text`/`_assertive_text`/`_lead_text`) for the union; harness loader branch; dual-shape selftest fixtures (good + deliberately-bad message reports); `tail_degraded` scorecard counter; cutover procedure in `docs/testing/coach-report-eval.md`.
8. **Docs**: ADR 0009 (done), `project-context.md` coach-layer/validator/prompt sections, `CONTEXT.md` reconciliation of the `CoachReport`-as-protagonist entries (Notification, Coach report generation) toward the Exchange vocabulary.

## Acceptance criteria

- AC1: With `COACH_PROMPT_ID=coach_message_v1`, a generated row stores prose `message` + tail fields in the `report` JSON column under schema 2.0; with a `coach_report_*` id the legacy path is byte-identical (existing suite green, identical pre/post-refactor eval scorecard on live v10 rows).
- AC2: `fetch_prior_commitments`, `adherence.py`, `write_back_beliefs` (and `tests/test_writeback_is_llm_free.py`) pass unmodified; M4 digests span a v10-then-2.0 sequence with identical `PriorReportDigest` shapes.
- AC3: `validate_message_policy` rejects each of the six rule violations injected into prose; legacy entry point proven byte-equivalent; persistent medical overreach yields `is_fallback=True`.
- AC4: Degraded tail stores the real message with `next_steps=[]` and `meta.tail_degraded=true`; no-text-block yields a templated fallback; a tail is never stored without a message.
- AC5: `make eval-selftest` validates both shapes (bad message fixture fails all applicable assertions); `make eval` scores whichever family is active; cutover compare on seeded regenerated data shows no assertion-level regression vs the v10/1.2 baseline (lexical thresholds re-baselined and documented).
- AC6: Frontend renders the message for 2.0 rows and the legacy panel for old rows (lint + build + smoke green); Telegram body is the prose, truncated at a paragraph boundary.
- AC7: Rollback rehearsed: flipping `COACH_PROMPT_ID` back to `coach_report_v10` re-serves cached v10 rows with zero code change.

## Verification

Step-gated per the synthesis migration path: (1) infra refactor under the old default, gated by identical eval scorecard; (2) generation path dark, stub-client unit tests; (3) render path dark; (4) local cutover rehearsal on `make seed-local` data with live regeneration and the eval compare (the A3 done-decision evidence; needs owner-authorised paid calls); (5) production flip is a Railway config change, owner-executed. TDD throughout; `make backend-test` green at every step.

## Out of scope

Exchange/Block tables and the record rename (A1); two-stage cadence and pipeline rework (A4); Telegram inline-keyboard delivery and tap handling (I1); chat-as-continuation (I2); the LLM-judge eval layer (#164 proper); any model flip to Opus (config decision, post-evaluation); `theme_hint` or any LLM-supplied label entering the deterministic write path.
