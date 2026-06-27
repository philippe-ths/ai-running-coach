"""Production LLM cost-cap safety default + non-fatal boot log (#549, supersedes #543).

The #543 boot guard (`assert_budget_cap_armed`) crashed the process when production
ran with no `LLM_BUDGET_*_USD` window set. On Railway that took prod fully DOWN
(the prior deploy was REMOVED, so a crashed boot left nothing serving) -- a
strictly worse failure than the missing cost cap it guarded. #549 replaces it with:

  * a non-fatal safety DEFAULT (`budget.production_default_ceiling`): in production
    an unconfigured, non-disabled cap falls back to a generous global-daily ceiling,
    so prod is never silently uncapped without crashing; and
  * a non-fatal boot LOG (`observability.log_budget_cap_status`) that only surfaces
    the resulting posture and NEVER raises.

These tests pin both: the default resolution (when it applies, when an explicit
window or the disable flag overrides it, and that the gate enforces it), and that
the boot log is silent-safe in every posture.
"""

import logging

import pytest

from app.core.config import settings
from app.core.observability import log_budget_cap_status
from app.services.coach import budget
from app.services.coach.budget import (
    cap_is_armed,
    new_in_memory_gate,
    production_default_ceiling,
    set_gate,
)


def _set(monkeypatch, **kwargs):
    for key, value in kwargs.items():
        monkeypatch.setattr(settings, key, value)


_ALL_WINDOWS_OFF = dict(
    LLM_BUDGET_USER_DAILY_USD=0.0,
    LLM_BUDGET_USER_MONTHLY_USD=0.0,
    LLM_BUDGET_GLOBAL_DAILY_USD=0.0,
    LLM_BUDGET_GLOBAL_MONTHLY_USD=0.0,
    LLM_BUDGET_DISABLED=False,
    LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD=50.0,
)


@pytest.fixture
def isolated_gate():
    """A fresh in-process gate for over_budget tests; reset to lazy after."""
    set_gate(new_in_memory_gate())
    try:
        yield
    finally:
        set_gate(None)


# --- the safety default resolution -----------------------------------------


def test_default_applies_in_production_when_uncapped_and_not_disabled(monkeypatch):
    # The exact #543/#549 case: prod, no window, no waiver -> the backstop arms.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    assert production_default_ceiling() == 50.0
    assert cap_is_armed() is True


def test_default_inactive_outside_production(monkeypatch):
    # Local dev / the test suite run uncapped and must NOT get the backstop.
    _set(monkeypatch, APP_ENV="local", **_ALL_WINDOWS_OFF)
    assert production_default_ceiling() == 0.0
    assert cap_is_armed() is False


@pytest.mark.parametrize(
    "window",
    [
        "LLM_BUDGET_USER_DAILY_USD",
        "LLM_BUDGET_USER_MONTHLY_USD",
        "LLM_BUDGET_GLOBAL_DAILY_USD",
        "LLM_BUDGET_GLOBAL_MONTHLY_USD",
    ],
)
def test_explicit_window_overrides_the_default(monkeypatch, window):
    # An explicit ceiling means the owner tuned the cap; the backstop steps aside.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, window, 5.0)
    assert production_default_ceiling() == 0.0
    assert cap_is_armed() is True


def test_disabled_waiver_suppresses_the_default(monkeypatch):
    # The conscious opt-out: LLM_BUDGET_DISABLED=true runs prod deliberately uncapped.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_DISABLED", True)
    assert production_default_ceiling() == 0.0
    assert cap_is_armed() is False


def test_default_can_be_turned_off_in_production(monkeypatch):
    # Setting the backstop to 0 in prod is an explicit "no backstop" -> uncapped.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD", 0.0)
    assert production_default_ceiling() == 0.0
    assert cap_is_armed() is False


def test_gate_enforces_the_default_ceiling(monkeypatch, isolated_gate):
    # The backstop is not cosmetic: over_budget must trip at the default global
    # ceiling. $50 backstop; record exactly $50 of global spend -> over budget.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    # haiku output is $5/MTok, so 10M output tokens == $50.00 (>= the $50 ceiling).
    assert budget.over_budget("user-1") is False
    budget.record("user-1", "claude-haiku-4-5", input_tokens=0, output_tokens=10_000_000)
    assert budget.over_budget("user-1") is True
    # A different user shares only the GLOBAL window, which is the one armed here,
    # so they are over budget too (the backstop is a global catastrophe stop).
    assert budget.over_budget("user-2") is True


def test_gate_uncapped_when_disabled(monkeypatch, isolated_gate):
    # With the waiver, no window is armed -> spend never trips over_budget.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_DISABLED", True)
    budget.record("user-1", "claude-haiku-4-5", input_tokens=0, output_tokens=10_000_000)
    assert budget.over_budget("user-1") is False


# --- the non-fatal boot log -------------------------------------------------


def test_boot_log_never_raises_outside_production(monkeypatch):
    _set(monkeypatch, APP_ENV="local", **_ALL_WINDOWS_OFF)
    log_budget_cap_status()  # does not raise


def test_boot_log_warns_on_backstop_default(monkeypatch, caplog):
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    with caplog.at_level(logging.WARNING):
        log_budget_cap_status()  # does not raise
    assert any("backstop" in r.message for r in caplog.records)


def test_boot_log_warns_when_explicitly_disabled(monkeypatch, caplog):
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_DISABLED", True)
    with caplog.at_level(logging.WARNING):
        log_budget_cap_status()  # does not raise
    assert any("UNCAPPED" in r.message for r in caplog.records)


def test_boot_log_info_when_explicit_window_armed(monkeypatch, caplog):
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_GLOBAL_DAILY_USD", 5.0)
    with caplog.at_level(logging.INFO):
        log_budget_cap_status()  # does not raise
    assert any("armed" in r.message for r in caplog.records)


def test_boot_log_warns_when_unarmed_and_backstop_off(monkeypatch, caplog):
    # Prod, no window, backstop turned off: genuinely uncapped -> loud warning.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD", 0.0)
    with caplog.at_level(logging.WARNING):
        log_budget_cap_status()  # does not raise
    assert any("UNCAPPED" in r.message for r in caplog.records)
