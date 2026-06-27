"""Deployed-only smoke for the Strava + Telegram handshake auth gates (#540).

The two user-facing handshakes that cannot be bootstrapped locally -- the Strava
OAuth `state` round-trip (#469) and the Telegram `/start` chat-bind + inbound tap
authorization (#477) -- are otherwise covered only by the MANUAL runbook
(`docs/testing/deployed-handshake-verification.md`) plus unit/integration tests of
the offline codec/authorization logic. This script is the automatable half: a
thin, deployed-only smoke that exercises the live deployment's auth GATES, so a
regression that opens a gate (or stops it failing closed) is caught by a
repeatable check rather than only by someone running the manual checklist.

Scope (deliberately the SAFE, non-mutating subset, per the #540 decision):
  * It only asserts that the live gates REJECT unauthentic input. It never
    completes a real OAuth callback and never sends a real `/start`/tap, so it
    cannot mutate the owner's Strava account or Telegram chat.
  * The fully automatable checks need NO credentials: they hit the webhook
    endpoints, which are exempt from the frontend-to-backend basic auth so the
    external services can reach them.
  * The session-gated positive checks from the manual runbook (the OAuth `/login`
    redirect carrying a non-empty signed `state`, and `link-status.configured`)
    are NOT automated here: they require a live Clerk session, which cannot be
    minted non-interactively (the same blocker as #488). They stay in the manual
    runbook. This script narrows, not replaces, that runbook.

This is NOT part of CI: it depends on live external config (a real deployment
with `STRAVA_WEBHOOK_SUBSCRIPTION_ID`, `TELEGRAM_WEBHOOK_SECRET`, prod
`APP_ENV`). Run it by hand (or from an out-of-CI scheduled job) after any change
to `app/api/auth.py`, `app/core/oauth_state.py`, `app/api/webhooks.py`, or the
Telegram link plumbing.

Usage:
    SMOKE_BASE_URL=https://<deployed-backend> python -m scripts.deployed_handshake_smoke
    # optionally exercise the authentic-secret no-op path too:
    SMOKE_BASE_URL=... SMOKE_TELEGRAM_WEBHOOK_SECRET=<secret> python -m scripts.deployed_handshake_smoke

Env:
    SMOKE_BASE_URL                (required) the deployed backend base URL, e.g.
                                  https://<railway-backend-domain>. No production
                                  hostname is hardcoded here (project principle:
                                  every seam URL is config), so this must be set.
    SMOKE_TELEGRAM_WEBHOOK_SECRET (optional) the deployment's TELEGRAM_WEBHOOK_SECRET.
                                  When set, also asserts that an AUTHENTIC-secret
                                  callback from an unbound/unknown chat is a silent
                                  200 no-op (no write). Skipped when absent.
    SMOKE_TIMEOUT_SECONDS         (optional, default 15) per-request timeout.

Exit code is 0 only when every REQUIRED check passes; any failure exits non-zero.
Optional checks that are skipped for missing config never fail the run.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import httpx

# A value that cannot collide with a real Strava athlete id or subscription id
# (those are positive), so the POST authenticity check is provably non-mutating:
# `_event_is_authentic` rejects it before any enqueue/soft-delete branch runs.
_IMPOSSIBLE_ID = -1
# A deliberately wrong Telegram secret -- proves the outer gate rejects a
# non-Telegram caller. Never equal to a real secret.
_WRONG_SECRET = "__smoke_invalid_secret__"  # noqa: S105 (not a real secret)


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    detail: str

    @property
    def ok(self) -> bool:
        # SKIP never fails the run; only FAIL does.
        return self.status != "FAIL"


def _passed(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "PASS", detail)


def _failed(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "FAIL", detail)


def _skipped(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "SKIP", detail)


def check_strava_verify_gate(client: httpx.Client, base: str) -> CheckResult:
    """GET /api/webhooks/strava with a WRONG verify token must be refused.

    A correctly-configured deployment rejects an unknown `hub.verify_token` with
    403 (or 503 in production when the token itself is unset -- the gate is still
    present, just misconfigured). A 200 that echoes the challenge means the gate
    accepted an unknown token -> anyone could register a subscription. Read-only.
    """
    name = "strava_verify_gate_rejects_wrong_token"
    try:
        resp = client.get(
            f"{base}/api/webhooks/strava",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": _WRONG_SECRET,
                "hub.challenge": "smoke_challenge",
            },
        )
    except httpx.HTTPError as exc:
        return _failed(name, f"request error: {exc!r}")

    if resp.status_code == 403:
        return _passed(name, "403 as expected (wrong verify token refused)")
    if resp.status_code == 503:
        return _passed(
            name,
            "503: gate present but STRAVA_WEBHOOK_VERIFY_TOKEN unset on the "
            "deployment -- gate is closed, but fix the config",
        )
    return _failed(
        name,
        f"expected 403/503, got {resp.status_code}: a wrong verify token was "
        f"not refused (gate may be open). Body: {resp.text[:200]!r}",
    )


def check_strava_event_authenticity(client: httpx.Client, base: str) -> CheckResult:
    """POST /api/webhooks/strava with an unauthentic event must be 403.

    Uses negative (impossible) owner/object ids so `_event_is_authentic` cannot
    match a connected athlete -> 403 BEFORE any enqueue or soft-delete. Provably
    non-mutating regardless of subscription config.
    """
    name = "strava_event_gate_rejects_unauthentic"
    body = {
        "object_type": "activity",
        "object_id": _IMPOSSIBLE_ID,
        "aspect_type": "create",
        "owner_id": _IMPOSSIBLE_ID,
        "subscription_id": _IMPOSSIBLE_ID,
        "event_time": int(time.time()),
    }
    try:
        resp = client.post(f"{base}/api/webhooks/strava", json=body)
    except httpx.HTTPError as exc:
        return _failed(name, f"request error: {exc!r}")

    if resp.status_code == 403:
        return _passed(name, "403 as expected (unauthentic event refused)")
    return _failed(
        name,
        f"expected 403, got {resp.status_code}: an event with an unconnected "
        f"owner was not refused. Body: {resp.text[:200]!r}",
    )


def check_telegram_secret_gate(client: httpx.Client, base: str) -> CheckResult:
    """POST /api/webhooks/telegram with a WRONG secret must be 403.

    The outer `X-Telegram-Bot-Api-Secret-Token` gate fails closed in production.
    The body carries no callback_query and no `/start`, so even if the gate were
    (mis)configured open on a non-prod deployment, the request is a no-op
    (`not_callback`) and writes nothing. A 200 here against a PRODUCTION
    deployment means the gate did not fail closed.
    """
    name = "telegram_secret_gate_rejects_wrong_secret"
    try:
        resp = client.post(
            f"{base}/api/webhooks/telegram",
            json={"update_id": 0},
            headers={"X-Telegram-Bot-Api-Secret-Token": _WRONG_SECRET},
        )
    except httpx.HTTPError as exc:
        return _failed(name, f"request error: {exc!r}")

    if resp.status_code == 403:
        return _passed(name, "403 as expected (wrong secret refused)")
    return _failed(
        name,
        f"expected 403, got {resp.status_code}: the Telegram webhook did not "
        f"fail closed on a wrong secret. This check is only valid against a "
        f"production deployment with TELEGRAM_WEBHOOK_SECRET set. "
        f"Body: {resp.text[:200]!r}",
    )


def check_telegram_unbound_chat_noop(
    client: httpx.Client, base: str, secret: str
) -> CheckResult:
    """With the CORRECT secret, an unbound/unknown chat callback is a 200 no-op.

    Confirms the inner authorization: a chat that is neither bound to a user nor
    the global owner chat is silently ignored (no CheckIn written). Double-safe:
    the callback carries a bogus token, so even an unlikely chat-id collision
    hits the `bad_token` path and writes nothing. Only runs when the secret is
    supplied.
    """
    name = "telegram_unbound_chat_is_noop"
    body = {
        "update_id": 0,
        "callback_query": {
            "id": "smoke",
            "data": "__smoke_bogus_token__",
            "message": {
                "chat": {"id": 1},  # not the global owner chat (large id), unbound
                "message_id": 1,
            },
        },
    }
    try:
        resp = client.post(
            f"{base}/api/webhooks/telegram",
            json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
    except httpx.HTTPError as exc:
        return _failed(name, f"request error: {exc!r}")

    if resp.status_code != 200:
        return _failed(
            name,
            f"expected 200 no-op, got {resp.status_code}: an authentic-secret "
            f"request did not pass the outer gate (is the secret correct?). "
            f"Body: {resp.text[:200]!r}",
        )
    reason = ""
    try:
        reason = (resp.json() or {}).get("reason", "")
    except ValueError:
        pass
    # Both reasons mean "nothing was written": the chat was unauthorized, or the
    # bogus token failed to decode. Either confirms the no-op guarantee.
    if reason in {"unauthorized_chat", "bad_token"}:
        return _passed(name, f"200 no-op as expected (reason={reason!r})")
    return _failed(
        name,
        f"200 but unexpected reason {reason!r}: expected a no-op "
        f"(unauthorized_chat/bad_token). Body: {resp.text[:200]!r}",
    )


def run_handshake_checks(
    client: httpx.Client, base: str, tg_secret: str = ""
) -> list[CheckResult]:
    """Run the deployed handshake auth-gate checks and return their results.

    The reusable seam: `main` calls it for the standalone run, and the post-deploy
    verifier (`scripts.post_deploy_verify`, #550) calls it as the release smoke
    after the health poll. The optional-secret Telegram no-op check is SKIPped
    (never FAILed) when `tg_secret` is empty.
    """
    results: list[CheckResult] = [
        check_strava_verify_gate(client, base),
        check_strava_event_authenticity(client, base),
        check_telegram_secret_gate(client, base),
    ]
    if tg_secret:
        results.append(check_telegram_unbound_chat_noop(client, base, tg_secret))
    else:
        results.append(
            _skipped(
                "telegram_unbound_chat_is_noop",
                "set SMOKE_TELEGRAM_WEBHOOK_SECRET to exercise this",
            )
        )
    return results


def report_results(results: list[CheckResult], header: str = "Results") -> int:
    """Print a results block and return an exit code (0 only when no check FAILed)."""
    print(f"{header}:")
    for r in results:
        print(f"  [{r.status:4}] {r.name}: {r.detail}")
    failures = [r for r in results if r.status == "FAIL"]
    skipped = [r for r in results if r.status == "SKIP"]
    print(
        f"\n{len(results) - len(failures) - len(skipped)} passed, "
        f"{len(failures)} failed, {len(skipped)} skipped."
    )
    return 1 if failures else 0


def main() -> int:
    base = (os.environ.get("SMOKE_BASE_URL") or "").rstrip("/")
    if not base:
        print(
            "ERROR: SMOKE_BASE_URL is required (the deployed backend base URL).\n"
            "  e.g. SMOKE_BASE_URL=https://<deployed-backend> "
            "python -m scripts.deployed_handshake_smoke",
            file=sys.stderr,
        )
        return 2

    timeout = float(os.environ.get("SMOKE_TIMEOUT_SECONDS", "15"))
    tg_secret = os.environ.get("SMOKE_TELEGRAM_WEBHOOK_SECRET") or ""

    print(f"Deployed handshake smoke against: {base}\n")

    # `follow_redirects=False`: a 302 to Strava must be observed as a redirect,
    # not silently followed off-host.
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        results = run_handshake_checks(client, base, tg_secret)

    code = report_results(results)
    if code != 0:
        print("\nDEPLOYED HANDSHAKE SMOKE FAILED.", file=sys.stderr)
    else:
        print("\nDeployed handshake smoke passed.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
