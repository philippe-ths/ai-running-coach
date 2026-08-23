"""#946: the period-report API — the async lifecycle, its two idempotency
guards, ownership, and the kill switch.

NO TEST HERE MAY REACH THE NETWORK OR REDIS: the enqueue seam is replaced.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import User
from app.models.period_report import PeriodReport
from app.services.coach import period_report_store as store
from app.services.coach.period_report import PROMPT_ID, SCHEMA_VERSION


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


@pytest.fixture(autouse=True)
def _no_real_enqueue(monkeypatch):
    monkeypatch.setattr(
        "app.api.period_reports.enqueue_period_report", lambda user_id, report_id: None
    )


def _seed_user(db) -> User:
    user = User(email=f"period-report-api-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


TODAY = date(2026, 8, 10)


def _body(start=TODAY, end=None, disciplines=None):
    return {
        "period_start": start.isoformat(),
        "period_end": (end or start + timedelta(days=6)).isoformat(),
        "disciplines": disciplines or [],
    }


# --- the kill switch ---------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/coach/period-reports"),
        ("get", "/api/coach/period-reports"),
    ],
)
def test_routes_refuse_while_the_surface_is_switched_off(db, client, monkeypatch, method, path):
    _act_as(_seed_user(db))
    monkeypatch.setattr(settings, "COACH_PERIOD_REPORT_ENABLED", False)

    kwargs = {"json": _body()} if method == "post" else {}
    resp = getattr(client, method)(path, **kwargs)

    assert resp.status_code == 503


def test_the_detail_route_also_refuses_while_switched_off(db, client, monkeypatch):
    user = _seed_user(db)
    _act_as(user)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    monkeypatch.setattr(settings, "COACH_PERIOD_REPORT_ENABLED", False)

    resp = client.get(f"/api/coach/period-reports/{report.id}")

    assert resp.status_code == 503


# --- the async lifecycle ------------------------------------------------------


def test_asking_for_a_report_returns_at_once_and_leaves_a_row_to_poll(db, client, monkeypatch):
    user = _seed_user(db)
    _act_as(user)
    enqueued = []
    monkeypatch.setattr(
        "app.api.period_reports.enqueue_period_report",
        lambda user_id, report_id: enqueued.append((user_id, report_id)),
    )

    resp = client.post("/api/coach/period-reports", json=_body())

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "generating"
    stored = db.query(PeriodReport).one()
    assert stored.id == UUID(body["id"])
    assert stored.user_id == user.id
    assert stored.status == store.GENERATING
    assert enqueued == [(user.id, stored.id)]


def test_polling_after_the_worker_marks_it_ready_returns_the_report(db, client):
    user = _seed_user(db)
    _act_as(user)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY + timedelta(days=6),
        disciplines=[], prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    store.mark_ready(
        db, report,
        content={"message": "A solid block.", "headline": "Solid", "next_steps": []},
        context_pack={}, model_id="claude-x",
    )

    resp = client.get(f"/api/coach/period-reports/{report.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["report"]["message"] == "A solid block."


def test_polling_after_a_failure_returns_a_runner_facing_message_not_the_internal_one(db, client):
    user = _seed_user(db)
    _act_as(user)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    store.mark_failed(db, report, "internal gate text nobody should see", kind=store.FAILURE_OVER_BUDGET)

    resp = client.get(f"/api/coach/period-reports/{report.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "internal gate text" not in body["message"]
    assert "allowance" in body["message"]


def test_a_malformed_stored_ready_row_reads_as_failed_not_a_blank_report(db, client):
    """Display-safe degradation: the stored row stays `ready`, but a runner
    reading the API response sees a clear failure rather than a "ready" report
    with no content and no explanation."""
    user = _seed_user(db)
    _act_as(user)
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    store.mark_ready(
        db, report, content={"not": "the right shape"}, context_pack={}, model_id="m",
    )

    resp = client.get(f"/api/coach/period-reports/{report.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["report"] is None
    db.refresh(report)
    assert report.status == store.READY  # the stored row itself is untouched


# --- idempotency ---------------------------------------------------------------


def test_a_second_identical_post_returns_the_in_flight_row_not_a_new_one(db, client, monkeypatch):
    _act_as(_seed_user(db))
    enqueued = []
    monkeypatch.setattr(
        "app.api.period_reports.enqueue_period_report",
        lambda user_id, report_id: enqueued.append((user_id, report_id)),
    )

    first = client.post("/api/coach/period-reports", json=_body())
    second = client.post("/api/coach/period-reports", json=_body())

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json()["id"] == second.json()["id"]
    assert len(enqueued) == 1


def test_a_second_identical_post_after_ready_returns_the_cached_report(db, client, monkeypatch):
    user = _seed_user(db)
    _act_as(user)
    enqueued = []
    monkeypatch.setattr(
        "app.api.period_reports.enqueue_period_report",
        lambda user_id, report_id: enqueued.append((user_id, report_id)),
    )
    report = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY + timedelta(days=6),
        disciplines=[], prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    store.mark_ready(
        db, report, content={"message": "cached", "headline": "h", "next_steps": []},
        context_pack={}, model_id="m",
    )

    resp = client.post("/api/coach/period-reports", json=_body())

    assert resp.status_code == 202
    assert resp.json()["id"] == str(report.id)
    assert resp.json()["status"] == "ready"
    assert enqueued == []  # no new generation was started


def test_a_different_period_is_not_treated_as_in_flight(db, client, monkeypatch):
    _act_as(_seed_user(db))
    enqueued = []
    monkeypatch.setattr(
        "app.api.period_reports.enqueue_period_report",
        lambda user_id, report_id: enqueued.append((user_id, report_id)),
    )

    first = client.post("/api/coach/period-reports", json=_body(start=TODAY))
    second = client.post(
        "/api/coach/period-reports", json=_body(start=TODAY + timedelta(days=30))
    )

    assert first.json()["id"] != second.json()["id"]
    assert len(enqueued) == 2


def test_a_stale_generating_row_does_not_block_a_retry(db, client, monkeypatch):
    user = _seed_user(db)
    _act_as(user)
    enqueued = []
    monkeypatch.setattr(
        "app.api.period_reports.enqueue_period_report",
        lambda user_id, report_id: enqueued.append((user_id, report_id)),
    )
    stale = store.create_generating_report(
        db, user.id, period_start=TODAY, period_end=TODAY + timedelta(days=6),
        disciplines=[], prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    stale.created_at = datetime.now(timezone.utc) - store.STALE_AFTER - timedelta(minutes=1)
    db.commit()

    resp = client.post("/api/coach/period-reports", json=_body())

    assert resp.status_code == 202
    assert resp.json()["id"] != str(stale.id)
    assert len(enqueued) == 1
    db.refresh(stale)
    assert stale.status == store.FAILED


# --- ownership -----------------------------------------------------------------


def test_another_tenants_report_is_denied(db, client):
    owner = _seed_user(db)
    other = _seed_user(db)
    report = store.create_generating_report(
        db, owner.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    _act_as(other)

    resp = client.get(f"/api/coach/period-reports/{report.id}")

    assert resp.status_code == 404


def test_a_missing_report_is_indistinguishable_from_another_tenants(db, client):
    owner = _seed_user(db)
    other = _seed_user(db)
    report = store.create_generating_report(
        db, owner.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    _act_as(other)
    cross_tenant = client.get(f"/api/coach/period-reports/{report.id}")
    missing = client.get(f"/api/coach/period-reports/{uuid4()}")

    assert cross_tenant.status_code == missing.status_code == 404
    assert cross_tenant.json() == missing.json()


def test_list_is_tenant_scoped(db, client):
    a = _seed_user(db)
    b = _seed_user(db)
    store.create_generating_report(
        db, a.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    store.create_generating_report(
        db, b.id, period_start=TODAY, period_end=TODAY, disciplines=[],
        prompt_id=PROMPT_ID, schema_version=SCHEMA_VERSION,
    )
    _act_as(a)

    resp = client.get("/api/coach/period-reports")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


# --- validation ------------------------------------------------------------


def test_period_end_before_period_start_is_rejected(db, client):
    _act_as(_seed_user(db))

    resp = client.post(
        "/api/coach/period-reports",
        json={"period_start": "2026-08-10", "period_end": "2026-08-01", "disciplines": []},
    )

    assert resp.status_code == 422


def test_a_period_over_a_year_is_rejected(db, client):
    _act_as(_seed_user(db))

    resp = client.post(
        "/api/coach/period-reports",
        json=_body(start=date(2020, 1, 1), end=date(2026, 8, 10)),
    )

    assert resp.status_code == 422


def test_an_unknown_body_field_is_rejected(db, client):
    """`extra="forbid"`: a field nobody declared fails at the boundary rather
    than being silently ignored or reaching a column."""
    _act_as(_seed_user(db))

    resp = client.post(
        "/api/coach/period-reports",
        json={**_body(), "prompt_id": "smuggled"},
    )

    assert resp.status_code == 422


def test_an_oversized_discipline_list_is_rejected(db, client):
    _act_as(_seed_user(db))

    resp = client.post(
        "/api/coach/period-reports",
        json=_body(disciplines=[f"Type{i}" for i in range(21)]),
    )

    assert resp.status_code == 422


def test_an_overlong_discipline_string_is_rejected(db, client):
    _act_as(_seed_user(db))

    resp = client.post(
        "/api/coach/period-reports",
        json=_body(disciplines=["x" * 65]),
    )

    assert resp.status_code == 422


def test_whitespace_only_disciplines_are_stripped_to_none(db, client, monkeypatch):
    """A discipline list that is whitespace after stripping reads as "every
    discipline", the same identity as an explicitly empty list — it must not
    silently create a distinct, permanently-empty filter."""
    _act_as(_seed_user(db))

    resp = client.post("/api/coach/period-reports", json=_body(disciplines=["   ", ""]))

    assert resp.status_code == 202
    stored = db.query(PeriodReport).one()
    assert stored.disciplines == []
    assert stored.disciplines_key == "all"
