"""#874: the FastAPI upper pin has a renewal trigger, and the trigger is wired.

#809 pinned `fastapi>=0.141.0,<0.142.0`. The bound is what makes local and CI
resolve the same route model, which is the condition that made the structural
route sweeps meaningful again. Nothing was ever going to tell us the bound had
gone stale, so `.github/workflows/fastapi-pin.yml` runs
`backend/scripts/check_fastapi_pin.py` weekly.

Two kinds of rot would make that trigger silently useless, and both are checked
here rather than left to the scheduled job to discover months later:

* the pin is reformatted, removed, or moved, and the reader that describes it
  quietly matches nothing;
* the workflow stops running the script, or stops being scheduled, in which
  case there is a checked-in check that nobody calls.

The PyPI payloads below are test setup shaped like the real response
(exercises code paths; the live payload is not an oracle for anything asserted
here). The pin itself is read from the REAL pyproject.toml, which is the point.
"""

import re
from pathlib import Path

import pytest

from scripts.check_fastapi_pin import (
    GUARD_TESTS,
    Pin,
    is_outside,
    main,
    newest_version,
    read_pin,
    verdict,
)

_REPO = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO / "backend" / "pyproject.toml"
_WORKFLOW = _REPO / ".github" / "workflows" / "fastapi-pin.yml"


# --- the reader describes the pin that is actually declared -------------------


def test_the_real_pin_is_read_off_the_real_pyproject():
    """The anti-rot check. If the constraint is reformatted the reader stops
    matching, and a check that reads nothing reports nothing — so that failure
    lands here, in the suite every change runs, rather than in a scheduled job
    somebody looks at once a quarter."""
    pin = read_pin(_PYPROJECT.read_text())

    assert pin is not None, (
        "backend/pyproject.toml no longer declares fastapi as "
        '"fastapi>=X,<Y", so the renewal trigger cannot describe it'
    )
    assert pin.lower.startswith("0.141")
    assert pin.upper == "0.142.0"


def test_an_unbounded_constraint_is_read_as_no_pin_at_all():
    """SENSITIVITY. Removing the bound is the change that quietly gives back the
    #809 defect, so it must not read as a healthy pin."""
    assert read_pin('dependencies = ["fastapi>=0.141.0",]') is None


def test_a_pin_on_another_package_is_not_mistaken_for_this_one():
    assert read_pin('["starlette>=0.41.0,<0.42.0"]') is None


def test_whitespace_in_the_constraint_does_not_defeat_the_reader():
    pin = read_pin('["fastapi>=0.141.0, <0.142.0"]')
    assert pin == Pin(lower="0.141.0", upper="0.142.0")


# --- the decision -------------------------------------------------------------


_PIN = Pin(lower="0.141.0", upper="0.142.0")


@pytest.mark.parametrize(
    "version, outside",
    [
        ("0.141.0", False),
        ("0.141.9", False),
        ("0.142.0", True),
        ("0.143.2", True),
        ("1.0.0", True),
        ("0.140.5", False),
    ],
)
def test_a_release_is_outside_the_pin_exactly_when_pip_would_refuse_it(version, outside):
    assert is_outside(version, _PIN) is outside


def _payload(*versions, yanked=(), files=True):
    return {
        "releases": {
            v: ([{"yanked": v in yanked}] if files else [])
            for v in versions
        }
    }


def test_the_newest_release_is_the_newest_by_version_not_by_string_order():
    """0.142.0 sorts BELOW 0.99.0 as text and above it as a version. Sorting the
    keys as strings would report a two-year-old release as the newest one and
    the trigger would never fire again."""
    assert newest_version(_payload("0.141.0", "0.99.0", "0.142.0")) == "0.142.0"


def test_a_pre_release_is_not_reported_as_something_to_move_a_pin_onto():
    assert newest_version(_payload("0.141.0", "0.142.0b1")) == "0.141.0"


def test_a_fully_yanked_release_is_not_reported():
    """PyPI's way of saying the release was withdrawn. Reporting it would send
    somebody to raise a pin onto a version that should not be installed."""
    assert newest_version(_payload("0.141.0", "0.142.0", yanked=("0.142.0",))) == "0.141.0"


def test_a_release_with_no_files_is_not_reported():
    assert newest_version(_payload("0.142.0", files=False)) is None


