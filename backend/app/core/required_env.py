"""Canonical list of env vars a production deploy needs, and a pure presence check.

Single source of truth shared by two enforcers so they never drift:
  - the boot guard ``observability.assert_production_config`` (reads resolved
    ``Settings`` values, crashes the WEB boot when one is unset), and
  - the pre-deploy preflight ``scripts.preflight_env_check`` (reads the raw deploy
    environment, exits non-zero so a release command can block cutover BEFORE the
    process boots -- #551).

This module deliberately imports nothing heavy (no ``Settings``, no DB): the
preflight must be able to read the required list and check ``os.environ`` without
instantiating ``Settings`` (which requires ``DATABASE_URL``), so it runs as a thin
release command and is trivially unit-testable against a synthetic environment.
"""

from __future__ import annotations

from typing import Iterable, List, Mapping

# Env vars whose absence makes a production WEB deploy FAIL CLOSED (every route
# 503s) WITHOUT crashing the boot -- the silent "up but broken" deploy that the
# boot guard turns into a crash. CLERK_JWKS_URL empty -> every authenticated route
# 503s; BASIC_AUTH_USER/PASSWORD empty -> every non-exempt route 503s. Web-only
# (the worker serves no HTTP). Keep in sync with the guard's scope note in
# ``observability.assert_production_config``.
REQUIRED_PRODUCTION_ENV = (
    "CLERK_JWKS_URL",
    "BASIC_AUTH_USER",
    "BASIC_AUTH_PASSWORD",
)

# Env vars BOTH the web and worker processes need to boot at all: ``DATABASE_URL``
# is a required ``Settings`` field with no default, so its absence is a loud
# pydantic crash rather than a silent 503. The preflight checks it too so a release
# missing it is blocked before cutover rather than crash-looping on boot.
REQUIRED_BASE_ENV = ("DATABASE_URL",)

# The full set the pre-deploy preflight validates for a production release.
REQUIRED_FOR_PRODUCTION_DEPLOY = REQUIRED_BASE_ENV + REQUIRED_PRODUCTION_ENV


def missing_env(names: Iterable[str], env: Mapping[str, str]) -> List[str]:
    """Return the names whose value in ``env`` is absent or whitespace-only.

    A whitespace-only value (a fat-fingered blank secret) counts as missing, the
    same boundary the boot guard's ``.strip()`` check applies, so a blank can never
    satisfy the gate.
    """
    return [name for name in names if not str(env.get(name, "") or "").strip()]
