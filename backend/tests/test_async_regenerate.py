"""#260: on-demand coach-report regeneration is asynchronous.

The synchronous force path (the "Re-run" button) made a 30-120s two-stage LLM call
inline and 504'd at the gateway. Regeneration now enqueues a worker job and returns
202 immediately; the frontend polls for the fresh report.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.test_coach_report_versioning import _seed_activity


class TestRegenerateEndpoint:
    def test_enqueues_job_and_returns_202(self, client: TestClient, db):
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert res.status_code == 202
        assert res.json() == {"status": "regenerating"}
        # the regeneration was handed to the worker, not run inline
        fake_queue.enqueue.assert_called_once()
        call = fake_queue.enqueue.call_args
        from app.jobs.process_new_activity import regenerate_report_job
        assert call.args[0] is regenerate_report_job
        assert call.args[1] == str(activity.id)
        # #264: the job timeout must exceed RQ's 180s default or a slow two-stage
        # generation is killed before it stores the report.
        from app.core.config import settings
        assert call.kwargs.get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS
        assert settings.RQ_JOB_TIMEOUT_SECONDS > 180

    def test_passes_deterministic_job_id(self, client: TestClient, db):
        # P2.2: a deterministic per-activity job id lets RQ collapse a racing
        # re-tap onto one job instead of queuing duplicate generations.
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert fake_queue.enqueue.call_args.kwargs.get("job_id") == (
            f"coach-regenerate:{activity.id}"
        )

    def test_cooldown_dedups_rapid_retaps(self, client: TestClient, db):
        # P2.2 / going-live landmine 1: the atomic Redis cooldown (SET NX EX)
        # short-circuits a second rapid regenerate so a stuck button cannot fan
        # out LLM spend. First tap acquires the key (set -> True), second is
        # within the window (set -> None).
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        fake_queue.connection.set.side_effect = [True, None]
        with patch("app.core.queue.queue", fake_queue):
            first = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
            second = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert first.json() == {"status": "regenerating"}
        assert second.json() == {"status": "cooldown"}
        fake_queue.enqueue.assert_called_once()  # only the first tap enqueued

    def test_404_when_activity_missing(self, client: TestClient, db):
        from uuid import uuid4
        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            res = client.post(f"/api/activities/{uuid4()}/coach-report/regenerate")
        assert res.status_code == 404
        fake_queue.enqueue.assert_not_called()

    def test_no_inline_llm_call(self, client: TestClient, db):
        # The request must never construct the Anthropic client (that is the worker's
        # job now) — proving the gateway-timeout path is gone.
        activity = _seed_activity(db)
        with patch("app.core.queue.queue", MagicMock()), \
             patch("app.services.coach.service.AnthropicClient",
                   side_effect=AssertionError("must not call the LLM in the request")):
            res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert res.status_code == 202


class TestRegenerateJob:
    def test_job_delegates_to_force_regeneration(self):
        from app.jobs import process_new_activity as job_mod

        fake_db = MagicMock()
        with patch.object(job_mod, "SessionLocal", return_value=fake_db), \
             patch.object(job_mod, "get_or_generate_coach_report", new=AsyncMock(return_value=None)) as gen:
            job_mod.regenerate_report_job("11111111-1111-1111-1111-111111111111")
        gen.assert_awaited_once()
        # force=True is the whole point: it regenerates the active-version row.
        assert gen.await_args.kwargs.get("force") is True
        fake_db.close.assert_called_once()  # session is always closed


class TestJobTimeouts:
    """#264: coach-generation jobs must outlast RQ's 180s default death penalty."""

    def test_queue_default_timeout_exceeds_rq_default(self):
        from app.core.config import settings
        from app.core.queue import queue
        assert settings.RQ_JOB_TIMEOUT_SECONDS > 180
        assert queue._default_timeout == settings.RQ_JOB_TIMEOUT_SECONDS

    def test_scheduled_fuller_turn_sets_timeout(self):
        from app.core.config import settings
        from app.jobs import process_new_activity as job_mod

        fake_scheduler = MagicMock()
        with patch("rq_scheduler.Scheduler", return_value=fake_scheduler), \
             patch("redis.Redis.from_url", return_value=MagicMock()):
            job_mod._schedule_fuller_turn("act-1")
        assert fake_scheduler.enqueue_in.call_args.kwargs.get("timeout") == settings.RQ_JOB_TIMEOUT_SECONDS

    def test_scheduled_block_complete_sets_timeout(self):
        from app.core.config import settings
        from app.jobs import process_new_activity as job_mod

        fake_scheduler = MagicMock()
        with patch("rq_scheduler.Scheduler", return_value=fake_scheduler), \
             patch("redis.Redis.from_url", return_value=MagicMock()):
            job_mod._schedule_block_complete("block-1", "act-1")
        assert fake_scheduler.enqueue_in.call_args.kwargs.get("timeout") == settings.RQ_JOB_TIMEOUT_SECONDS
