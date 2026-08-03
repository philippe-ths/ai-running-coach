"""The test session must resolve the CODE DEFAULTS, not a developer's `.env` (#752).

`Settings` is a module-level singleton built at import time and reads
`backend/.env`. On a developer machine that file carries prod-parity coach
configuration (a lean prompt id, the receipt cadence on, most `COACH_*_ENABLED`
kill switches off), while a large family of tests asserts the byte-stable
behaviour of the DEFAULTS. The two disagreed: `make backend-test` reported 99
failures locally and 0 in CI, which trains everyone to stop reading a red local
suite.

These tests pin the fix at the level the problem lives at. `conftest.py` marks the
session before importing the app and `Settings` then skips the env file, so a
local run resolves exactly what CI resolves — CI has no `.env` and sets only a
dummy `DATABASE_URL`.

The coach assertion is written over a PREFIX rather than a hand-maintained list,
because the kill-switch family is designed to grow (#522) and a list is what rots.
"""

from pydantic_core import PydanticUndefined

from app.core.config import Settings, settings


def test_dotenv_is_not_loaded_in_the_test_session():
    """The mechanism itself: no env file is in play while the suite runs.

    Asserted directly rather than inferred from values, because a `.env` that
    happens to match the defaults today would hide the leak until the day it
    stops matching.
    """
    assert Settings.model_config.get("env_file") is None


def test_every_coach_setting_resolves_to_its_declared_default():
    """No `COACH_*` value reaches the suite from outside the code.

    This is the assertion that fails when `.env` leaks: it is the direct cause of
    the 99 local failures, concentrated in the pack/prompt-gating suites that pin
    byte-stable behaviour against the defaults.
    """
    leaked = {}
    for name, field in Settings.model_fields.items():
        if not name.startswith("COACH_") or field.default is PydanticUndefined:
            continue
        actual = getattr(settings, name)
        if actual != field.default:
            leaked[name] = (field.default, actual)

    assert not leaked, (
        "coach settings differ from their declared defaults, so the suite is "
        "testing an ambient environment rather than the code "
        f"(name: expected -> actual): {leaked}"
    )
