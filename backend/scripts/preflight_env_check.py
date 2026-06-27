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

    web Pre-Deploy Command:    python -m scripts.preflight_env_check --scope web --require-production
    worker Pre-Deploy Command: python -m scripts.preflight_env_check --scope worker --require-production

The ``web`` scope checks the fail-closed HTTP gate vars + the DB; the ``worker``
scope checks only ``DATABASE_URL`` (it serves no HTTP). See
``docs/deployment/deploy-checklist.md`` for the full wiring (verified on Railway:
a failing Pre-Deploy Command marks the deploy FAILED and leaves the previously
ACTIVE deployment serving).

Checks the canonical per-scope list in ``app.core.required_env`` (the web set is
the same one the boot guard ``observability.assert_production_config`` enforces),
reading the RAW deploy environment via ``os.environ`` so it needs no ``Settings``
instantiation (and thus no live ``DATABASE_URL``) just to report what is missing.

Enforcement:
  - Enforces when ``APP_ENV=production``, when ``PREFLIGHT_FORCE`` is truthy, or
    when ``--require-production`` is passed.
  - Otherwise it is a no-op that exits 0, mirroring the boot guard: local dev and
    the test suite run with these unset and must not fail.

``--require-production`` is the hardened release-command form: it ALWAYS enforces
AND additionally FAILS when ``APP_ENV`` is not exactly ``production``. Without it,
an empty/dropped ``APP_ENV`` would make the gate silently skip (observed: Railway's
inline variable editor can save an empty value), which disarms not just this gate
but the boot guard and the production auth path too. Because it is a CLI flag baked
into the Pre-Deploy Command string, it cannot be dropped the way a separate env var
can, so the release command stays armed.

Exit code: 0 = all checks pass (or not enforcing); 1 = a required var is missing
(or, under ``--require-production``, ``APP_ENV`` is not production).

Usage:
    APP_ENV=production python -m scripts.preflight_env_check --scope web
    python -m scripts.preflight_env_check --scope web --require-production  # release cmd
    PREFLIGHT_FORCE=1 python -m scripts.preflight_env_check                 # dry run
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Sequence, Tuple

from app.core.required_env import missing_env, required_for_scope

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _parse_args(argv: Sequence[str]) -> Tuple[str, bool]:
    """Read ``--scope <value>``/``--scope=<value>`` (default ``web``) and the
    ``--require-production`` flag from argv."""
    scope = "web"
    require_production = False
    for i, arg in enumerate(argv):
        if arg == "--scope" and i + 1 < len(argv):
            scope = argv[i + 1].strip().lower()
        elif arg.startswith("--scope="):
            scope = arg.split("=", 1)[1].strip().lower()
        elif arg == "--require-production":
            require_production = True
    return scope, require_production


def check(
    env: Mapping[str, str],
    *,
    scope: str = "web",
    require_production: bool = False,
) -> Tuple[int, str]:
    """Pure check: return ``(exit_code, message)`` for the given environment + scope.

    Separated from ``main`` so it can be unit-tested against a synthetic env with
    no process exit and no stdout coupling.
    """
    app_env = str(env.get("APP_ENV", "") or "").strip().lower()
    force = _is_truthy(env.get("PREFLIGHT_FORCE"))
    enforcing = force or require_production or app_env == "production"
    required = required_for_scope(scope)

    if not enforcing:
        return 0, (
            f"preflight[{scope}]: skipped (APP_ENV={app_env or 'unset'!r}, not "
            "production; pass --require-production or set PREFLIGHT_FORCE=1 to check "
            "anyway)."
        )

    problems = []
    # --require-production catches the silent-disarm footgun: an APP_ENV that is not
    # exactly "production" means this gate (and the boot guard, and the prod auth
    # path) would otherwise be off. Treat it as a hard failure, not a skip.
    if require_production and app_env != "production":
        problems.append(
            f"APP_ENV is not 'production' (got {app_env or 'unset'!r}) -- production "
            "hardening (this gate, the boot guard, and the auth path) would be "
            "disarmed; set APP_ENV=production"
        )
    missing = missing_env(required, env)
    if missing:
        problems.append("required env var(s) unset: " + ", ".join(missing))

    if problems:
        return 1, (
            f"preflight[{scope}] FAILED: "
            + "; ".join(problems)
            + ". A deploy in this state would fail closed (503 every route) or run "
            "unhardened. Fix on the affected service, then redeploy. "
            "See docs/deployment/deploy-checklist.md."
        )
    return 0, (
        f"preflight[{scope}] OK: all required env vars are present ("
        + ", ".join(required)
        + ")."
    )


def main() -> int:
    scope, require_production = _parse_args(sys.argv[1:])
    exit_code, message = check(
        os.environ, scope=scope, require_production=require_production
    )
    stream = sys.stderr if exit_code != 0 else sys.stdout
    print(message, file=stream)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
