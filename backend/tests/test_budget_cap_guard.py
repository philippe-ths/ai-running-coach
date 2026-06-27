"""Production budget-cap boot guard (#543).

`assert_budget_cap_armed` crashes the boot when production would run the LLM cost
cap silently disabled (all `LLM_BUDGET_*_USD` ceilings 0 and no explicit waiver),
so an uncapped deployment that lets one user drain the shared Anthropic budget is
never promoted by accident. These tests pin the firing condition: it raises in
production only when the cap is both unarmed and not waived, and stays a no-op
everywhere else (the suite and local dev run uncapped and must not raise).
"""

import pytest

from app.core.config import settings
from app.core.observability import ProductionConfigError, assert_budget_cap_armed


def _set(monkeypatch, **kwargs):
    for key, value in kwargs.items():
        monkeypatch.setattr(settings, key, value)


_ALL_WINDOWS_OFF = dict(
    LLM_BUDGET_USER_DAILY_USD=0.0,
    LLM_BUDGET_USER_MONTHLY_USD=0.0,
    LLM_BUDGET_GLOBAL_DAILY_USD=0.0,
    LLM_BUDGET_GLOBAL_MONTHLY_USD=0.0,
    LLM_BUDGET_DISABLED=False,
)


def test_noop_outside_production_even_when_uncapped(monkeypatch):
    # Local dev and the test suite run with no cost cap; the guard must stay
    # silent or it would break every non-production boot.
    _set(monkeypatch, APP_ENV="local", **_ALL_WINDOWS_OFF)
    assert_budget_cap_armed()  # does not raise


def test_raises_in_production_when_uncapped_and_not_waived(monkeypatch):
    # The exact #543 risk: prod with no LLM_BUDGET_* window set and no waiver.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    with pytest.raises(ProductionConfigError) as exc:
        assert_budget_cap_armed()
    assert "LLM_BUDGET" in str(exc.value)


@pytest.mark.parametrize(
    "window",
    [
        "LLM_BUDGET_USER_DAILY_USD",
        "LLM_BUDGET_USER_MONTHLY_USD",
        "LLM_BUDGET_GLOBAL_DAILY_USD",
        "LLM_BUDGET_GLOBAL_MONTHLY_USD",
    ],
)
def test_passes_in_production_when_any_single_window_armed(monkeypatch, window):
    # Any one positive ceiling arms the cap (mirrors over_budget's per-window check).
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, window, 5.0)
    assert_budget_cap_armed()  # does not raise


def test_passes_in_production_when_explicitly_waived(monkeypatch):
    # The conscious opt-out: an owner who deliberately wants no cap sets the flag.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    monkeypatch.setattr(settings, "LLM_BUDGET_DISABLED", True)
    assert_budget_cap_armed()  # does not raise


def test_error_names_the_windows_and_the_waiver(monkeypatch):
    # The message must be actionable: how to arm, and how to waive.
    _set(monkeypatch, APP_ENV="production", **_ALL_WINDOWS_OFF)
    with pytest.raises(ProductionConfigError) as exc:
        assert_budget_cap_armed()
    msg = str(exc.value)
    assert "LLM_BUDGET_USER_DAILY_USD" in msg
    assert "LLM_BUDGET_DISABLED" in msg
