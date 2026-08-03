"""Post-activity cadence seam (#330, redrawn in #696).

After an activity is ingested, analyzed, and block-assigned, what the coach does
next — the shape and timing of the `Exchange` — depends on the active configuration.
There are three cadences, one module each:

- SINGLE-SHOT (`single_shot.py`; any non-two-stage prompt: coach_message_v1,
  coach_report_v*): one report generated and notified inline on ingest.
- OPENER/FULLER (`opener_fuller.py`; a two-stage prompt, COACH_RECEIPT_CADENCE off;
  A4 / ADR 0010): a debounced LLM opener once the block looks complete, then a
  conditional fuller turn fired by the runner's reply (early) or a timer.
- RECEIPT (`receipt.py`; a two-stage prompt, COACH_RECEIPT_CADENCE on; #296 /
  ADR 0018): an instant deterministic per-activity receipt on ingest, then one full
  LLM report ~BLOCK_GAP after the session (the block-complete debounce) or on a
  "done" tap.

Before this seam, the decision was ~10 `is_two_stage_prompt` / `is_receipt_cadence`
gates scattered across six functions in `process_new_activity`. This package is now
the one place it lives. `get_active_cadence(settings)` reads the config AT DISPATCH
TIME (never baked into a scheduled job's args), so flipping COACH_PROMPT_ID /
COACH_RECEIPT_CADENCE stays a zero-code-change rollback: a job scheduled under one
cadence that fires after a flip does the now-current thing (AC6).

#696 moved the cadence BODIES here. They were `_private` helpers in
`process_new_activity` that these adapters reached back across the module boundary to
call, so following one cadence meant reading two files and bouncing caller -> wrapper
-> adapter -> private. Each cadence now reads top-to-bottom in its own module, and the
shared, cadence-agnostic side effects (notify + sentinel, block/exchange resolution,
deferred scheduling, the fuller turn) live in `app/jobs/exchange_ops.py`, which the
cadence modules call and which never calls back into a cadence.

The RQ ENTRYPOINTS deliberately stay in `process_new_activity`: RQ serializes a
deferred job as its `module.function` path, so moving them would strand every
block-complete check and fuller timer already sitting in Redis across a deploy.

The post-activity events:
- on_ingest         — a fresh activity has been ingested + analyzed + block-assigned
- on_block_complete — the block-complete debounce fired (scheduled at +BLOCK_GAP)
- on_reply          — the runner replied (a CheckIn or chat message) on a block member
- on_done           — the runner tapped "done" (receipt cadence only)
"""

from app.jobs.cadence.base import PostActivityCadence
from app.jobs.cadence.opener_fuller import OpenerFullerCadence
from app.jobs.cadence.receipt import ReceiptCadence
from app.jobs.cadence.single_shot import SingleShotCadence
from app.services.coach.service import is_receipt_cadence, is_two_stage_prompt

__all__ = [
    "PostActivityCadence",
    "SingleShotCadence",
    "OpenerFullerCadence",
    "ReceiptCadence",
    "get_active_cadence",
]


def get_active_cadence(settings) -> PostActivityCadence:
    """The cadence for the active configuration, resolved AT CALL TIME so a config
    flip is a zero-code-change rollback. Receipt requires the flag AND a two-stage
    prompt; opener/fuller is any other two-stage prompt; everything else is
    single-shot."""
    if is_receipt_cadence(settings.COACH_PROMPT_ID):
        return ReceiptCadence()
    if is_two_stage_prompt(settings.COACH_PROMPT_ID):
        return OpenerFullerCadence()
    return SingleShotCadence()
