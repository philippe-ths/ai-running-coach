"""#260: on-demand coach-report regeneration is asynchronous.

The synchronous force path (the "Re-run" button) made a 30-120s two-stage LLM call
inline and 504'd at the gateway. Regeneration now enqueues a worker job and returns
202 immediately; the frontend polls for the fresh report.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

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

    def test_passes_deterministic_and_rq_legal_job_id(self, client: TestClient, db):
        # P2.2: a deterministic per-activity job id lets RQ collapse a racing
        # re-tap onto one job instead of queuing duplicate generations. It MUST
        # also be a legal RQ job id: RQ rejects an id outside "letters, numbers,
        # underscores and dashes" (a ":" silently 503'd every regeneration in
        # prod, #490). The mocked queue does not run RQ's validation, so assert
        # the charset rule here. (Asserting the rule rather than calling RQ's
        # validator keeps this independent of RQ-version-specific internals.)
        import re

        activity = _seed_activity(db)
        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        job_id = fake_queue.enqueue.call_args.kwargs.get("job_id")
        assert job_id == f"coach-regenerate-{activity.id}"  # deterministic per activity
        assert re.fullmatch(r"[A-Za-z0-9_-]+", job_id), f"RQ-illegal job id: {job_id!r}"

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
             patch("app.services.coach.turn.AnthropicClient",
                   side_effect=AssertionError("must not call the LLM in the request")):
            res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert res.status_code == 202


class TestRegenerateEnqueueFailure:
    """#483: a queue/Redis failure on START must never surface a raw 500.

    The cooldown guard degrades open on a Redis error, so an enqueue failure lands
    in the request with the job unstarted. Before the fix the unguarded enqueue
    propagated as a 500 ("Couldn't start regeneration (500)"); now it returns an
    actionable 503 and frees the cooldown so "Try again" can re-enqueue.
    """

    def test_enqueue_failure_returns_503_not_500(self, client: TestClient, db):
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        fake_queue.connection.set.return_value = True  # cooldown acquired
        fake_queue.enqueue.side_effect = RedisConnectionError("redis down")
        with patch("app.core.queue.queue", fake_queue):
            res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert res.status_code == 503  # not a raw 500
        # the runner gets an actionable message, not a stack trace
        assert "try again" in res.json()["detail"].lower()

    def test_enqueue_failure_frees_cooldown_so_retry_can_reenqueue(self, client: TestClient, db):
        # The cooldown key was set for a job that never started; it must be cleared
        # so the next tap is not short-circuited to {"status": "cooldown"}.
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        fake_queue.connection.set.return_value = True
        fake_queue.enqueue.side_effect = RedisConnectionError("redis down")
        with patch("app.core.queue.queue", fake_queue):
            client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        fake_queue.connection.delete.assert_called_once_with(
            f"coach_regen_cooldown:{activity.id}"
        )

    def test_cooldown_delete_failure_still_returns_503(self, client: TestClient, db):
        # Redis fully unreachable: even the best-effort cooldown cleanup raises. The
        # endpoint must still degrade to 503, never a 500.
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        fake_queue.connection.set.return_value = True
        fake_queue.enqueue.side_effect = RedisConnectionError("redis down")
        fake_queue.connection.delete.side_effect = RedisConnectionError("redis down")
        with patch("app.core.queue.queue", fake_queue):
            res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert res.status_code == 503

    def test_healthy_enqueue_unchanged(self, client: TestClient, db):
        # Guard must not change the happy path: a healthy queue still enqueues + 202s.
        activity = _seed_activity(db)
        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            res = client.post(f"/api/activities/{activity.id}/coach-report/regenerate")
        assert res.status_code == 202
        assert res.json() == {"status": "regenerating"}
        fake_queue.enqueue.assert_called_once()
        fake_queue.connection.delete.assert_not_called()  # cooldown untouched on success


class TestRegenerateJob:
    def test_job_reanalyzes_then_delegates_to_force_regeneration(self):
        # #646: the Re-run job re-derives analysis from stored streams FIRST (so the
        # report reflects current deployed analysis code), then force-regenerates.
        from app.jobs import process_new_activity as job_mod

        fake_db = MagicMock()
        with patch.object(job_mod, "SessionLocal", return_value=fake_db), \
             patch.object(job_mod, "reanalyze_activity_if_streams", return_value=True) as reanalyze, \
             patch.object(job_mod, "get_or_generate_coach_report", new=AsyncMock(return_value=None)) as gen:
            job_mod.regenerate_report_job("11111111-1111-1111-1111-111111111111")
        # re-analysis runs before the regen, against the same activity/session.
        reanalyze.assert_called_once()
        assert reanalyze.call_args.args[1] == "11111111-1111-1111-1111-111111111111"
        gen.assert_awaited_once()
        # force=True is the whole point: it regenerates the active-version row.
        assert gen.await_args.kwargs.get("force") is True
        fake_db.close.assert_called_once()  # session is always closed

    def test_job_proceeds_when_reanalysis_fails(self):
        # #646: re-analysis is best-effort — a failure must NOT block the regen.
        from app.jobs import process_new_activity as job_mod

        fake_db = MagicMock()
        with patch.object(job_mod, "SessionLocal", return_value=fake_db), \
             patch.object(job_mod, "reanalyze_activity_if_streams",
                          side_effect=RuntimeError("analysis blew up")), \
             patch.object(job_mod, "get_or_generate_coach_report", new=AsyncMock(return_value=None)) as gen:
            job_mod.regenerate_report_job("11111111-1111-1111-1111-111111111111")
        gen.assert_awaited_once()  # regen still ran with existing metrics
        assert gen.await_args.kwargs.get("force") is True
        fake_db.close.assert_called_once()


class TestJobTimeouts:
    """#264: coach-generation jobs must outlast RQ's 180s default death penalty."""

    def test_queue_default_timeout_exceeds_rq_default(self):
        from app.core.config import settings
        from app.core.queue import queue
        assert settings.RQ_JOB_TIMEOUT_SECONDS > 180
        assert queue._default_timeout == settings.RQ_JOB_TIMEOUT_SECONDS

    def test_scheduled_fuller_turn_sets_timeout(self):
        # #123/ADR 0006: deferred jobs now use RQ-native queue.enqueue_in (drained
        # by the worker's with_scheduler), and RQ-native uses `job_timeout`, not
        # rq-scheduler's `timeout`.
        from app.core.config import settings
        from app.jobs import exchange_ops

        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            exchange_ops.schedule_fuller_turn("act-1")
        assert fake_queue.enqueue_in.call_args.kwargs.get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS

    def test_scheduled_block_complete_sets_timeout(self):
        from app.core.config import settings
        from app.jobs import exchange_ops

        fake_queue = MagicMock()
        with patch("app.core.queue.queue", fake_queue):
            exchange_ops.schedule_block_complete("block-1", "act-1")
        assert fake_queue.enqueue_in.call_args.kwargs.get("job_timeout") == settings.RQ_JOB_TIMEOUT_SECONDS
