"""Pre-deploy entrypoint: env preflight, then (gated) Alembic migrations (#593).

Railway production deploys do NOT auto-apply Alembic migrations, so code could
ship ahead of its schema and 500 for every user until someone migrated by hand
(#586). This script closes that gap by applying ``alembic upgrade head`` as part
of the Pre-Deploy Command, while REUSING the #551 env preflight so the deploy is
still blocked when a required env var is missing.

Order and fail-fast:
  1. The env preflight (``scripts.preflight_env_check``) ALWAYS runs first. If it
     fails, migrations do NOT run and the process exits non-zero, so a
     misconfigured release fails at the gate while the previous deploy keeps
     serving (Railway fails a deploy whose Pre-Deploy Command exits non-zero).
  2. Migrations run ONLY after a passing preflight AND only when the
     ``RUN_MIGRATIONS`` env flag is truthy.

The ``RUN_MIGRATIONS`` gate keeps migrations to ONE service: set it on the WEB
service only, leave it off on the WORKER, so the two services never migrate
concurrently. With the flag off the script is the plain env preflight (worker
behaviour).

This is an ops entrypoint, so the flag is read straight from ``os.environ`` and
is deliberately NOT a field on ``app.core.config`` -- config stays untouched.

Intended Railway wiring (cwd = ``backend/``):
    web    Pre-Deploy Command: python -m scripts.pre_deploy    (RUN_MIGRATIONS=true)
    worker Pre-Deploy Command: python -m scripts.pre_deploy    (RUN_MIGRATIONS unset)

The preflight enforcement is inherited from ``scripts.preflight_env_check`` (it
enforces under ``APP_ENV=production`` / ``PREFLIGHT_FORCE``); see that module and
``docs/deployment/deploy-checklist.md``.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence

from alembic import command
from alembic.config import Config

from scripts import preflight_env_check

_TRUTHY = {"1", "true", "yes", "on"}


def _migrations_enabled(env: os._Environ[str] | None = None) -> bool:
    """True when the ``RUN_MIGRATIONS`` gate is truthy (default OFF)."""
    source = os.environ if env is None else env
    return str(source.get("RUN_MIGRATIONS", "") or "").strip().lower() in _TRUTHY


def run_preflight() -> int:
    """Run the #551 env preflight, returning its exit code (0 = pass)."""
    return preflight_env_check.main()


def run_migrations() -> None:
    """Apply Alembic migrations up to ``head`` via the Python API.

    Uses the in-process API (not a shell-out) so it stays importable and
    mockable. Expects the process cwd to be ``backend/`` (where ``alembic.ini``
    lives), matching how Railway runs ``python -m scripts.*``.
    """
    command.upgrade(Config("alembic.ini"), "head")


def main(argv: Sequence[str] | None = None) -> int:
    """Run preflight first (fail-fast), then migrate only if enabled + passing."""
    # Preflight parses its own scope/flags from sys.argv; pass ours through so
    # `python -m scripts.pre_deploy --scope web --require-production` works.
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]

    preflight_rc = run_preflight()
    if preflight_rc != 0:
        # Fail fast: a failing preflight must never let migrations run.
        return preflight_rc

    if _migrations_enabled():
        print("pre_deploy: RUN_MIGRATIONS set -- applying alembic upgrade head")
        run_migrations()
        print("pre_deploy: migrations applied (alembic at head)")
    else:
        print("pre_deploy: RUN_MIGRATIONS off -- skipping migrations (preflight only)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
