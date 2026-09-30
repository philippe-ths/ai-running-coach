"""Post-deploy verification logic (#550).

`scripts.post_deploy_verify` is the release gate that turns "a human noticed the
crash email" (the #546 outage) into an automated check: it polls the deployed
`/api/health` until healthy, then runs the deployed handshake auth-gate smoke.

These tests drive the two reusable pieces against an httpx MockTransport (no
network, no real deploy): the health poll's pass/fail/retry verdicts, and the
handshake-check seam's pass/skip/fail wiring. The handshake smoke's gate SEMANTICS
are deployed/production-only (covered by the manual runbook and the auth unit
tests); here we only verify post_deploy_verify reads the gate verdicts correctly.
"""

import httpx

from scripts.deployed_handshake_smoke import run_handshake_checks
from scripts.post_deploy_verify import poll_health

_BASE = "http://testserver"


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=_BASE,
        follow_redirects=False,
    )


# --- health poll ------------------------------------------------------------


def test_poll_health_passes_when_healthy():
    def handler(_request):
        return httpx.Response(200, json={"status": "ok", "database": "ok"})

    with _client(handler) as client:
        result = poll_health(client, _BASE, timeout_seconds=2, poll_seconds=0.01)
    assert result.status == "PASS"
    assert "healthy" in result.detail


def test_poll_health_passes_with_degraded_db_but_reachable():
    # A reachable process with a broken DB still booted and serves: the deploy
    # gate is "did the process come up", and the DB state is surfaced in detail.
    def handler(_request):
        return httpx.Response(200, json={"status": "ok", "database": "error: boom"})

    with _client(handler) as client:
        result = poll_health(client, _BASE, timeout_seconds=2, poll_seconds=0.01)
    assert result.status == "PASS"
    assert "error: boom" in result.detail


def test_poll_health_fails_on_non_200():
    def handler(_request):
        return httpx.Response(503, text="nope")

    with _client(handler) as client:
        result = poll_health(client, _BASE, timeout_seconds=0, poll_seconds=0.01)
    assert result.status == "FAIL"
    assert "503" in result.detail


def test_poll_health_fails_on_200_but_status_not_ok():
    def handler(_request):
        return httpx.Response(200, json={"status": "starting"})

    with _client(handler) as client:
        result = poll_health(client, _BASE, timeout_seconds=0, poll_seconds=0.01)
    assert result.status == "FAIL"
    assert "never became healthy" in result.detail


def test_poll_health_fails_on_connection_refused():
    # The crashed-boot signature: nothing listening -> ConnectError -> FAIL.
    def handler(_request):
        raise httpx.ConnectError("connection refused")

    with _client(handler) as client:
        result = poll_health(client, _BASE, timeout_seconds=0, poll_seconds=0.01)
    assert result.status == "FAIL"
    assert "never became healthy" in result.detail


def test_poll_health_retries_until_healthy():
    # The new deploy comes up on the 3rd poll; the poller must wait, not fail once.
    state = {"calls": 0}

    def handler(_request):
        state["calls"] += 1
        if state["calls"] < 3:
            return httpx.Response(503, text="warming up")
        return httpx.Response(200, json={"status": "ok", "database": "ok"})

    with _client(handler) as client:
        result = poll_health(client, _BASE, timeout_seconds=5, poll_seconds=0.01)
    assert result.status == "PASS"
    assert state["calls"] >= 3


# --- handshake-check seam ---------------------------------------------------


def _rejecting_handler(_request):
    """All webhook gates reject (403) -- the deployed-healthy shape."""
    return httpx.Response(403, text="forbidden")


def test_handshake_checks_pass_when_gates_reject_and_skip_optional():
    with _client(_rejecting_handler) as client:
        results = run_handshake_checks(client, _BASE, tg_secret="")
    by_status = [(r.name, r.status) for r in results]
    statuses = {s for _, s in by_status}
    assert "FAIL" not in statuses, by_status
    # The optional Telegram no-op check is skipped without a secret.
    assert any(s == "SKIP" for _, s in by_status)


def test_handshake_checks_fail_when_a_gate_opens():
    # A Strava event gate that returns 200 (accepts an unauthentic event) FAILs.
    def handler(request):
        if request.url.path == "/api/webhooks/strava" and request.method == "POST":
            return httpx.Response(200, text="ok")
        return httpx.Response(403, text="forbidden")

    with _client(handler) as client:
        results = run_handshake_checks(client, _BASE, tg_secret="")
    assert any(r.status == "FAIL" for r in results)


# --- deployed commit (#1027) ------------------------------------------------
# Health alone cannot tell the new deploy from the old one: the outgoing
# deployment answers healthy for the whole build. Given the pushed commit, the
# gate passes only once production reports running it.

_NEW = "b" * 40
_OLD = "a" * 40


def _serving(*commits):
    """Health answers ok, reporting each commit in turn and then the last forever."""
    seen = list(commits)

    def handler(_request):
        commit = seen.pop(0) if len(seen) > 1 else seen[0]
        return httpx.Response(200, json={"status": "ok", "database": "ok", "commit": commit})

    return handler


def test_poll_health_waits_through_the_old_deploy_for_the_pushed_commit():
    with _client(_serving(_OLD, _OLD, _NEW)) as client:
        result = poll_health(
            client, _BASE, timeout_seconds=5, poll_seconds=0.01, expected_commit=_NEW
        )
    assert result.status == "PASS"


def test_poll_health_fails_when_the_old_deploy_stays_live():
    with _client(_serving(_OLD)) as client:
        result = poll_health(
            client, _BASE, timeout_seconds=0.2, poll_seconds=0.01,
            expected_commit=_NEW, contains=lambda live, expected: False,
        )
    assert result.status == "FAIL"
    assert _OLD[:12] in result.detail


def test_poll_health_passes_when_a_newer_deploy_superseded_the_pushed_commit():
    # Railway can skip a commit's build when a later push lands first; the
    # later deploy still contains it, so the gate for the earlier push passes.
    with _client(_serving(_OLD)) as client:
        result = poll_health(
            client, _BASE, timeout_seconds=5, poll_seconds=0.01,
            expected_commit=_NEW, contains=lambda live, expected: live == _OLD,
        )
    assert result.status == "PASS"


def test_poll_health_names_a_deploy_that_reports_no_commit():
    with _client(_serving(None)) as client:
        result = poll_health(
            client, _BASE, timeout_seconds=0.2, poll_seconds=0.01, expected_commit=_NEW
        )
    assert result.status == "FAIL"
    assert "RAILWAY_GIT_COMMIT_SHA" in result.detail
