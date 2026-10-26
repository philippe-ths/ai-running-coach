"""Verify webhook event authentication and per-aspect_type job dispatch.

Strava does not sign webhook payloads (#100), so the handler authenticates
each event against the connected athlete and (when configured) the active
subscription id before doing any work. These tests cover both the dispatch
behaviour for authentic events and the rejection of forged ones.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, StravaAccount, User

# The athlete id that the seeded connected account owns. Authentic events in
# these tests carry this owner_id; forged events carry a different one.
CONNECTED_ATHLETE_ID = 999


@pytest.fixture
def fake_queue():
    fake = MagicMock()
    with patch("app.api.webhooks.queue", fake):
        yield fake


@pytest.fixture
def connected_account(db):
    """Seed the single connected athlete so authentic events pass the owner
    check. Without this, every event is rejected as an unknown owner."""
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=CONNECTED_ATHLETE_ID,
        access_token="test-token-do-not-use-in-prod",
        refresh_token="test-refresh-do-not-use-in-prod",
        expires_at=9999999999,
        scope="read",
    )
    db.add(account)
    db.commit()
    return account


def _post(
    client,
    *,
    aspect_type: str,
    object_id: int = 7777,
    owner_id: int = CONNECTED_ATHLETE_ID,
    subscription_id: int = 1,
):
    payload = {
        "object_type": "activity",
        "object_id": object_id,
        "aspect_type": aspect_type,
        "owner_id": owner_id,
        "subscription_id": subscription_id,
        "event_time": 1700000000,
        "updates": {},
    }
    return client.post("/api/webhooks/strava", json=payload)


class TestDispatchForAuthenticEvents:
    def test_create_enqueues_process_new_activity_job(
        self, client, fake_queue, connected_account
    ):
        from app.jobs.process_new_activity import process_new_activity_job

        response = _post(client, aspect_type="create")
        assert response.status_code == 200
        assert response.json()["status"] == "processed"

        fake_queue.enqueue.assert_called_once()
        args, kwargs = fake_queue.enqueue.call_args
        assert args[0] is process_new_activity_job
        assert kwargs["strava_athlete_id"] == CONNECTED_ATHLETE_ID
        assert kwargs["strava_activity_id"] == 7777

    def test_create_enqueue_carries_retry_policy(
        self, client, fake_queue, connected_account
    ):
        # #215: a worker crash mid-pipeline (after ingest, before the opener is
        # scheduled/notified) strands the exchange unless RQ re-runs the job —
        # polling never re-picks an already-ingested activity. The pipeline
        # enqueue must carry the shared bounded Retry policy.
        from app.jobs.process_new_activity import PIPELINE_RETRY

        response = _post(client, aspect_type="create")
        assert response.status_code == 200

        _, kwargs = fake_queue.enqueue.call_args
        assert kwargs["retry"] is PIPELINE_RETRY
        assert PIPELINE_RETRY.max >= 1

    def test_update_enqueues_existing_sync_activity_job(
        self, client, db, fake_queue, connected_account
    ):
        from app.jobs.strava_sync import sync_activity_job

        _seed_activity(db, user=connected_account.user, strava_activity_id=7777)
        response = _post(client, aspect_type="update")
        assert response.status_code == 200

        fake_queue.enqueue.assert_called_once()
        args, kwargs = fake_queue.enqueue.call_args
        assert args[0] is sync_activity_job

    def test_delete_does_not_enqueue(self, client, fake_queue, connected_account):
        response = _post(client, aspect_type="delete")
        assert response.status_code == 200
        fake_queue.enqueue.assert_not_called()


class TestRejectsForgedEvents:
    """Threat model: an attacker who learns the callback URL POSTs a forged
    but well-formed event. The attacker must not be able to enqueue a job or
    mutate any activity."""

    def test_unknown_owner_is_rejected_without_enqueue(
        self, client, fake_queue, connected_account
    ):
        # No connected account owns athlete 12345.
        response = _post(client, aspect_type="create", owner_id=12345)
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_no_connected_account_rejects_everything(self, client, fake_queue):
        # Nothing seeded: there is no connected athlete, so no event is authentic.
        response = _post(client, aspect_type="create")
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_subscription_mismatch_is_rejected_without_enqueue(
        self, client, fake_queue, connected_account, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_SUBSCRIPTION_ID", 350809)
        # Authentic owner, but the event references a different subscription.
        response = _post(client, aspect_type="create", subscription_id=1)
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_matching_subscription_and_owner_is_accepted(
        self, client, fake_queue, connected_account, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_SUBSCRIPTION_ID", 350809)
        response = _post(client, aspect_type="create", subscription_id=350809)
        assert response.status_code == 200
        fake_queue.enqueue.assert_called_once()

    def test_forged_delete_does_not_soft_delete_activity(self, client, db, fake_queue):
        """The delete branch soft-deletes by object_id. A forged delete event
        must not be able to remove an activity it does not own."""
        from datetime import datetime

        user = User(email=f"u-{uuid4()}@example.com")
        db.add(user)
        db.commit()
        activity = Activity(
            user_id=user.id,
            strava_activity_id=5555,
            start_date=datetime(2026, 5, 27, 10, 0, 0),
            type="Run",
            name="Victim run",
            distance_m=5000,
            moving_time_s=1500,
            elapsed_time_s=1500,
        )
        db.add(activity)
        db.commit()

        # Forged delete from an owner with no connected account.
        response = _post(client, aspect_type="delete", object_id=5555, owner_id=12345)
        assert response.status_code == 403

        db.refresh(activity)
        assert activity.is_deleted is False


# A second connected athlete, distinct from CONNECTED_ATHLETE_ID, used to model
# the multi-user cross-owner attack: athlete B is fully connected (passes the
# owner and subscription checks) but references athlete A's activity.
ATHLETE_B_ID = 888


def _seed_user_with_account(db, *, strava_athlete_id: int):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=strava_athlete_id,
        access_token="test-token-do-not-use-in-prod",
        refresh_token="test-refresh-do-not-use-in-prod",
        expires_at=9999999999,
        scope="read",
    )
    db.add(account)
    db.commit()
    return user


def _seed_activity(db, *, user, strava_activity_id: int):
    from datetime import datetime

    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_activity_id,
        start_date=datetime(2026, 5, 27, 10, 0, 0),
        type="Run",
        name="Victim run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
    )
    db.add(activity)
    db.commit()
    return activity


class TestRejectsCrossOwnerEvents:
    """Threat model (#512, multi-user): athlete B is a CONNECTED runner, so a
    forged event from B passes the owner-is-connected and subscription checks.
    Because strava_activity_id is globally unique, B must still not be able to
    reference athlete A's activity and trigger a side effect on it (soft-delete
    or reprocess). The object-to-owner bind in _event_is_authentic enforces this;
    removing that bind makes every assertion here fail (non-vacuous)."""

    def test_connected_other_owner_delete_does_not_soft_delete(
        self, client, db, fake_queue
    ):
        athlete_a = _seed_user_with_account(db, strava_athlete_id=CONNECTED_ATHLETE_ID)
        _seed_user_with_account(db, strava_athlete_id=ATHLETE_B_ID)
        activity = _seed_activity(db, user=athlete_a, strava_activity_id=4242)

        # Athlete B (connected) forges a delete for athlete A's activity.
        response = _post(
            client, aspect_type="delete", object_id=4242, owner_id=ATHLETE_B_ID
        )
        assert response.status_code == 403

        db.refresh(activity)
        assert activity.is_deleted is False

    def test_connected_other_owner_update_does_not_enqueue(
        self, client, db, fake_queue
    ):
        athlete_a = _seed_user_with_account(db, strava_athlete_id=CONNECTED_ATHLETE_ID)
        _seed_user_with_account(db, strava_athlete_id=ATHLETE_B_ID)
        _seed_activity(db, user=athlete_a, strava_activity_id=4343)

        # Athlete B (connected) forges an update for athlete A's activity: must
        # not enqueue a re-ingest (sync_activity_job) on A's data.
        response = _post(
            client, aspect_type="update", object_id=4343, owner_id=ATHLETE_B_ID
        )
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_connected_other_owner_create_when_already_exists_does_not_enqueue(
        self, client, db, fake_queue
    ):
        athlete_a = _seed_user_with_account(db, strava_athlete_id=CONNECTED_ATHLETE_ID)
        _seed_user_with_account(db, strava_athlete_id=ATHLETE_B_ID)
        _seed_activity(db, user=athlete_a, strava_activity_id=4444)

        # A create referencing an already-stored activity owned by A must not
        # reprocess it under B's credentials.
        response = _post(
            client, aspect_type="create", object_id=4444, owner_id=ATHLETE_B_ID
        )
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_same_owner_delete_still_soft_deletes(self, client, db, fake_queue):
        # Non-vacuous guard: the legitimate owner deleting their own activity
        # must still succeed through the same code path.
        athlete_a = _seed_user_with_account(db, strava_athlete_id=CONNECTED_ATHLETE_ID)
        activity = _seed_activity(db, user=athlete_a, strava_activity_id=4545)

        response = _post(
            client,
            aspect_type="delete",
            object_id=4545,
            owner_id=CONNECTED_ATHLETE_ID,
        )
        assert response.status_code == 200

        db.refresh(activity)
        assert activity.is_deleted is True

    def test_same_owner_update_still_enqueues(self, client, db, fake_queue):
        athlete_a = _seed_user_with_account(db, strava_athlete_id=CONNECTED_ATHLETE_ID)
        _seed_activity(db, user=athlete_a, strava_activity_id=4646)

        response = _post(
            client,
            aspect_type="update",
            object_id=4646,
            owner_id=CONNECTED_ATHLETE_ID,
        )
        assert response.status_code == 200
        fake_queue.enqueue.assert_called_once()


class TestWebhookDeliveredTwice:
    """#1136: Strava retries a webhook it thinks was not acknowledged, with the
    same event. RQ rejects the repeated job id only under `unique=True`, so
    without it the pipeline runs twice in parallel for one activity."""

    @pytest.fixture
    def unique_queue(self):
        from tests._unique_queue import UniqueAwareQueue

        q = UniqueAwareQueue()
        with patch("app.api.webhooks.queue", q):
            yield q

    def test_repeated_create_event_runs_the_pipeline_once(
        self, client, unique_queue, connected_account
    ):
        first = _post(client, aspect_type="create")
        second = _post(client, aspect_type="create")

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(unique_queue.runs) == 1

    def test_repeated_update_event_runs_once(
        self, client, db, unique_queue, connected_account
    ):
        _seed_activity(db, user=connected_account.user, strava_activity_id=7777)
        _post(client, aspect_type="update")
        second = _post(client, aspect_type="update")

        assert second.status_code == 200
        assert len(unique_queue.runs) == 1


class TestUpdateForAnActivityWeNeverStored:
    """#1136: an `update` for an activity with no row (its `create` was lost)
    used to store a summary-only row through the sync job; self-heal then
    treated it as known, so it was never analysed or reported. It must run the
    full pipeline instead, as a `create` would."""

    def test_update_without_a_row_runs_the_full_pipeline(
        self, client, db, fake_queue, connected_account
    ):
        from app.jobs.process_new_activity import PIPELINE_RETRY, process_new_activity_job

        _seed_activity(db, user=connected_account.user, strava_activity_id=7000)
        response = _post(client, aspect_type="update", object_id=8888)
        assert response.status_code == 200

        fake_queue.enqueue.assert_called_once()
        args, kwargs = fake_queue.enqueue.call_args
        assert args[0] is process_new_activity_job
        assert kwargs["strava_athlete_id"] == CONNECTED_ATHLETE_ID
        assert kwargs["strava_activity_id"] == 8888
        assert kwargs["retry"] is PIPELINE_RETRY

    def test_update_to_an_old_unstored_activity_is_only_synced(
        self, client, db, fake_queue, connected_account
    ):
        # An edit to an activity older than our history must not send a receipt
        # and a report about a long-past session.
        from app.jobs.strava_sync import sync_activity_job

        _seed_activity(db, user=connected_account.user, strava_activity_id=9000)
        response = _post(client, aspect_type="update", object_id=8888)
        assert response.status_code == 200

        args, _ = fake_queue.enqueue.call_args
        assert args[0] is sync_activity_job

    def test_update_with_nothing_stored_is_only_synced(
        self, client, fake_queue, connected_account
    ):
        from app.jobs.strava_sync import sync_activity_job

        response = _post(client, aspect_type="update", object_id=8888)
        assert response.status_code == 200

        args, _ = fake_queue.enqueue.call_args
        assert args[0] is sync_activity_job


class TestDeletingTheMemberThatHeldTheCheck:
    """#1136: every processed activity schedules a block-complete check, and only
    the check of the member that ends last is live. Deleting that member (a
    duplicate upload, say) left the survivors with no live check, so the full
    report never fired. The survivor that now ends last must get one."""

    @pytest.fixture
    def deferred(self):
        from tests._unique_queue import UniqueAwareQueue

        q = UniqueAwareQueue()
        with patch("app.core.queue.queue", q):
            yield q

    def _block_of_two(self, db, user):
        from datetime import datetime

        from app.services.blocks import assign_activity_to_block

        first = _seed_activity(db, user=user, strava_activity_id=6001)
        later = _seed_activity(db, user=user, strava_activity_id=6002)
        later.start_date = datetime(2026, 5, 27, 10, 30, 0)
        db.commit()
        assign_activity_to_block(db, first)
        block = assign_activity_to_block(db, later)
        assert first.block_id == later.block_id == block.id
        return first, later, block

    def test_deleting_the_last_member_schedules_the_survivors_check(
        self, client, db, fake_queue, deferred, connected_account
    ):
        from app.jobs.process_new_activity import block_complete_job

        first, _later, block = self._block_of_two(db, connected_account.user)

        response = _post(client, aspect_type="delete", object_id=6002)
        assert response.status_code == 200

        assert [(f, a) for f, a, _ in deferred.runs] == [
            (block_complete_job, (str(block.id), str(first.id)))
        ]

    def test_deleting_an_earlier_member_schedules_nothing(
        self, client, db, fake_queue, deferred, connected_account
    ):
        self._block_of_two(db, connected_account.user)

        _post(client, aspect_type="delete", object_id=6001)

        assert deferred.runs == []  # the later member's own check is still live

    def test_deleting_from_a_closed_exchange_schedules_nothing(
        self, client, db, fake_queue, deferred, connected_account
    ):
        from datetime import datetime, timezone

        from app.models import Exchange

        _first, _later, block = self._block_of_two(db, connected_account.user)
        ex = db.query(Exchange).filter(Exchange.block_id == block.id).one()
        ex.fuller_sent_at = datetime.now(timezone.utc)
        db.commit()

        _post(client, aspect_type="delete", object_id=6002)

        assert deferred.runs == []