# --- what the reader is told --------------------------------------------------


def test_a_passing_guard_says_the_pin_is_now_only_holding_us_back():
    message = verdict("0.142.0", _PIN, guard_passed=True)

    assert "PASS" in message
    assert "Raise it" in message
    # The cost of leaving it must be stated, or "do nothing" looks free.
    assert "security" in message


def test_a_failing_guard_says_the_pin_is_earning_its_keep():
    message = verdict("0.142.0", _PIN, guard_passed=False)

    assert "FAIL" in message
    assert "earning its keep" in message
    # The decision it puts is how long to stay behind, not whether to bump.
    assert "bump the number" in message


# --- the trigger fails loudly rather than warning -----------------------------


def test_a_release_past_the_pin_exits_non_zero(capsys):
    """The whole posture. A warning in a scheduled job's log is no signal."""
    code = main(
        [],
        fetch=lambda: _payload("0.141.0", "0.142.0"),
        run_guard=lambda version: True,
    )

    assert code == 1
    assert "0.142.0" in capsys.readouterr().err


def test_a_release_inside_the_pin_exits_zero(capsys):
    code = main(
        [],
        fetch=lambda: _payload("0.141.0", "0.141.9"),
        run_guard=lambda version: pytest.fail("the guard ran for a covered release"),
    )

    assert code == 0
    assert "covers it" in capsys.readouterr().out


def test_the_guard_actually_runs_against_the_candidate_release():
    """AC2: the trigger covers the route-model behaviour rather than just the
    number. The candidate version is what gets installed and tested."""
    seen = []
    main([], fetch=lambda: _payload("0.142.3"), run_guard=lambda v: seen.append(v) or True)

    assert seen == ["0.142.3"]


def test_a_pin_the_reader_cannot_find_is_itself_a_failure(monkeypatch, tmp_path, capsys):
    """Not "no pin, nothing to renew". No pin is the #809 defect returning."""
    from scripts import check_fastapi_pin

    probe = tmp_path / "pyproject.toml"
    probe.write_text('dependencies = ["fastapi"]\n')
    monkeypatch.setattr(check_fastapi_pin, "PYPROJECT", probe)

    code = check_fastapi_pin.main([], fetch=lambda: _payload("0.142.0"))

    assert code == 1
    assert "upper bound" in capsys.readouterr().err


# --- the trigger is wired -----------------------------------------------------


def test_the_workflow_runs_this_script_on_a_schedule():
    """A checked-in check nobody calls is the vacuous-guard failure this
    repository has found five times. The wiring is the check."""
    workflow = _WORKFLOW.read_text()

    assert "schedule:" in workflow, "the renewal trigger is not scheduled"
    assert re.search(r"cron:\s*\"[^\"]+\"", workflow), "no cron expression"
    assert "backend/scripts/check_fastapi_pin.py" in workflow, (
        "the workflow no longer runs the check it exists to run"
    )
    assert "workflow_dispatch:" in workflow, "no way to run it on demand"


def test_the_guards_the_trigger_runs_still_exist():
    """The script names test files by path. A renamed file would turn the
    candidate run into a pytest collection error that reads like a FastAPI
    incompatibility."""
    for path in GUARD_TESTS:
        assert (_REPO / "backend" / path).exists(), path


def test_the_candidate_run_deselects_the_declared_constraint_bookkeeping():
    """Found by running the script end to end rather than trusting it.

    `test_the_installed_fastapi_matches_the_declared_constraint` asserts the
    installed fastapi satisfies the constraint in pyproject.toml. The candidate
    run installs a version the constraint deliberately refuses, so that test
    fails for bookkeeping and the verdict reported "the pin is earning its keep"
    when nothing about the route model had moved. It is deselected by node id,
    so the route-model walk in the same file still runs.
    """
    from scripts.check_fastapi_pin import GUARD_DESELECT

    assert GUARD_DESELECT, "the false-verdict deselect was removed"
    for node in GUARD_DESELECT:
        path, _, name = node.partition("::")
        assert path in GUARD_TESTS, f"{node} deselects a file the guard does not run"
        source = (_REPO / "backend" / path).read_text()
        assert f"def {name}(" in source, (
            f"{node} names a test that no longer exists, so the deselect is silently "
            "doing nothing and the false verdict is back"
        )
