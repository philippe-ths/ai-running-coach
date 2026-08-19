"""Ownership is a property of the ROUTE, not of the handler body (#802).

Two kinds of test live here.

**Structural.** Every route that takes a client-supplied owned-resource id
resolves it through `app.api.deps` before its body runs. This is the guarantee
the refactor exists to create: a route added later gets tenant scoping by
construction instead of having to remember it. The assertion is over the route's
resolved dependency tree, so it survives any rewrite of the handler body and
fails the moment a new route reaches for the id itself.

**Behavioural.** One cross-tenant denial test per owned resource type, exercised
through HTTP, plus the ordering invariants that would otherwise be invisible.
Anchored to the threat model — "A cannot reach B's row, and cannot tell whether
it exists" — not to any particular query.

The trends router is covered here too because its five routes had no
endpoint-level scoping test for two of them; their scoping is threaded as a
service argument that three of the five services still DEFAULT to None, so an
endpoint that forgot would leak silently rather than fail.

Fixtures are synthetic (trust level 5). They are test setup, not ground truth:
the claim under test is a denial rule over two arbitrary tenants, and the row
contents are irrelevant to it.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.api import deps
from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import (
    Activity,
    Block,
    CheckIn,
    DerivedMetric,
    StravaAccount,
    User,
    UserMaterial,
    UserProfile,
)
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport
from app.models.thread import Thread
from tests._route_table import (
    api_routes,
    assert_enumeration_is_not_vacuous,
    flat_body_params,
)

# A path parameter naming an owned resource -> the dependencies allowed to
# resolve it. More than one entry means the routes genuinely differ (a different
# load strategy or a different 404 detail), which stays explicit rather than
# being flattened onto a single default.
OWNED_PATH_PARAMS = {
    "activity_id": {
        deps.get_owned_activity,
        deps.get_owned_activity_detail,
        deps.get_owned_activity_with_metrics,
    },
    "block_id": {deps.get_owned_block},
    "thread_id": {deps.get_owned_thread},
    "material_id": {deps.get_owned_material},
    "race_id": {deps.get_owned_goal_race},
    "session_id": {deps.get_owned_planned_session},
    "plan_id": {deps.get_owned_training_plan},
}

# Every ownership resolver, whatever the resource. The broad sweep below accepts
# any of these; the narrow sweep insists on the right one for a known id name.
OWNERSHIP_DEPENDENCIES = {
    fn
    for name, fn in vars(deps).items()
    if callable(fn)
    and getattr(fn, "__module__", "") == "app.api.deps"
    and (name.startswith("get_owned_") or name.startswith("require_owned_"))
}

# DENY BY DEFAULT. A route that takes a path parameter is presumed to be
# addressing an owned row until it is listed here. Keeping this list empty is
# the goal; adding to it is a deliberate, reviewable act. Without this
# inversion the guard would only catch a route that happens to reuse one of the
# four id names above, and a new `/{run_id}` route could leak in silence.
PATH_PARAMS_THAT_ARE_NOT_OWNED_RESOURCES: set[tuple[str, str]] = set()

# Routes whose owned-resource id arrives in the request BODY, where no path
# sweep can see it. Asserted against the live route table below, and each is
# pinned behaviourally further down this file.
BODY_CARRIED_OWNERSHIP = {
    ("POST", "/api/blocks/{block_id}/split"),
    ("POST", "/api/blocks/{block_id}/merge"),
    ("POST", "/api/coach/threads/messages"),
}

# Body fields ending in `_id` that name something other than an owned row.
#
# The two webhook endpoints are the only entries, and they are a different kind
# of surface: they carry no session, so there is no authenticated runner to
# scope to. These ids belong to the UPSTREAM protocol, and each is already the
# input to that endpoint's own authenticity check rather than a tenant scope —
# Strava's `owner_id`/`subscription_id` are matched against a connected account
# and `STRAVA_WEBHOOK_SUBSCRIPTION_ID` by `_event_is_authentic`, and Telegram's
# `update_id` is a delivery sequence number on a route gated by the
# `X-Telegram-Bot-Api-Secret-Token` header. Resolving them through the
# owner-scoped dependencies would be wrong, not merely unnecessary.
BODY_ID_FIELDS_THAT_ARE_NOT_OWNED_RESOURCES: set[tuple[str, str, str]] = {
    ("POST", "/api/webhooks/strava", "object_id"),
    ("POST", "/api/webhooks/strava", "owner_id"),
    ("POST", "/api/webhooks/strava", "subscription_id"),
    ("POST", "/api/webhooks/telegram", "update_id"),
}


# The route walk itself lives in tests/_route_table.py so that every structural
# sweep in the suite -- this file's ownership guards and the session-gate fence
# in test_clerk_auth.py -- shares one enumeration and one anti-vacuity check.
# It was written here first, during #802, when this file's sweep returned 16
# routes locally and 0 in CI; #809 found the session-gate fence had the same
# hole and had been passing without inspecting a single route.
_api_routes = api_routes


def _methods(route):
    return list(route.methods)


_PATH_PARAM_RE = re.compile(r"\{([^}:]+)")


def _routes_with_path_params():
    """Every route whose PATH carries a client-supplied value.

    Read off the path string rather than the resolved dependant, so a route that
    declares its id nowhere in its own signature — the exact shape this guard
    exists to catch — is still discovered.
    """
    out = []
    for route in _api_routes():
        params = sorted(set(_PATH_PARAM_RE.findall(route.path)))
        if params:
            out.append((route, params))
    return out


def _routes_with_body():
    """Every route that accepts a request body, however it is declared."""
    return [r for r in _api_routes() if flat_body_params(r.dependant)]


def _owned_routes():
    found = []
    for route in _api_routes():
        for param, allowed in OWNED_PATH_PARAMS.items():
            if "{" + param + "}" in route.path:
                found.append((route, param, allowed))
    return found


def _route_key(route):
    return route.key  # (method, path), defined once on RouteInfo


# --- structural: the guarantee the refactor creates -------------------------


def test_route_enumeration_is_complete():
    """The sweeps must see every route the app actually serves.

    This is the load-bearing test in the file. Every guard below is a sweep over
    an enumeration of the route table, and a sweep over an EMPTY enumeration
    passes silently — it does not error, it just stops checking anything. FastAPI
    changed its internal router layout once already (0.141 wraps included routers
    instead of flattening them) and turned the obvious implementation into
    exactly that silent no-op.

    So the enumeration is checked against the public OpenAPI document, which is
    the app's own statement of what it serves. If a future version moves the
    furniture again, this fails loudly and the guards stay honest.

    The check itself now lives with the walk in `tests/_route_table.py` (#809),
    where the session-gate fence shares it; `tests/test_route_table.py` proves
    it actually fails on an empty or short enumeration.
    """
    assert_enumeration_is_not_vacuous()


def test_owned_resource_routes_are_discovered():
    """The sweeps below are only meaningful if they actually find routes.

    Without this, renaming a path parameter would turn the structural guard into
    a vacuous pass instead of a failure.
    """
    found = _owned_routes()
    assert len(found) >= 16, (
        f"expected the owned-resource routes to be discoverable, found {len(found)}: "
        f"{[r.path for r, _, _ in found]}"
    )
    covered = {param for _, param, _ in found}
    assert covered == set(OWNED_PATH_PARAMS), (
        f"some owned resource type has no route using its id: "
        f"{set(OWNED_PATH_PARAMS) - covered}"
    )
    assert len(_routes_with_path_params()) >= 16
    # The body sweeps must find routes too, and must find the declared ones.
    with_body = {_route_key(r) for r in _routes_with_body()}
    assert len(with_body) >= 8, f"body-carrying routes not discoverable: {with_body}"
    assert BODY_CARRIED_OWNERSHIP <= with_body, (
        f"the body sweep cannot see the declared body-carried routes: "
        f"{BODY_CARRIED_OWNERSHIP - with_body}"
    )
    # And the body-field reader must actually read fields, or the *_id sweep is
    # vacuous however many routes it iterates.
    fields = {
        name
        for r in _routes_with_body()
        for name in _body_model_fields(r)
    }
    assert "thread_id" in fields and "activity_id" in fields, (
        f"body model fields are not readable on this FastAPI version: {sorted(fields)}"
    )


@pytest.mark.parametrize(
    "route,param,allowed",
    _owned_routes(),
    ids=lambda v: (
        f"{_methods(v)[0]} {v.path}" if hasattr(v, "path") else ""
    ),
)
def test_owned_resource_id_is_resolved_in_the_route_signature(route, param, allowed):
    """A route taking a KNOWN owned-resource id must resolve THAT resource.

    This is what stops the next route forgetting: the check is not something a
    handler body opts into, it is something the route declares. A new route that
    takes `{activity_id}` and queries the table itself fails here — and a route
    that resolves the wrong resource type fails too.
    """
    calls = route.gating_calls
    assert calls & allowed, (
        f"{_methods(route)} {route.path} takes '{param}' but resolves no "
        f"owned-resource dependency. Declare one of "
        f"{sorted(f.__name__ for f in allowed)} in the signature instead of "
        f"fetching and checking the row in the handler body (#802)."
    )


@pytest.mark.parametrize(
    "route,params",
    _routes_with_path_params(),
    ids=lambda v: (
        f"{_methods(v)[0]} {v.path}" if hasattr(v, "path") else ""
    ),
)
def test_any_path_parameter_route_resolves_ownership(route, params):
    """Deny by default: ANY path parameter is presumed to address an owned row.

    The narrow sweep above keys off four known id names, so a route that called
    its parameter something else — `{run_id}`, `{report_id}` — would sail past
    it while leaking another tenant's row. This sweep has no name list: a new
    parameterised route must either resolve ownership through `app.api.deps` or
    be listed, deliberately, in PATH_PARAMS_THAT_ARE_NOT_OWNED_RESOURCES.
    """
    if _route_key(route) in PATH_PARAMS_THAT_ARE_NOT_OWNED_RESOURCES:
        pytest.skip("explicitly declared as not addressing an owned resource")
    calls = route.gating_calls
    assert calls & OWNERSHIP_DEPENDENCIES, (
        f"{_methods(route)} {route.path} takes path parameter(s) {params} "
        "but resolves no ownership dependency from app.api.deps. Either declare "
        "one in the signature (#802), or — if the parameter genuinely names "
        "nothing a runner owns — add this route to "
        "PATH_PARAMS_THAT_ARE_NOT_OWNED_RESOURCES with a reason."
    )


def _body_model_fields(route):
    """The declared field names of every model this route accepts as a body.

    Reads the dependency tree rather than `route.body_field`, and takes the
    annotation from whichever attribute the installed FastAPI exposes, so the
    sweep does not go quietly vacuous on a version bump.
    """
    names = {}
    for param in flat_body_params(route.dependant):
        model = getattr(param, "type_", None)
        if model is None:
            model = getattr(getattr(param, "field_info", None), "annotation", None)
        names.update(getattr(model, "model_fields", None) or {})
    return names


@pytest.mark.parametrize(
    "route",
    _routes_with_body(),
    ids=lambda v: f"{_methods(v)[0]} {v.path}",
)
def test_body_carried_resource_ids_are_declared(route):
    """Deny by default for ids that arrive in the BODY.

    No structural sweep can tell whether a body field named `*_id` addresses an
    owned row, so the rule is declaration: a route whose payload carries one is
    listed in BODY_CARRIED_OWNERSHIP (and pinned behaviourally in this file), or
    the field is named in BODY_ID_FIELDS_THAT_ARE_NOT_OWNED_RESOURCES. Adding a
    new `*_id` to a request schema fails here until somebody decides which.
    """
    key = _route_key(route)
    for name in _body_model_fields(route):
        if not name.endswith("_id"):
            continue
        if (key[0], key[1], name) in BODY_ID_FIELDS_THAT_ARE_NOT_OWNED_RESOURCES:
            continue
        assert key in BODY_CARRIED_OWNERSHIP, (
            f"{key[0]} {key[1]} accepts body field '{name}', which looks like a "
            "client-supplied resource id, but the route is not declared in "
            "BODY_CARRIED_OWNERSHIP. Resolve it through app.api.deps and add it "
            "there with a cross-tenant test, or declare it in "
            "BODY_ID_FIELDS_THAT_ARE_NOT_OWNED_RESOURCES (#802)."
        )


@pytest.mark.parametrize(
    "route",
    _routes_with_body(),
    ids=lambda v: f"{_methods(v)[0]} {v.path}",
)
def test_no_route_parses_its_body_twice(route):
    """A payload must be validated once, however many dependants want it.

    Moving an ownership check into a dependency tempts you to declare the body
    in both the dependency and the handler. FastAPI counts body params by NAME,
    so the OpenAPI schema stays clean and nothing looks wrong — but the payload
    is parsed once per dependant and the runner gets every 422 entry repeated.
    The fix is for the dependency to carry the validated body through; this
    guard is what makes the mistake visible instead of silent.
    """
    body_params = flat_body_params(route.dependant)
    counts = {}
    for param in body_params:
        counts[param.name] = counts.get(param.name, 0) + 1
    repeated = {name: n for name, n in counts.items() if n > 1}
    assert not repeated, (
        f"{_methods(route)} {route.path} declares {repeated} more than "
        "once across its dependency tree, so the payload is validated that many "
        "times and every 422 entry is duplicated. Have the dependency return the "
        "validated body instead of declaring it in both places (#802)."
    )


def test_declared_body_carried_routes_still_exist():
    """The declaration list must track the route table, not drift from it."""
    live = {_route_key(r) for r in _api_routes()}
    assert BODY_CARRIED_OWNERSHIP <= live, (
        f"BODY_CARRIED_OWNERSHIP names routes that no longer exist: "
        f"{BODY_CARRIED_OWNERSHIP - live}"
    )


def test_ownership_dependencies_live_in_exactly_one_module():
    """One definition per owned resource type.

    Before #802 the same rule was written four times across five routers, two of
    them byte-identical bodies. A second home for it is how the copies drift.
    """
    for allowed in OWNED_PATH_PARAMS.values():
        for fn in allowed:
            assert fn.__module__ == "app.api.deps", (
                f"{fn.__name__} is defined in {fn.__module__}; ownership rules "
                "belong in app.api.deps so there is one definition to change."
            )


def test_body_carried_ownership_routes_reuse_the_shared_resolvers():
    """The body-carried resolvers must not re-implement the rule.

    `blocks.split` names its activity, `blocks.merge` its other block, and the
    thread turn its thread — all in the request body, where the path sweep above
    cannot reach. They still go through the same resolvers, so a change to the
    denial rule lands on them too.
    """
    import inspect

    from app.api import blocks, threads

    assert (
        deps.require_owned_activity
        in inspect.getclosurevars(blocks.get_split_target_activity).globals.values()
    ), "split's body-carried activity must resolve through deps.require_owned_activity"
    assert (
        deps.require_owned_block
        in inspect.getclosurevars(blocks.get_merge_other_block).globals.values()
    ), "merge's body-carried block must resolve through deps.require_owned_block"
    assert (
        deps.require_owned_thread
        in inspect.getclosurevars(threads.get_thread_turn).globals.values()
    ), "the thread turn's body-carried thread must resolve through deps.require_owned_thread"


# --- behavioural: one cross-tenant denial per owned resource type -----------


def _seed_tenant(db, *, email, athlete_id, strava_activity_id, atype, hash_suffix):
    """One tenant with a row of every owned type."""
    user = User(email=email)
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=athlete_id, access_token="test-token-do-not-use-in-prod",
        refresh_token="test-refresh-do-not-use-in-prod", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=strava_activity_id,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type=atype, name=f"{atype} run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.add(CoachReport(
        activity_id=activity.id,
        report={"message": "Nice run.", "headline": "ok", "next_steps": [],
                "risks": [], "questions": []},
        meta={"confidence": "medium", "model_id": "m", "prompt_id": "x",
              "schema_version": "2.0", "input_hash": "x",
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "policy_violations": []},
        context_pack={}, prompt_id="x", schema_version="2.0", is_fallback=False,
    ))
    db.add(CoachChatMessage(activity_id=activity.id, role="user", content="hi"))
    block = Block(
        user_id=user.id, start_date=activity.start_date,
        end_date=activity.start_date + timedelta(seconds=1500),
        primary_activity_id=activity.id,
    )
    db.add(block)
    material = UserMaterial(
        user_id=user.id, kind="other", title="mine", filename="m.md",
        raw_text="private", content_hash=f"hash-{hash_suffix}", status="active",
    )
    db.add(material)
    thread = Thread(user_id=user.id, activity_id=activity.id, title="t")
    db.add(thread)
    db.commit()
    for row in (activity, block, material, thread):
        db.refresh(row)
    return SimpleNamespace(
        user=user, activity=activity, block=block, material=material, thread=thread
    )


@pytest.fixture
def tenants(db):
    a = _seed_tenant(db, email="a@own.dev", athlete_id=811, strava_activity_id=8101,
                     atype="Run", hash_suffix="a")
    b = _seed_tenant(db, email="b@own.dev", athlete_id=822, strava_activity_id=8202,
                     atype="Ride", hash_suffix="b")
    yield a, b


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


# Every route reachable with another tenant's id, by owned resource type. One
# entry per (resource type, route) so the denial is proven at each door, but the
# expectation is written once per type rather than repeated per route.
ACTIVITY_ROUTES = [
    ("POST", "/api/activities/{id}/process_deep", None),
    ("PUT", "/api/activities/{id}/intent", {"user_intent": "easy"}),
    ("POST", "/api/activities/{id}/checkin", {"rpe": 5}),
    ("GET", "/api/activities/{id}", None),
    ("GET", "/api/activities/{id}/coach-report?generate=false", None),
    ("GET", "/api/activities/{id}/coach-chat", None),
    ("DELETE", "/api/activities/{id}/coach-chat", None),
]

MATERIAL_ROUTES = [
    ("GET", "/api/coach/materials/{id}", None),
    ("POST", "/api/coach/materials/{id}/archive", None),
    ("DELETE", "/api/coach/materials/{id}", None),
]

THREAD_ROUTES = [
    ("GET", "/api/coach/threads/{id}", None),
    ("PATCH", "/api/coach/threads/{id}", {"title": "x"}),
    ("DELETE", "/api/coach/threads/{id}", None),
]


def _call(client, method, path, body):
    fn = getattr(client, method.lower())
    return fn(path, json=body) if body is not None else fn(path)


@pytest.mark.parametrize("method,template,body", ACTIVITY_ROUTES)
def test_another_tenants_activity_is_denied(client, tenants, method, template, body):
    a, b = tenants
    _act_as(a.user)
    resp = _call(client, method, template.format(id=b.activity.id), body)
    assert resp.status_code == 404, (
        f"{method} {template} served another tenant's activity "
        f"({resp.status_code})"
    )
    assert resp.json()["detail"] == "Activity not found"


@pytest.mark.parametrize("method,template,body", ACTIVITY_ROUTES)
def test_a_missing_activity_is_indistinguishable_from_another_tenants(
    client, tenants, method, template, body
):
    """The denial must not double as an existence oracle.

    If a cross-tenant id 404'd differently from an unknown id, a caller could
    enumerate which activity ids exist on the deployment.
    """
    a, b = tenants
    _act_as(a.user)
    foreign = _call(client, method, template.format(id=b.activity.id), body)
    unknown = _call(client, method, template.format(id=uuid4()), body)
    assert (foreign.status_code, foreign.json()) == (
        unknown.status_code,
        unknown.json(),
    ), f"{method} {template} distinguishes a foreign id from an unknown one"


@pytest.mark.parametrize("method,template,body", MATERIAL_ROUTES)
def test_another_tenants_material_is_denied(client, tenants, method, template, body):
    a, b = tenants
    _act_as(a.user)
    resp = _call(client, method, template.format(id=b.material.id), body)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Material not found."


@pytest.mark.parametrize("method,template,body", THREAD_ROUTES)
def test_another_tenants_thread_is_denied(client, tenants, method, template, body):
    a, b = tenants
    _act_as(a.user)
    resp = _call(client, method, template.format(id=b.thread.id), body)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Thread not found"


def test_another_tenants_block_is_denied_on_split(client, tenants):
    a, b = tenants
    _act_as(a.user)
    resp = client.post(
        f"/api/blocks/{b.block.id}/split", json={"activity_id": str(b.activity.id)}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Block not found"


def test_another_tenants_block_is_denied_on_merge(client, tenants):
    a, b = tenants
    _act_as(a.user)
    resp = client.post(
        f"/api/blocks/{a.block.id}/merge", json={"other_block_id": str(b.block.id)}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Block not found"


def test_regenerate_keeps_its_own_denial_wording(client, tenants):
    """The regenerate route answers one 404 for BOTH "not yours" and "not
    analysed yet", deliberately: the two must stay indistinguishable, so it
    carries its own detail rather than the generic one."""
    a, b = tenants
    _act_as(a.user)
    fake_queue = MagicMock()
    with patch("app.core.queue.queue", fake_queue):
        resp = client.post(
            f"/api/activities/{b.activity.id}/coach-report/regenerate"
        )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Activity not found or metrics not yet computed."
    fake_queue.enqueue.assert_not_called()


def test_denied_write_leaves_the_other_tenants_row_untouched(client, tenants, db):
    """A denial must be a denial, not a 404 after the write landed."""
    a, b = tenants
    _act_as(a.user)
    client.put(f"/api/activities/{b.activity.id}/intent", json={"user_intent": "hard"})
    client.post(f"/api/activities/{b.activity.id}/checkin", json={"rpe": 9})
    client.delete(f"/api/coach/materials/{b.material.id}")
    client.delete(f"/api/coach/threads/{b.thread.id}")

    db.refresh(b.activity)
    assert b.activity.user_intent != "hard"
    assert db.query(CheckIn).filter(CheckIn.activity_id == b.activity.id).count() == 0
    assert db.query(UserMaterial).filter(UserMaterial.id == b.material.id).count() == 1
    assert db.query(Thread).filter(Thread.id == b.thread.id).count() == 1


@pytest.mark.parametrize(
    "template",
    [
        "/api/activities/{id}",
        "/api/coach/threads/{id}",
        "/api/coach/materials/{id}",
    ],
)
@pytest.mark.parametrize("bad", ["not-a-uuid", "1", "%00", "' OR 1=1 --"])
def test_malformed_resource_ids_are_rejected_not_resolved(
    client, tenants, template, bad
):
    """A malformed id must be refused by validation, never reach a query.

    422 (or 404 from routing) — never a 500, and never a 200 that would mean the
    id was coerced into something that matched a row.
    """
    a, _ = tenants
    _act_as(a.user)
    resp = client.get(template.format(id=bad))
    assert resp.status_code in (404, 422), (
        f"{template} with id {bad!r} returned {resp.status_code}"
    )


# --- ordering invariants that only show up in combination -------------------


def test_thread_turn_ignores_an_anchor_it_would_not_use(client, tenants):
    """A turn that names an owned thread must NOT validate the anchor.

    The anchor is only consulted when no thread was named, so a client that
    always sends both must not start 404-ing when the anchor is stale or
    foreign. Pinned because moving the resolution into a dependency is exactly
    where that order could quietly change.
    """
    a, b = tenants
    _act_as(a.user)

    async def _no_events(*args, **kwargs):
        if False:
            yield None

    import app.services.coach.thread_turn as thread_turn

    with patch.object(thread_turn, "stream_thread_turn", _no_events):
        resp = client.post(
            "/api/coach/threads/messages",
            json={
                "message": "hi",
                "thread_id": str(a.thread.id),
                "anchor_activity_id": str(b.activity.id),
            },
        )
    assert resp.status_code == 200, (
        "naming an owned thread must not make a foreign anchor fatal"
    )


def test_thread_turn_denies_a_foreign_anchor_when_no_thread_is_named(client, tenants):
    a, b = tenants
    _act_as(a.user)
    resp = client.post(
        "/api/coach/threads/messages",
        json={"message": "hi", "anchor_activity_id": str(b.activity.id)},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Activity not found"


def test_a_malformed_turn_reports_each_problem_once(client, tenants):
    """The turn's payload must be validated once, not once per dependant.

    The thread turn resolves its owned rows in a dependency that also needs the
    body. Declaring `ThreadMessageSend` in BOTH the dependency and the handler
    leaves the OpenAPI schema intact — FastAPI counts body params by name — but
    parses and validates the payload twice, so the runner got every 422 entry
    duplicated. The dependency carries the validated body through instead.
    """
    a, _ = tenants
    _act_as(a.user)
    resp = client.post("/api/coach/threads/messages", json={})
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert len(locs) == len(set(locs)), (
        f"the turn body was validated more than once: {locs}"
    )


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/blocks/{block_id}/split", {}),
        ("POST", "/api/blocks/{block_id}/merge", {}),
        ("PUT", "/api/activities/{activity_id}/intent", {}),
    ],
)
def test_ownership_is_decided_before_the_payload_is_judged(
    client, tenants, method, path, body
):
    """A caller who names a row they do not own is told 404, whatever they sent.

    DELIBERATE CHANGE (#802): a dependency resolves before the handler's own
    parameters, so a request that is BOTH aimed at an unowned/unknown row AND
    malformed now gets the 404 rather than the 422 it used to get. It is the
    narrower answer of the two: someone with no claim to the row learns nothing
    about the schema. Pinned so it stays an intended property rather than an
    accident of parameter order.
    """
    a, b = tenants
    _act_as(a.user)
    target = b.block.id if "block_id" in path else b.activity.id
    resp = _call(client, method, path.replace("{block_id}", str(target)).replace(
        "{activity_id}", str(target)), body)
    assert resp.status_code == 404, (
        f"{method} {path} with a foreign id and a malformed body returned "
        f"{resp.status_code}; ownership must be decided first"
    )


def test_import_reports_a_bad_date_before_a_missing_account(client, db):
    """Payload validation still precedes account resolution.

    A caller with no linked Strava account who posts a future date has always
    been told about the date (400), not about the account (404). Dependencies
    resolve before the body, so this order is only preserved by where the
    validation dependency sits in the signature.
    """
    caller = User(email="nostrava@own.dev")
    db.add(caller)
    db.commit()
    _act_as(caller)

    resp = client.post("/api/strava/import", json={"since_date": "2099-01-01"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "since_date cannot be in the future."


def test_import_reports_a_missing_account_when_the_date_is_valid(client, db):
    caller = User(email="nostrava2@own.dev")
    db.add(caller)
    db.commit()
    _act_as(caller)

    resp = client.post("/api/strava/import", json={"since_date": "2026-01-01"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == (
        "No linked Strava account found. Connect Strava first."
    )


def test_refresh_without_an_account_is_a_clean_no_op_not_a_404(client, db):
    """The self-heal refresh answers 200 no_account, unlike sync and import.

    Its account dependency is deliberately the optional one; flattening the two
    onto a single "linked account" rule would turn an app-open into a 404.
    """
    caller = User(email="nostrava3@own.dev")
    db.add(caller)
    db.commit()
    _act_as(caller)

    resp = client.post("/api/activities/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_account"}


# --- trends: endpoint-level scoping coverage --------------------------------
#
# The trends services take user_id as a keyword argument, and three of the five
# still DEFAULT it to None (get_available_types, get_weekly_stats,
# get_trends_report). A route that forgot to pass it would return every tenant's
# data with no error, so the scoping is pinned here at the endpoint boundary.


def _analysed_activity(db, user_id, *, days_ago, distance_m, atype="Run", name="x"):
    on = date.today() - timedelta(days=days_ago)
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        type=atype, name=name, distance_m=distance_m,
        moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=0.0, raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(DerivedMetric(
        activity_id=activity.id, effort_score=50.0, flags=[],
        confidence="high", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def test_trends_types_excludes_another_tenants_activity_types(client, tenants):
    a, _ = tenants
    _act_as(a.user)
    types = client.get("/api/trends/types").json()
    assert "Run" in types
    assert "Ride" not in types, "B's Ride type leaked into A's filter list"


def test_trends_report_excludes_another_tenants_distance(client, tenants, db):
    a, b = tenants
    _analysed_activity(db, a.user.id, days_ago=1, distance_m=3000, name="A only")
    _analysed_activity(db, b.user.id, days_ago=1, distance_m=90000, name="B only")

    _act_as(a.user)
    report = client.get("/api/trends?range=30D").json()
    assert report["summary"]["total_distance_m"] == 3000, (
        "B's 90km folded into A's trends totals"
    )
    assert report["summary"]["activity_count"] == 1, (
        "B's activity was counted in A's trends report"
    )
    daily = sum(p["total_distance_m"] for p in report["daily_distance"])
    assert daily == 3000, "B's distance appeared in A's daily distance series"


def test_trends_load_excludes_another_tenants_contributions(client, tenants, db):
    a, b = tenants
    a_act = _analysed_activity(db, a.user.id, days_ago=2, distance_m=1000, name="A only")
    b_act = _analysed_activity(db, b.user.id, days_ago=2, distance_m=9999, name="B only")

    _act_as(a.user)
    weeks = client.get("/api/trends/load").json()["weeks"]
    ids = {pt["id"] for w in weeks for pt in w["activities"]}
    assert str(a_act.id) in ids
    assert str(b_act.id) not in ids, "B's activity appeared in A's load report"


def test_trends_volume_excludes_another_tenants_distance(client, tenants, db):
    a, b = tenants
    _analysed_activity(db, a.user.id, days_ago=1, distance_m=3000, name="A only")
    _analysed_activity(db, b.user.id, days_ago=1, distance_m=80000, name="B only")

    _act_as(a.user)
    report = client.get("/api/trends/volume?range=7D").json()
    distance = next(
        m for m in report["rolling"]["metrics"] if m["metric"] == "distance_m"
    )
    assert distance["current_all"] == 3000, "B's 80km inflated A's volume window"


def test_weekly_stats_excludes_another_tenants_distance(client, tenants, db):
    a, b = tenants
    _analysed_activity(db, a.user.id, days_ago=1, distance_m=3000, name="A only")
    _analysed_activity(db, b.user.id, days_ago=1, distance_m=80000, name="B only")

    _act_as(a.user)
    stats = client.get("/api/stats/weekly").json()
    assert stats["summary"]["total_distance_m"] == 3000, (
        "B's 80km folded into A's weekly dashboard stats"
    )
    assert stats["summary"]["activity_count"] == 1, (
        "B's activity was counted in A's weekly dashboard stats"
    )
