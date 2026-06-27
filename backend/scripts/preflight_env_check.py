"""Pre-deploy required-env preflight (#551).

Validates that the env vars a production deploy needs are present BEFORE the
process boots, so a release that is missing one is blocked at the release command
rather than crash-looping on boot. The motivation is the #546 incident: a change
needed an env set first, merged before it was set, and on Railway a crashed boot
does NOT keep the prior healthy deploy serving (the old deploys were ``REMOVED``),
so prod went fully down. A release command that exits non-zero BEFORE cutover is
the stronger gate -- it fails the deploy while the old one keeps serving.

Intended use: wire as the Railway *release command* on both the ``web`` and
``worker`` services (it runs in the deploy environment, where the vars live),
ahead of ``alembic upgrade head`` and the app start. See
``docs/deployment/deploy-checklist.md`` for the wiring and the platform-behaviour
caveat that must be verified before relying on it.

Checks the canonical list in ``app.core.required_env`` (the same one the boot
guard ``observability.assert_production_config`` enforces), reading the RAW deploy
environment via ``os.environ`` so it needs no ``Settings`` instantiation (and thus
no live ``DATABASE_URL``) just to report what is missing.

Enforcement:
  - Enforces when ``APP_ENV=production`` (the real release-command case) OR when
    ``PREFLIGHT_FORCE`` is truthy (a dry run, or the self-test).
  - Otherwise it is a no-op that exits 0, mirroring the boot guard: local dev and
    the test suite run with these unset and must not fail.

Exit code: 0 = all required vars present (or not enforcing); 1 = a required var is
missing.

Usage:
    APP_ENV=production python -m scripts.preflight_env_check
    PREFLIGHT_FORCE=1 python -m scripts.preflight_env_check   # dry run / self-test
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Tuple

from app.core.required_env import REQUIRED_FOR_PRODUCTION_DEPLOY, missing_env

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def check(env: Mapping[str, str]) -> Tuple[int, str]:
    """Pure check: return ``(exit_code, message)`` for the given environment.

    Separated from ``main`` so it can be unit-tested against a synthetic env with
    no process exit and no stdout coupling.
    """
    app_env = str(env.get("APP_ENV", "") or "").strip().lower()
    force = _is_truthy(env.get("PREFLIGHT_FORCE"))
    enforcing = force or app_env == "production"

    if not enforcing:
        return 0, (
            f"preflight: skipped (APP_ENV={app_env or 'unset'!r}, not production; "
            "set PREFLIGHT_FORCE=1 to check anyway)."
        )

    missing = missing_env(REQUIRED_FOR_PRODUCTION_DEPLOY, env)
    if missing:
        return 1, (
            "preflight FAILED: required production env var(s) unset: "
            + ", ".join(missing)
            + ". A deploy missing these would fail closed (503 every route) or "
            "crash on boot. Set them on every affected service, then redeploy. "
            "See docs/deployment/deploy-checklist.md."
        )
    return 0, (
        "preflight OK: all required production env vars are present ("
        + ", ".join(REQUIRED_FOR_PRODUCTION_DEPLOY)
        + ")."
    )


def main() -> int:
    exit_code, message = check(os.environ)
    stream = sys.stderr if exit_code != 0 else sys.stdout
    print(message, file=stream)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
