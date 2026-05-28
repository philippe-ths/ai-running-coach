"""Tests for the observability layer (Sentry init + structured JSON logging)."""

import io
import json
import logging
from unittest.mock import patch

import pytest

from app.core import observability
from app.core.config import settings


class TestJSONFormatter:
    def test_renders_a_single_json_object_with_required_keys(self):
        formatter = observability.JSONFormatter()
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )

        payload = json.loads(formatter.format(record))

        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["message"] == "hello world"
        assert "ts" in payload

    def test_includes_exception_info_when_present(self):
        formatter = observability.JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=20,
            msg="caught",
            args=None,
            exc_info=exc_info,
        )
        payload = json.loads(formatter.format(record))

        assert "exc_info" in payload
        assert "ValueError: boom" in payload["exc_info"]

    def test_includes_extra_fields_attached_to_the_record(self):
        formatter = observability.JSONFormatter()
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=30,
            msg="m",
            args=None,
            exc_info=None,
        )
        record.activity_id = 42
        record.user = "philippe"

        payload = json.loads(formatter.format(record))

        assert payload["activity_id"] == 42
        assert payload["user"] == "philippe"


class TestInitLogging:
    def test_replaces_existing_handlers_idempotently(self, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "local")
        root = logging.getLogger()
        observability.init_logging()
        first_handlers = list(root.handlers)
        observability.init_logging()
        second_handlers = list(root.handlers)

        assert len(first_handlers) == 1
        assert len(second_handlers) == 1
        assert first_handlers[0] is not second_handlers[0]

    def test_uses_json_formatter_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "production")
        observability.init_logging()
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler.formatter, observability.JSONFormatter)

    def test_uses_human_formatter_outside_production(self, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "local")
        observability.init_logging()
        handler = logging.getLogger().handlers[0]
        assert not isinstance(handler.formatter, observability.JSONFormatter)

    def test_json_handler_actually_emits_json(self, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "production")
        observability.init_logging()
        buffer = io.StringIO()
        logging.getLogger().handlers[0].stream = buffer

        logging.getLogger("app.test").info("hello")

        payload = json.loads(buffer.getvalue().strip())
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"


class TestInitSentry:
    def test_is_noop_when_dsn_is_unset(self, monkeypatch):
        monkeypatch.setattr(settings, "SENTRY_DSN", "")
        with patch.object(observability.sentry_sdk, "init") as mock_init:
            observability.init_sentry("web")
        mock_init.assert_not_called()

    def test_initialises_sdk_when_dsn_is_set(self, monkeypatch):
        monkeypatch.setattr(settings, "SENTRY_DSN", "https://x@example.ingest.sentry.io/1")
        monkeypatch.setattr(settings, "APP_ENV", "production")
        with patch.object(observability.sentry_sdk, "init") as mock_init, \
                patch.object(observability.sentry_sdk, "set_tag") as mock_tag:
            observability.init_sentry("worker")

        mock_init.assert_called_once()
        kwargs = mock_init.call_args.kwargs
        assert kwargs["dsn"] == "https://x@example.ingest.sentry.io/1"
        assert kwargs["environment"] == "production"
        assert kwargs["traces_sample_rate"] == 0.0
        mock_tag.assert_called_once_with("component", "worker")


@pytest.fixture(autouse=True)
def _restore_logging_after_each_test():
    """Tests mutate the root logger; restore baseline afterwards."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
