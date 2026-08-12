"""One enumeration of the app's route table, shared by every structural guard.

Several tests assert a property over EVERY route the app serves: that each one
is session-gated (`test_clerk_auth.py`), that each one taking an owned-resource
id resolves it through `app.api.deps` (`test_route_ownership_802.py`). Each such
test is a sweep, and a sweep over an EMPTY enumeration passes silently. It does
not error. It just stops checking anything, while still reporting green.

FastAPI has already turned the obvious implementation into exactly that no-op
once:

* Up to ~0.13x, `include_router` FLATTENED the included routes into
  `app.routes`, so `[r for r in app.routes if hasattr(r, "dependant")]` found
  every one of them, with the include-time dependencies merged into each
  route's own `dependant`.
* From 0.141, `app.routes` carries an opaque `_IncludedRouter` wrapper instead.
  The real routes live behind `original_router`, the include-time prefix and
  dependencies on `include_context`. The old expression matches NOTHING.

Both halves of that change matter, and they fail in opposite directions:

1. A sweep that only looks at top-level `app.routes` entries with a `dependant`
   sees zero routes on 0.141 and passes vacuously (issue #809).
2. A sweep that resolves the wrapper but reads only `route.dependant` sees a
   route gated at `include_router` time as UNGATED on 0.141, because the
   include-time dependency is no longer merged into the route's own tree.

So this module resolves the wrapper AND carries the include-time dependencies
alongside each route, and `assert_enumeration_is_not_vacuous` proves the result
against the app's own OpenAPI document rather than trusting it. Every sweep in
the suite goes through here, so a third route model arriving in a future FastAPI
fails one loud test instead of quietly disarming several guards.
"""

from typing import NamedTuple

from app.main import app as _app

# A hard floor beneath the OpenAPI cross-check. The cross-check is the real
# guarantee (it is exact and maintains itself), but it is derived from the same
# app object; if `app.openapi()` ever went empty, `served <= enumerated` would
# hold trivially. This number is well under the ~50 paths the app serves today
# and is only ever raised deliberately.
MIN_API_ROUTES = 40


class RouteInfo(NamedTuple):
    """One resolved route: its full URL path, and everything gating it.

    `inherited` holds the dependencies attached at `include_router` time. From
    FastAPI 0.141 those live on the included router rather than on each route's
    own dependant, so a guard reading only `route.dependant` would report a
    route as unguarded when its router guards it.
    """

    path: str
    methods: tuple
    route: object
    inherited: tuple = ()

    @property
    def dependant(self):
        return self.route.dependant

    @property
    def key(self):
        return (self.methods[0], self.path)

    @property
    def gating_calls(self) -> set:
        """Every callable that runs before this route's body, however attached.

        The union of the route's own resolved dependency tree and the
        dependencies attached where its router was included. Either placement
        gates the route, and which one FastAPI uses is a version detail.
        """
        return set(flat_dependency_calls(self.dependant)) | set(self.inherited)

    def label(self) -> str:
        return f"{self.methods[0]} {self.path}"


def flat_dependency_calls(dependant) -> list:
    """Every callable in a route's resolved dependency tree, to any depth."""
    calls = []
    for sub in getattr(dependant, "dependencies", None) or []:
        if getattr(sub, "call", None) is not None:
            calls.append(sub.call)
        calls.extend(flat_dependency_calls(sub))
    return calls


def flat_body_params(dependant) -> list:
    """Every body parameter in a route's dependency tree, including nested ones."""
    params = list(getattr(dependant, "body_params", None) or [])
    for sub in getattr(dependant, "dependencies", None) or []:
        params.extend(flat_body_params(sub))
    return params


def walk_routes(routes, prefix: str, out: list, inherited: tuple = ()) -> None:
    """Collect every route with a dependency tree, whatever the router shape.

    Handles both router models described in this module's docstring. Anything
    with a `dependant` and a `path` is a real route; anything exposing an
    `original_router` is an include wrapper to descend through, carrying its
    prefix and its include-time dependencies down; anything else with `routes`
    but no `path` of its own is a plain mount.
    """
    for route in routes:
        if getattr(route, "dependant", None) is not None and hasattr(route, "path"):
            methods = tuple(sorted(getattr(route, "methods", None) or ["GET"]))
            out.append(RouteInfo(prefix + route.path, methods, route, inherited))
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            ctx = getattr(route, "include_context", None)
            attached = tuple(
                d.dependency for d in (getattr(ctx, "dependencies", None) or [])
            )
            walk_routes(
                inner.routes,
                prefix + (getattr(ctx, "prefix", "") or ""),
                out,
                inherited + attached,
            )
        elif hasattr(route, "routes") and not hasattr(route, "path"):
            walk_routes(route.routes, prefix, out, inherited)


def all_routes(application=None) -> list:
    """Every resolved route on the app, regardless of prefix."""
    out: list = []
    walk_routes((application or _app).routes, "", out)
    return out


def api_routes(application=None) -> list:
    """Every resolved `/api/` route on the app."""
    return [info for info in all_routes(application) if info.path.startswith("/api/")]


def served_api_paths(application=None) -> set:
    """The `/api/` paths the app itself says it serves, per its OpenAPI document."""
    return {
        path
        for path in (application or _app).openapi()["paths"]
        if path.startswith("/api/")
    }


def assert_enumeration_is_not_vacuous(application=None) -> list:
    """Fail loudly if the enumeration cannot see what the app actually serves.

    This is the anti-vacuity check every route sweep depends on: an empty (or
    merely incomplete) enumeration turns a sweep into a silent no-op, which is
    what issue #809 was. Returns the enumeration so a caller can use it.
    """
    enumerated = api_routes(application)
    served = served_api_paths(application)
    assert served, "the app serves no /api paths at all -- something is very wrong"
    assert len(enumerated) >= MIN_API_ROUTES, (
        f"route enumeration found only {len(enumerated)} /api routes, below the "
        f"floor of {MIN_API_ROUTES}. Either the app shrank drastically or the "
        "enumeration in tests/_route_table.py has stopped seeing this FastAPI "
        "version's router layout, which would make every route sweep vacuous "
        "(#809)."
    )
    missing = sorted(served - {info.path for info in enumerated})
    assert not missing, (
        "the route sweeps cannot see routes the app actually serves, so they "
        f"would pass without checking them: {missing}"
    )
    return enumerated
