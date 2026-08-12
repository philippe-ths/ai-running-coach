"""The route enumeration every structural guard sweeps over (#809).

This file is the guard on the guards. `tests/_route_table.py` is the single
place the app's route table is walked, and several security sweeps -- every
`/api` route is session-gated, every owned-resource id is resolved through
`app.api.deps` -- are only as good as that walk. A sweep over an empty
enumeration passes silently, which is exactly how the session-gate fence went
green in CI while inspecting zero routes.

So the walk is tested directly, and its NEGATIVE paths are the point:

* it finds what the real app serves (checked against the app's own OpenAPI doc),
* it resolves dependencies attached at `include_router` time, which FastAPI
  0.141 stopped merging into each route's own dependant,
* and its anti-vacuity assertion actually fails when the enumeration comes back
  empty or short, rather than being an assertion nobody has ever seen fire.

The miniature apps below are TEST SETUP, not ground truth (trust level 5): they
exist to drive the walker's branches, and nothing about their shape is a claim
about the real app. The real app is asserted against separately, and its own
OpenAPI document is the oracle for what it serves.
"""

from unittest.mock import patch

import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.main import app
from tests import _route_table
from tests._route_table import (
    MIN_API_ROUTES,
    api_routes,
    assert_enumeration_is_not_vacuous,
    served_api_paths,
)


def _gate():  # a stand-in for a session dependency
    return None


def _other_gate():
    return None


def _mini_app(*, gate_at_include: bool) -> FastAPI:
    """A router included with or without an include-time dependency."""
    router = APIRouter()

    @router.get("/thing")
    def _read_thing():
        return {}

    @router.post("/thing/{thing_id}")
    def _write_thing(thing_id: str):
        return {}

    mini = FastAPI()
    mini.include_router(
        router,
        prefix="/api",
        dependencies=[Depends(_gate)] if gate_at_include else [],
    )
    return mini


# --- the walk sees what the app serves --------------------------------------


def test_enumeration_covers_every_path_the_real_app_serves():
    """The oracle is the app's own OpenAPI document, not a hand-kept list."""
    enumerated = {info.path for info in api_routes()}
    served = served_api_paths()
    assert served, "the app serves no /api paths at all"
    assert served <= enumerated, sorted(served - enumerated)


def test_enumeration_clears_the_floor_on_the_real_app():
    assert len(api_routes()) >= MIN_API_ROUTES


def test_every_enumerated_route_has_a_path_and_a_method():
    for info in api_routes():
        assert info.path.startswith("/api/")
        assert info.methods and all(isinstance(m, str) for m in info.methods)
        assert info.key == (info.methods[0], info.path)


# --- include-time dependencies are resolved ---------------------------------


def test_include_time_dependency_is_carried_into_gating_calls():
    """FastAPI 0.141 stopped merging include-time deps into route.dependant.

    A guard reading only `route.dependant` therefore reports a route gated at
    `include_router` time as UNGATED -- a false alarm on the newer version and,
    worse, the shape that invites somebody to "fix" it by weakening the guard.
    `gating_calls` is the union, so it holds on both router models.
    """
    mini = _mini_app(gate_at_include=True)
    routes = api_routes(mini)
    assert len(routes) == 2
    for info in routes:
        assert _gate in info.gating_calls, (
            f"{info.label()} is gated where its router is included, but the "
            "enumeration did not see it"
        )


def test_a_route_with_no_gate_reports_no_gate():
    """The negative control for the above: absence must be reported as absence."""
    mini = _mini_app(gate_at_include=False)
    routes = api_routes(mini)
    assert len(routes) == 2
    for info in routes:
        assert _gate not in info.gating_calls


def test_route_level_dependency_is_seen_too():
    mini = FastAPI()
    router = APIRouter()

    @router.get("/own", dependencies=[Depends(_other_gate)])
    def _own():
        return {}

    mini.include_router(router, prefix="/api")
    (info,) = api_routes(mini)
    assert _other_gate in info.gating_calls
    assert _gate not in info.gating_calls


