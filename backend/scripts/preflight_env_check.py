"""Pre-deploy required-env preflight (#551).

Validates that the env vars a production deploy needs are present BEFORE the
process boots, so a release that is missing one is blocked at the release command
rather than crash-looping on boot. The motivation is the #546 incident: a change
needed an env set first, merged before it was set, and on Railway a crashed boot
does NOT keep the prior healthy deploy serving (the old deploys were ``REMOVED``),
so prod went fully down. A release command that exits non-zero BEFORE cutover is
the stronger gate -- it fails the deploy while the old one keeps serving.

Intended use: wire as the Railway **Pre-Deploy Command** on each service (it runs
in the deploy environment, where the vars live, and Railway fails the deploy and
keeps the previous one serving when it exits non-zero). Env is per-service on
Railway, so pass the service scope:

    web service Pre-Deploy Command:    python -m scripts.preflight_env_check --scope web
    worker service Pre-Deploy Command: python -m scripts.preflight_env_check --scope worker

The ``web`` scope checks the fail-closed HTTP gate vars + the DB; the ``worker``
scope checks only ``DATABASE_URL`` (it serves no HTTP). See
``docs/deployment/deploy-checklist.md`` for the full wiring and the platform-
behaviour caveat that must be verified before relying on it.

Checks the canonical per-scope list in ``app.core.required_env`` (the web set is
the same one the boot guard ``observability.assert_production_config`` enforces),
reading the RAW deploy environment via ``os.environ`` so it needs no ``Settings``
instantiation (and thus no live ``DATABASE_URL``) just to report what is missing.

Enforcement:
  - Enforces when ``APP_ENV=production`` (the real release-command case) OR when
    ``PREFLIGHT_FORCE`` is truthy (a dry run, or the self-test).
  - Otherwise it is a no-op that exits 0, mirroring the boot guard: local dev and
    the test suite run with these unset and must not fail.

Exit code: 0 = all required vars present (or not enforcing); 1 = a required var is
missing.

Usage:
    APP_ENV=production python -m scripts.preflight_env_check --scope web
    PREFLIGHT_FORCE=1 python -m scripts.preflight_env_check   # dry run / self-test
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Sequence, Tuple

from app.core.required_env import missing_env, required_for_scope

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _parse_scope(argv: Sequence[str]) -> str:
    """Read ``--scope <value>`` / ``--scope=<value>`` from argv; default ``web``."""
    for i, arg in enumerate(argv):
        if arg == "--scope" and i + 1 < len(argv):
            return argv[i + 1].strip().lower()
        if arg.startswith("--scope="):
            return arg.split("=", 1)[1].strip().lower()
    return "web"


def check(env: Mapping[str, str], *, scope: str = "web") -> Tuple[int, str]:
    """Pure check: return ``(exit_code, message)`` for the given environment + scope.

    Separated from ``main`` so it can be unit-tested against a synthetic env with
    no process exit and no stdout coupling.
    """
    app_env = str(env.get("APP_ENV", "") or "").strip().lower()
    force = _is_truthy(env.get("PREFLIGHT_FORCE"))
    enforcing = force or app_env == "production"
    required = required_for_scope(scope)

    if not enforcing:
        return 0, (
            f"preflight[{scope}]: skipped (APP_ENV={app_env or 'unset'!r}, not "
            "production; set PREFLIGHT_FORCE=1 to check anyway)."
        )

    missing = missing_env(required, env)
    if missing:
        return 1, (
            f"preflight[{scope}] FAILED: required production env var(s) unset: "
            + ", ".join(missing)
            + ". A deploy missing these would fail closed (503 every route) or "
            "crash on boot. Set them on the affected service, then redeploy. "
            "See docs/deployment/deploy-checklist.md."
        )
    return 0, (
        f"preflight[{scope}] OK: all required env vars are present ("
        + ", ".join(required)
        + ")."
    )


def main() -> int:
    scope = _parse_scope(sys.argv[1:])
    exit_code, message = check(os.environ, scope=scope)
    stream = sys.stderr if exit_code != 0 else sys.stdout
    print(message, file=stream)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
