"""Tests for the pre-deploy entrypoint (#593).

The script runs the #551 env preflight FIRST (fail-fast), then -- only when the
``RUN_MIGRATIONS`` flag is truthy AND the preflight passed -- applies Alembic
migrations. This gate lets the WEB service migrate on deploy while the WORKER
service (flag off) runs preflight only, so the schema is applied exactly once.
"""

from __future__ import annotations

import pytest

from scripts import pre_deploy


@pytest.fixture
def calls(monkeypatch):
    """Record preflight/migration invocation order without doing real work."""
    order: list[str] = []

    def fake_preflight() -> int:
        order.append("preflight")
        return 0

    def fake_migrate() -> None:
        order.append("migrate")

    monkeypatch.setattr(pre_deploy, "run_preflight", fake_preflight)
    monkeypatch.setattr(pre_deploy, "run_migrations", fake_migrate)
    return order


def test_flag_off_runs_preflight_only(monkeypatch, calls):
    monkeypatch.delenv("RUN_MIGRATIONS", raising=False)
    rc = pre_deploy.main([])
    assert rc == 0
    assert calls == ["preflight"]


def test_flag_on_runs_preflight_then_migrations(monkeypatch, calls):
    monkeypatch.setenv("RUN_MIGRATIONS", "true")
    rc = pre_deploy.main([])
    assert rc == 0
    assert calls == ["preflight", "migrate"]


def test_preflight_failure_skips_migrations_and_exits_nonzero(monkeypatch):
    order: list[str] = []

    def failing_preflight() -> int:
        order.append("preflight")
        return 1

    def fake_migrate() -> None:
        order.append("migrate")

    monkeypatch.setenv("RUN_MIGRATIONS", "true")
    monkeypatch.setattr(pre_deploy, "run_preflight", failing_preflight)
    monkeypatch.setattr(pre_deploy, "run_migrations", fake_migrate)

    rc = pre_deploy.main([])
    assert rc != 0
    assert order == ["preflight"]  # migrations never ran


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("  On ", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_run_migrations_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("RUN_MIGRATIONS", raising=False)
    else:
        monkeypatch.setenv("RUN_MIGRATIONS", value)
    assert pre_deploy._migrations_enabled() is expected


def test_flag_on_but_preflight_fails_does_not_migrate(monkeypatch):
    """Fail-fast holds even with the flag on: a failing preflight blocks migrate."""
    order: list[str] = []
    monkeypatch.setenv("RUN_MIGRATIONS", "1")
    monkeypatch.setattr(
        pre_deploy, "run_preflight", lambda: (order.append("preflight"), 2)[1]
    )
    monkeypatch.setattr(
        pre_deploy, "run_migrations", lambda: order.append("migrate")
    )
    rc = pre_deploy.main([])
    assert rc == 2
    assert order == ["preflight"]
