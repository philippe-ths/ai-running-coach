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


class TestInitSentryWithoutSDK:
    """Sentry is optional (issue #102): with the SDK absent, init_sentry is a
    no-op regardless of SENTRY_DSN, and importing the module never fails."""

    def test_noop_when_sdk_unavailable_even_with_dsn_set(self, monkeypatch):
        monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", False)
        monkeypatch.setattr(settings, "SENTRY_DSN", "https://x@example.ingest.sentry.io/1")
        # Must not raise (no sentry_sdk reference is reached) and must do nothing.
        observability.init_sentry("web")


@pytest.mark.skipif(
    not observability._SENTRY_AVAILABLE,
    reason="sentry_sdk not installed (install the 'observability' extra)",
)
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

    def test_swallows_init_exception_so_process_still_boots(self, monkeypatch, caplog):
        """A malformed DSN, a startup-time network policy, or a bad integration
        kwarg used to abort the import of any process that called init_sentry
        at module import time. The wrap catches the failure, logs it, and lets
        the process keep booting without Sentry."""
        monkeypatch.setattr(settings, "SENTRY_DSN", "this-is-not-a-real-dsn")

        def _boom(*_args, **_kwargs):
            raise Exception("malformed DSN")

        with patch.object(observability.sentry_sdk, "init", side_effect=_boom), \
                patch.object(observability.sentry_sdk, "set_tag") as mock_tag, \
                caplog.at_level(logging.ERROR, logger="app.core.observability"):
            observability.init_sentry("web")

        # set_tag must not run if init failed
        mock_tag.assert_not_called()
        # A failure to init is loud but not fatal
        assert any("sentry" in rec.message.lower() for rec in caplog.records)


class TestSentryCaptureActive:
    """`sentry_capture_active` is the single source of truth for "capture on".
    It requires BOTH the SDK installed and a DSN set (issue #102: logs-only by
    default). A DSN alone must not report capture as active."""

    def test_inactive_when_dsn_unset(self, monkeypatch):
        monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", True)
        monkeypatch.setattr(settings, "SENTRY_DSN", "")
        assert observability.sentry_capture_active() is False

    def test_inactive_when_sdk_absent_even_with_dsn_set(self, monkeypatch):
        # The dead-signal case from #105: a DSN is configured but the optional
        # SDK is not installed, so nothing actually captures.
        monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", False)
        monkeypatch.setattr(settings, "SENTRY_DSN", "https://x@example.ingest.sentry.io/1")
        assert observability.sentry_capture_active() is False

    def test_active_only_when_sdk_present_and_dsn_set(self, monkeypatch):
        monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", True)
        monkeypatch.setattr(settings, "SENTRY_DSN", "https://x@example.ingest.sentry.io/1")
        assert observability.sentry_capture_active() is True


class TestSecretRedaction:
    """Telegram bot tokens ride in the Bot API URL path, which httpx logs at
    INFO. The redaction filter must mask the secret without dropping the line
    or touching other (safe) request logs. See #131."""

    FAKE_TOKEN = "bot123456789:AAFakeSecret_tokenPART-xyz123"

    def test_redacts_token_in_plain_string(self):
        out = observability._redact_secrets(f"sent via {self.FAKE_TOKEN} ok")
        assert "AAFakeSecret_tokenPART-xyz123" not in out
        assert "bot123456789:<redacted>" in out

    def test_redacts_token_inside_url(self):
        url = f"https://api.telegram.org/{self.FAKE_TOKEN}/sendMessage"
        assert (
            observability._redact_secrets(url)
            == "https://api.telegram.org/bot123456789:<redacted>/sendMessage"
        )

    def test_leaves_non_matching_string_as_same_object(self):
        s = "https://www.strava.com/api/v3/athlete/activities?page=1"
        # Strava uses header auth, no secret in the URL: must be untouched.
        assert observability._redact_secrets(s) is s

    def test_preserves_non_string_type_when_no_secret(self):
        result = observability._redact_secrets(200)
        assert result == 200 and isinstance(result, int)

    def test_filter_masks_httpx_style_record(self):
        # Mirrors how httpx emits a request log: msg with %s, URL in args.
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='HTTP Request: %s %s "%s"',
            args=("POST", f"https://api.telegram.org/{self.FAKE_TOKEN}/sendMessage", "HTTP/1.1 200 OK"),
            exc_info=None,
        )
        assert observability.SecretRedactingFilter().filter(record) is True
        message = record.getMessage()
        assert "AAFakeSecret_tokenPART-xyz123" not in message
        assert "bot123456789:<redacted>" in message

    def test_filter_masks_non_string_url_arg(self):
        # httpx passes an httpx.URL object, not a str; redaction works on str().
        class _FakeURL:
            def __str__(self):
                return f"https://api.telegram.org/{TestSecretRedaction.FAKE_TOKEN}/sendMessage"

        record = logging.LogRecord(
            name="httpx", level=logging.INFO, pathname=__file__, lineno=1,
            msg="HTTP Request: %s", args=(_FakeURL(),), exc_info=None,
        )
        observability.SecretRedactingFilter().filter(record)
        assert "AAFakeSecret_tokenPART-xyz123" not in record.getMessage()

    def test_init_logging_wires_redaction_end_to_end(self, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "production")
        observability.init_logging()
        buffer = io.StringIO()
        logging.getLogger().handlers[0].stream = buffer

        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s"',
            "POST",
            f"https://api.telegram.org/{self.FAKE_TOKEN}/sendMessage",
            "HTTP/1.1 200 OK",
        )

        out = buffer.getvalue()
        assert "AAFakeSecret_tokenPART-xyz123" not in out
        assert "bot123456789:<redacted>" in out


