"""Post-deploy verification: confirm a fresh deploy is actually healthy (#550).

When a bad deploy reaches prod, nothing automated caught it. During the #546
incident the budget boot guard crashed both web and worker and prod was down
(`/api/health` -> HTTP 000); it was discovered only because the owner saw
Railway's crash email. This script is the release gate that turns "a human
noticed the crash email" into "the check caught it".

What it does, against a deployed backend base URL (`SMOKE_BASE_URL`):
  1. POLLS `GET /api/health` until it returns 200 with ``status == "ok"``, for up
     to `POST_DEPLOY_HEALTH_TIMEOUT_SECONDS` (default 180). A new Railway deploy
     takes a minute or two to come up, so this waits rather than checking once.
     A timeout (the process never became healthy -- the #546 outage) FAILS.
  2. Runs the deployed handshake auth-gate smoke (`scripts.deployed_handshake_smoke`)
     as the release smoke, so a regression that opens an auth gate is also caught.

Exit code is 0 only when health came up AND every required handshake check passed.
This is meant to run on push to `main` (the CI ``post-deploy-verify`` job) after the
Railway deploy, or by hand. It is non-mutating: health is a read, and the handshake
smoke only asserts the live gates REJECT unauthentic input (see that script).

Env:
    SMOKE_BASE_URL                   (required) deployed backend base URL, e.g.
                                     https://<railway-backend-domain>. No production
                                     hostname is hardcoded (project principle: every
                                     seam URL is config).
    POST_DEPLOY_HEALTH_TIMEOUT_SECONDS (optional, default 180) how long to wait for
                                     /api/health to become healthy.
    POST_DEPLOY_HEALTH_POLL_SECONDS  (optional, default 5) seconds between polls.
    SMOKE_TIMEOUT_SECONDS            (optional, default 15) per-request timeout.
    SMOKE_TELEGRAM_WEBHOOK_SECRET    (optional) enables the Telegram no-op handshake
                                     check; skipped (never failed) when absent.

Usage:
    SMOKE_BASE_URL=https://<deployed-backend> python -m scripts.post_deploy_verify
"""

from __future__ import annotations

import os
import sys
import time

import httpx

from scripts.deployed_handshake_smoke import (
    CheckResult,
    report_results,
    run_handshake_checks,
    _failed,
    _passed,
)


def poll_health(
    client: httpx.Client, base: str, timeout_seconds: float, poll_seconds: float
) -> CheckResult:
    """Poll GET /api/health until 200 + status ok, or fail after the timeout.

    Returns a PASS once the deploy is healthy (reporting how long it took) or a
    FAIL describing the last observed state -- a non-200, a non-ok body, a
    connection refusal (the crashed-boot HTTP-000 case), or never coming up in
    time. The whole point of #550 is that this FAIL is the signal a crashed or
    regressed deploy emits, instead of silence.
    """
    name = "deploy_health_within_timeout"
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_detail = "no attempt made"
    while True:
        attempts += 1
        try:
            resp = client.get(f"{base}/api/health")
            if resp.status_code == 200:
                body = {}
                try:
                    body = resp.json() or {}
                except ValueError:
                    body = {}
                if body.get("status") == "ok":
                    elapsed = timeout_seconds - max(0.0, deadline - time.monotonic())
                    db = body.get("database")
                    detail = (
                        f"healthy after {attempts} attempt(s), ~{elapsed:.0f}s "
                        f"(database={db!r})"
                    )
                    # A reachable process with a broken DB is "up but degraded".
                    # Treat it as healthy for the deploy gate (the process booted
                    # and serves), but make the DB state visible in the detail.
                    return _passed(name, detail)
                last_detail = f"200 but body status != ok: {body!r}"
            else:
                last_detail = f"HTTP {resp.status_code}: {resp.text[:160]!r}"
        except httpx.HTTPError as exc:
            # ConnectError here is the crashed-boot signature: nothing listening.
            last_detail = f"request error: {exc!r}"

        if time.monotonic() >= deadline:
            return _failed(
                name,
                f"/api/health never became healthy within {timeout_seconds:.0f}s "
                f"({attempts} attempt(s)). Last: {last_detail}. A crashed or "
                f"regressed deploy is the likely cause.",
            )
        time.sleep(poll_seconds)


def main() -> int:
    base = (os.environ.get("SMOKE_BASE_URL") or "").rstrip("/")
    if not base:
        print(
            "ERROR: SMOKE_BASE_URL is required (the deployed backend base URL).\n"
            "  e.g. SMOKE_BASE_URL=https://<deployed-backend> "
            "python -m scripts.post_deploy_verify",
            file=sys.stderr,
        )
        return 2

    health_timeout = float(os.environ.get("POST_DEPLOY_HEALTH_TIMEOUT_SECONDS", "180"))
    poll_seconds = float(os.environ.get("POST_DEPLOY_HEALTH_POLL_SECONDS", "5"))
    req_timeout = float(os.environ.get("SMOKE_TIMEOUT_SECONDS", "15"))
    tg_secret = os.environ.get("SMOKE_TELEGRAM_WEBHOOK_SECRET") or ""

    print(f"Post-deploy verification against: {base}\n")

    results: list[CheckResult] = []
    # `follow_redirects=False`: a 302 to Strava must be observed as a redirect,
    # not silently followed off-host (matches the handshake smoke).
    with httpx.Client(timeout=req_timeout, follow_redirects=False) as client:
        print(
            f"Waiting for /api/health (timeout {health_timeout:.0f}s, "
            f"poll {poll_seconds:.0f}s)...",
            flush=True,
        )
        health = poll_health(client, base, health_timeout, poll_seconds)
        results.append(health)
        print(f"  [{health.status:4}] {health.name}: {health.detail}\n", flush=True)

        # Only run the handshake smoke if the deploy is actually up; against a
        # down deploy the gate checks would just pile redundant connection errors
        # onto the health failure.
        if health.ok:
            results.extend(run_handshake_checks(client, base, tg_secret))

    code = report_results(results, "Post-deploy verification")
    if code != 0:
        print("\nPOST-DEPLOY VERIFICATION FAILED.", file=sys.stderr)
    else:
        print("\nPost-deploy verification passed.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
