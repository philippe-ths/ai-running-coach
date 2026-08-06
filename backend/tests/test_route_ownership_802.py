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

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute

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
}

# Every route below carries an owned-resource id in its BODY rather than its
# path, so the structural sweep cannot see it. Each is pinned behaviourally in
# this file instead; listing them here keeps the two halves honest about what
# the sweep does and does not cover.
BODY_CARRIED_OWNERSHIP = {
    ("POST", "/api/blocks/{block_id}/split"),
    ("POST", "/api/blocks/{block_id}/merge"),
    ("POST", "/api/coach/threads/messages"),
}


def _flat_dependency_calls(dependant):
    """Every callable in a route's resolved dependency tree."""
    calls = []
    for sub in dependant.dependencies:
        calls.append(sub.call)
        calls.extend(_flat_dependency_calls(sub))
    return calls


def _owned_routes():
    found = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for param, allowed in OWNED_PATH_PARAMS.items():
            if "{" + param + "}" in route.path:
                found.append((route, param, allowed))
    return found


# --- structural: the guarantee the refactor creates -------------------------


def test_owned_resource_routes_are_discovered():
    """The sweep below is only meaningful if it actually finds routes.

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


@pytest.mark.parametrize(
    "route,param,allowed",
    _owned_routes(),
    ids=lambda v: (
        f"{sorted(v.methods)[0]} {v.path}" if isinstance(v, APIRoute) else ""
    ),
)
def test_owned_resource_id_is_resolved_in_the_route_signature(route, param, allowed):
    """A route taking an owned-resource id must resolve it via app.api.deps.

    This is what stops the next route forgetting: the check is not something a
    handler body opts into, it is something the route declares. A new route that
    takes `{activity_id}` and queries the table itself fails here.
    """
    calls = set(_flat_dependency_calls(route.dependant))
    assert calls & allowed, (
        f"{sorted(route.methods)} {route.path} takes '{param}' but resolves no "
        f"owned-resource dependency. Declare one of "
        f"{sorted(f.__name__ for f in allowed)} in the signature instead of "
        f"fetching and checking the row in the handler body (#802)."
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
        in inspect.getclosurevars(threads.get_turn_targets).globals.values()
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