class TestWarnNotificationConfig:
    """Non-fatal production boot warning for incomplete Telegram config (#600/#609)."""

    def _telegram_active(self, monkeypatch, *, env="production", username="bot", owner="o@x.dev"):
        monkeypatch.setattr(settings, "APP_ENV", env)
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "GLOBAL")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", username)
        monkeypatch.setattr(settings, "OWNER_EMAIL", owner)

    def test_noop_outside_production(self, monkeypatch, caplog):
        # Missing everything, but not production => silent (dev/test run unset).
        self._telegram_active(monkeypatch, env="local", username="", owner="")
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config()
        assert not caplog.records

    def test_noop_when_telegram_not_active(self, monkeypatch, caplog):
        # Telegram channel off (no token/chat) => these vars are irrelevant.
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "")
        monkeypatch.setattr(settings, "OWNER_EMAIL", "")
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config()
        assert not caplog.records

    def test_silent_when_fully_configured(self, monkeypatch, caplog):
        self._telegram_active(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config()
        assert not caplog.records

    def test_warns_when_bot_username_unset(self, monkeypatch, caplog):
        # #609: linking breaks silently without TELEGRAM_BOT_USERNAME.
        self._telegram_active(monkeypatch, username="")
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config()
        assert any("TELEGRAM_BOT_USERNAME" in r.getMessage() + str(getattr(r, "missing", "")) for r in caplog.records)

    def test_warns_when_owner_email_unset(self, monkeypatch, caplog):
        # #600: without OWNER_EMAIL the owner's own unbound fallback is suppressed.
        self._telegram_active(monkeypatch, owner="")
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config()
        assert any("OWNER_EMAIL" in r.getMessage() + str(getattr(r, "missing", "")) for r in caplog.records)

    def test_never_raises(self, monkeypatch):
        # Non-fatal by design (the #549 lesson): must never crash the boot.
        self._telegram_active(monkeypatch, username="", owner="")
        observability.warn_notification_config()  # no exception

    # --- per-process scoping (#795) --------------------------------------------

    def test_worker_warns_when_owner_email_unset(self, monkeypatch, caplog):
        """#795: the worker SENDS, so an OWNER_EMAIL it lacks is its own problem.

        This is the exact production configuration that leaked — set on `web`,
        absent on `worker` — and it booted without a word, because #609 wired the
        warning into the web process only.
        """
        self._telegram_active(monkeypatch, username="bot", owner="")
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config("worker")
        assert any("OWNER_EMAIL" in r.getMessage() + str(getattr(r, "missing", ""))
                   for r in caplog.records)

    def test_worker_ignores_bot_username(self, monkeypatch, caplog):
        # TELEGRAM_BOT_USERNAME mints the /start deep link, which only web does.
        # Warning the worker about it would train the operator to ignore this line.
        self._telegram_active(monkeypatch, username="", owner="o@x.dev")
        with caplog.at_level(logging.WARNING, logger="app.core.observability"):
            observability.warn_notification_config("worker")
        assert not caplog.records

    def test_worker_boot_checks_its_own_notification_config(self, monkeypatch):
        """The wiring, not just the function: `python -m app.worker` must call it.

        Pinned because the #795 defect was never in this function — it was in which
        processes bothered to call it.
        """
        from app import worker as worker_module

        called: list[str] = []
        monkeypatch.setattr(worker_module, "warn_notification_config",
                            lambda process="web": called.append(process))
        for name in ("init_logging", "init_sentry", "warn_if_coach_prompt_inert",
                     "log_budget_cap_status"):
            monkeypatch.setattr(worker_module, name, lambda *a, **k: None)
        monkeypatch.setattr(worker_module, "build_worker_runtime", lambda *a, **k: object())
        monkeypatch.setattr(worker_module, "run_worker_runtime", lambda *a, **k: None)

        worker_module.main()
        assert called == ["worker"]


@pytest.fixture(autouse=True)
def _restore_logging_after_each_test():
    """Tests mutate the root logger; restore baseline afterwards."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