def test_nested_routers_are_walked_through():
    inner = APIRouter()

    @inner.get("/deep")
    def _deep():
        return {}

    outer = APIRouter()
    outer.include_router(inner, prefix="/nest", dependencies=[Depends(_gate)])

    mini = FastAPI()
    mini.include_router(outer, prefix="/api", dependencies=[Depends(_other_gate)])

    (info,) = api_routes(mini)
    assert info.path == "/api/nest/deep"
    assert {_gate, _other_gate} <= info.gating_calls


# --- the anti-vacuity assertion has teeth -----------------------------------


def test_anti_vacuity_check_passes_on_the_real_app():
    assert len(assert_enumeration_is_not_vacuous()) >= MIN_API_ROUTES


def test_anti_vacuity_check_fails_when_the_app_serves_nothing():
    """An enumeration that comes back empty must be a FAILURE, not a green sweep."""
    with pytest.raises(AssertionError, match="serves no /api paths"):
        assert_enumeration_is_not_vacuous(FastAPI())


def test_anti_vacuity_check_fails_when_the_enumeration_is_short():
    """The #809 shape exactly: the app serves routes, the walk returns none."""
    with patch.object(_route_table, "api_routes", return_value=[]):
        with pytest.raises(AssertionError, match="below the floor"):
            assert_enumeration_is_not_vacuous()


def test_anti_vacuity_check_fails_when_a_served_path_is_invisible():
    """A partially blind walk is as dangerous as a wholly blind one."""
    full = api_routes()
    served = served_api_paths()
    dropped = sorted(served)[0]
    truncated = [info for info in full if info.path != dropped]
    with patch.object(_route_table, "api_routes", return_value=truncated):
        with pytest.raises(AssertionError, match="cannot see routes the app"):
            assert_enumeration_is_not_vacuous()


# --- local and CI resolve the same route model ------------------------------


def test_the_installed_fastapi_matches_the_declared_constraint():
    """Local, CI and the deploy must agree about FastAPI's router model (#809).

    The constraint in `backend/pyproject.toml` is not cosmetic: which router
    model is in play decides whether the sweeps in this suite see anything. It
    used to be unbounded, so a developer venv sat on 0.128 while CI resolved
    0.141 and the two runs checked different things. Reading the declared
    constraint here keeps one source of truth and turns a stale venv into a
    named failure instead of a divergence nobody notices.
    """
    import tomllib
    from pathlib import Path

    import fastapi
    from packaging.requirements import Requirement

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    declared = [
        Requirement(dep)
        for dep in tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    ]
    (spec,) = [r.specifier for r in declared if r.name == "fastapi"]
    assert str(spec), "fastapi must carry an explicit version constraint (#809)"
    assert fastapi.__version__ in spec, (
        f"installed fastapi {fastapi.__version__} does not satisfy the declared "
        f"'{spec}'. Reinstall the backend (pip install -e './backend[test]'); a "
        "venv on a different router model checks different things than CI does."
    )


def test_the_shared_walk_finds_at_least_what_the_naive_sweep_did():
    """Pin the mechanism itself so the regression stays legible.

    The pre-#809 expression -- filter `app.routes` for entries that carry a
    `dependant` -- returns NOTHING on FastAPI 0.141, because `include_router`
    now inserts an opaque wrapper, and everything on 0.128. The shared walk must
    never see less than it did, on either model.

    Deliberately `>=` rather than a strict `>`: which of the two holds depends
    on the installed version, and pinning the version-specific half here would
    duplicate what `test_the_installed_fastapi_matches_the_declared_constraint`
    already states in one place.
    """
    naive = [
        r
        for r in app.routes
        if getattr(r, "dependant", None) is not None
        and getattr(r, "path", "").startswith("/api/")
    ]
    assert len(api_routes()) >= len(naive)
    assert len(api_routes()) >= MIN_API_ROUTES
