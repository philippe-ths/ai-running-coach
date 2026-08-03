"""Structural guards on the post-activity cadence seam (#696).

#696 moved the cadence bodies out of `process_new_activity` into
`app/jobs/cadence/*`, with the shared side effects in `app/jobs/exchange_ops.py`.
Two properties of that split are invisible to the behavioural tests and would
regress silently, so they are pinned here:

1. The RQ ENTRYPOINTS keep their module path. RQ serializes a deferred job as its
   `module.function` string, so moving one strands every job already sitting in
   Redis across a deploy — up to BLOCK_GAP_SECONDS for a block-complete check and
   EXCHANGE_STAGE2_DELAY_SECONDS for a fuller timer. Nothing in the suite would
   notice; the damage shows up as silently missing coach reports in production.
2. No module reaches into another module's `_private` names — the seam-in-the-
   wrong-place shape #696 removed. A cadence must be readable from its own module.
"""

import ast
import pathlib

import pytest

from app.core.config import settings
from app.jobs import cadence as cadence_pkg
from app.jobs.cadence.opener_fuller import OpenerFullerCadence
from app.jobs.cadence.receipt import ReceiptCadence
from app.jobs.cadence.single_shot import SingleShotCadence

_JOBS_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "jobs"

# The four RQ entrypoints and the module path their on-Redis payloads name. A
# deferred job resolves this string at execution time, so this mapping is a
# deployment contract, not an implementation detail.
_PINNED_JOB_PATHS = {
    "process_new_activity_job": "app.jobs.process_new_activity",
    "block_complete_job": "app.jobs.process_new_activity",
    "fuller_turn_job": "app.jobs.process_new_activity",
    "regenerate_report_job": "app.jobs.process_new_activity",
}


@pytest.mark.parametrize("job_name,module_path", sorted(_PINNED_JOB_PATHS.items()))
def test_rq_entrypoint_keeps_its_module_path(job_name, module_path):
    """A deferred job in Redis names its function as `module.function`. Moving one of
    these to another module makes every already-scheduled instance unresolvable on the
    worker after deploy — a silent loss of the coach report for every block scheduled
    in the preceding window. If a move is genuinely wanted, it needs a drain plan, not
    just a green suite."""
    module = __import__(module_path, fromlist=[job_name])
    job = getattr(module, job_name)
    assert job.__module__ == module_path
    assert callable(job)


def _cross_module_private_reads(path: pathlib.Path) -> list[str]:
    """Attribute reads of the form `<module>._<name>` in one file — i.e. this module
    reaching into another module's privates."""
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
            and isinstance(node.value, ast.Name)
        ):
            found.append(f"{node.value.id}.{node.attr}")
    return found


@pytest.mark.parametrize(
    "relpath",
    [
        "cadence/__init__.py",
        "cadence/base.py",
        "cadence/single_shot.py",
        "cadence/opener_fuller.py",
        "cadence/receipt.py",
        "exchange_ops.py",
        "process_new_activity.py",
    ],
)
def test_no_cross_module_private_access(relpath):
    """#696 AC1: a cadence's behaviour is readable from a single module, and no module
    depends on another module's `_private` names. Before the split, `cadence.py` called
    eight `pna._*` helpers and following one cadence meant reading two files."""
    offenders = _cross_module_private_reads(_JOBS_DIR / relpath)
    assert offenders == [], f"{relpath} reaches into another module's privates: {offenders}"


def test_each_cadence_owns_its_events_in_its_own_module():
    """The three adapters and the events each acts on. A cadence that stops defining
    its own handler has had its body drift back out of its module."""
    assert SingleShotCadence.on_ingest.__module__ == "app.jobs.cadence.single_shot"
    assert SingleShotCadence.on_block_complete.__module__ == "app.jobs.cadence.single_shot"

    assert OpenerFullerCadence.on_ingest.__module__ == "app.jobs.cadence.opener_fuller"
    assert OpenerFullerCadence.on_block_complete.__module__ == "app.jobs.cadence.opener_fuller"
    assert OpenerFullerCadence.on_reply.__module__ == "app.jobs.cadence.opener_fuller"

    assert ReceiptCadence.on_ingest.__module__ == "app.jobs.cadence.receipt"
    assert ReceiptCadence.on_block_complete.__module__ == "app.jobs.cadence.receipt"
    assert ReceiptCadence.on_done.__module__ == "app.jobs.cadence.receipt"

    # The events an adapter deliberately does NOT act on stay the base no-op.
    assert SingleShotCadence.on_reply.__module__ == "app.jobs.cadence.base"
    assert SingleShotCadence.on_done.__module__ == "app.jobs.cadence.base"
    assert OpenerFullerCadence.on_done.__module__ == "app.jobs.cadence.base"
    assert ReceiptCadence.on_reply.__module__ == "app.jobs.cadence.base"


@pytest.mark.parametrize(
    "prompt_id,receipt_flag,expected",
    [
        ("coach_report_v10", False, SingleShotCadence),
        ("coach_message_v1", True, SingleShotCadence),  # inert under a single-shot prompt
        ("coach_message_v2", False, OpenerFullerCadence),
        ("coach_message_lean_grouped_v7", False, OpenerFullerCadence),
        ("coach_message_v2", True, ReceiptCadence),
        ("coach_message_lean_grouped_v7", True, ReceiptCadence),
    ],
)
def test_cadence_resolves_from_config_at_call_time(
    monkeypatch, prompt_id, receipt_flag, expected
):
    """#696 AC2 / ADR 0019: the cadence is read from config on every dispatch, never
    baked into a scheduled job's args, so a COACH_PROMPT_ID / COACH_RECEIPT_CADENCE flip
    is a zero-code-change rollback and a job scheduled under one cadence that fires after
    a flip does the now-current thing."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", prompt_id)
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", receipt_flag)
    assert isinstance(cadence_pkg.get_active_cadence(settings), expected)
