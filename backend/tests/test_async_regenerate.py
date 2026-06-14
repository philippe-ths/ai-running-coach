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
        args = fake_queue.enqueue.call_args.args
        from app.jobs.process_new_activity import regenerate_report_job
        assert args[0] is regenerate_report_job
        assert args[1] == str(activity.id)

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
