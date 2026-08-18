#!/usr/bin/env python
"""Does the FastAPI upper pin still need to be there? (#874)

#809 pinned `fastapi>=0.141.0,<0.142.0`. The upper bound is what makes local
and CI resolve the same route model, which is the condition that made the
auth-gate coverage guard meaningful again: FastAPI 0.141 changed
`include_router` from flattening routes into `app.routes` to wrapping them
behind an opaque `_IncludedRouter`, and an unbounded constraint let CI resolve
a route model the local venv never saw. The guard went green while inspecting
zero routes.

A hard upper bound is a standing commitment to look at it periodically, and
nothing in this repository produced that reminder. The project would simply
stop receiving FastAPI releases, security fixes included, and the first signal
would be somebody noticing.

This is that signal. It runs on a schedule, and when a release exists past the
pin it does not merely report the number: it installs that release and runs the
route-model guards under it, so the report says which of the two situations
this is.

  * The guards PASS under the newer release. The pin has done its job and is now
    only holding the project back. Raise it.
  * The guards FAIL under the newer release. The pin is earning its keep, and
    the failure text is the evidence of what changed. The decision is then how
    long to stay behind, not whether the pin was ever justified.

Either way a human has to look, so either way this exits non-zero. It fails
rather than warns deliberately: a warning in a scheduled job's log is the same
as no signal at all, which is the failure mode this exists to remove.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

_BACKEND = Path(__file__).resolve().parents[1]
PYPROJECT = _BACKEND / "pyproject.toml"

# The route-model behaviour the pin protects. Running these under the candidate
# release is the whole point: AC2 of #874 is that the trigger covers the
# behaviour rather than just bumping a number.
GUARD_TESTS = (
    "tests/test_route_table.py",
    "tests/test_route_ownership_802.py",
    "tests/test_clerk_auth.py",
)

# One test in that set asserts the INSTALLED fastapi satisfies the DECLARED
# constraint. That is the right check for an ordinary run and the wrong one
# here: this run installs a version the constraint deliberately refuses, so the
# test fails for bookkeeping and the verdict below would report "the pin is
# earning its keep" when nothing about the route model had moved at all. It is
# deselected by node id rather than by skipping the file, so the rest of that
# file — the route-model walk this whole exercise is about — still runs.
#
# Found by running this script end to end against a lowered pin rather than
# trusting it; the first real run produced exactly that false verdict.
GUARD_DESELECT = (
    "tests/test_route_table.py::test_the_installed_fastapi_matches_the_declared_constraint",
)

_PIN = re.compile(r'"fastapi>=(?P<lower>[^,"\s]+)\s*,\s*<(?P<upper>[^"\s]+)"')

PYPI_URL = "https://pypi.org/pypi/fastapi/json"


@dataclass(frozen=True)
class Pin:
    lower: str
    upper: str

    @property
    def spec(self) -> str:
        return f">={self.lower},<{self.upper}"


def read_pin(pyproject_text: str) -> Optional[Pin]:
    """The declared FastAPI constraint, or None if it is no longer this shape.

    None is a real answer and the caller treats it as a failure: an unbounded
    constraint means #809's guarantee is gone, and a differently-shaped bound
    means this reader is describing a pin that no longer exists. Either way
    somebody has to look, which is the same posture the diagram guard's
    extractors take.
    """
    match = _PIN.search(pyproject_text)
    if match is None:
        return None
    return Pin(lower=match.group("lower"), upper=match.group("upper"))


def newest_version(payload: dict[str, Any]) -> Optional[str]:
    """The newest real release in a PyPI project JSON payload.

    Pre-releases are skipped: a release candidate is not something to move a
    production pin onto, and reporting one would train the reader to ignore
    this. So is a version whose every file is yanked, which is PyPI's way of
    saying the release was withdrawn.
    """
    from packaging.version import InvalidVersion, Version

    best: Optional[Version] = None
    for raw, files in (payload.get("releases") or {}).items():
        if not files or all(f.get("yanked") for f in files):
            continue
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if version.is_prerelease:
            continue
        if best is None or version > best:
            best = version
    return str(best) if best is not None else None


def is_outside(version: str, pin: Pin) -> bool:
    """Is this release one the declared constraint would refuse to install?"""
    from packaging.version import Version

    return Version(version) >= Version(pin.upper)


def verdict(newest: str, pin: Pin, guard_passed: bool) -> str:
    """What the reader is being asked to decide, given how the guards fared."""
    if guard_passed:
        return (
            f"fastapi {newest} is past the pin ({pin.spec}) and the route-model guards "
            f"PASS under it.\n"
            f"The pin has done its job. Raise it in backend/pyproject.toml, and let the "
            f"normal CI run vote on the upgrade.\n"
            f"Leaving it is a standing decision to decline FastAPI releases, security "
            f"fixes included."
        )
    return (
        f"fastapi {newest} is past the pin ({pin.spec}) and the route-model guards FAIL "
        f"under it.\n"
        f"The pin is earning its keep: something in that release moves the route model "
        f"the structural sweeps walk, which is the #809 failure exactly.\n"
        f"The decision is how long to stay behind and what to do about the guards, not "
        f"whether to bump the number. The failure output above is the evidence."
    )


def _fetch(url: str = PYPI_URL) -> dict[str, Any]:
    import httpx

    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    return response.json()


def _run_guard(version: str) -> bool:
    """Install the candidate release and run the guards the pin protects under it."""
    print(f"==> installing fastapi=={version}", flush=True)
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", f"fastapi=={version}"],
        cwd=_BACKEND,
    )
    if install.returncode != 0:
        print("==> could not install the candidate release", file=sys.stderr)
        return False
    print(f"==> running {', '.join(GUARD_TESTS)} under fastapi {version}", flush=True)
    deselect: list[str] = []
    for node in GUARD_DESELECT:
        deselect += ["--deselect", node]
    guard = subprocess.run(
        [sys.executable, "-m", "pytest", *GUARD_TESTS, *deselect, "-q"], cwd=_BACKEND
    )
    return guard.returncode == 0


def main(
    argv: Optional[list[str]] = None,
    *,
    fetch: Callable[[], dict[str, Any]] = _fetch,
    run_guard: Callable[[str], bool] = _run_guard,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-guard",
        action="store_true",
        help="report the version only, without installing it or running the guards",
    )
    args = parser.parse_args(argv)

    pin = read_pin(PYPROJECT.read_text())
    if pin is None:
        print(
            "backend/pyproject.toml no longer declares fastapi with an upper bound in "
            "the form this check reads. Either the #809 pin was removed, in which case "
            "local and CI can resolve different route models again, or it was rewritten "
            "and this check is now describing a constraint that does not exist. Both "
            "need a human.",
            file=sys.stderr,
        )
        return 1

    newest = newest_version(fetch())
    if newest is None:
        print("PyPI returned no usable fastapi release.", file=sys.stderr)
        return 1

    if not is_outside(newest, pin):
        print(f"fastapi {newest} is the newest release and the pin ({pin.spec}) covers it.")
        return 0

    guard_passed = False if args.no_guard else run_guard(newest)
    if args.no_guard:
        print(f"fastapi {newest} is past the pin ({pin.spec}).", file=sys.stderr)
        return 1
    print("", file=sys.stderr)
    print(verdict(newest, pin, guard_passed), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
