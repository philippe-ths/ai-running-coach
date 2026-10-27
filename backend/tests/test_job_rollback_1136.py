"""#1136: recovery work after a database error rolls the failed transaction back
first.

When a flush fails, the session refuses every later statement until it is rolled
back. Recovery code that skips the rollback (releasing a claim, marking a row
failed, marking an activity attempted) raises instead of recovering, and the row
is left in the state the recovery was meant to undo.

These run on `savepoint_db` (see conftest), where a rollback undoes only the
failed work, as on a real database.
"""

from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models import Activity, Exchange, StravaAccount, User
from app.services.blocks import assign_activity_to_block
from app.services.coach import exchange_lifecycle as lifecycle
from tests.conftest import TestingSessionLocal


@pytest.fixture
def real_db(savepoint_db):
    return savepoint_db


def _fresh(real_db):
    """A second session on the same connection, to read what was really kept."""
    return TestingSessionLocal(
        bind=real_db.connection(), join_transaction_mode="create_savepoint"
    )


def _user(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _fail_a_flush(db) -> None:
    """Break the session's transaction the way a real write error does."""
    existing = db.query(User).first()
    db.add(User(email=existing.email))  # unique email -> IntegrityError on flush
    db.flush()


def _activity(db, user, strava_id: int) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_id,
        start_date=datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        type="Run",
        name="Run",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
    )
    db.add(activity)
    db.commit()
    return activity


def test_fuller_claim_is_released_after_a_database_error(real_db):
    # The claim sets `fuller_sent_at`, the CLOSED sentinel. A DB error inside the
    # turn left the release UPDATE refused, so the exchange stayed CLOSED with
    # nothing sent and every retry bailed on the claim.
    user = _user(real_db)
    activity = _activity(real_db, user, 9101)
    block = assign_activity_to_block(real_db, activity)
    exchange = lifecycle.get_exchange_for_block(real_db, block.id)
    exchange_id = exchange.id

    with pytest.raises(Exception):
        with lifecycle.fuller_claim(real_db, exchange) as claim:
            assert claim.won
            _fail_a_flush(real_db)

    real_db.rollback()
    kept = _fresh(real_db).get(Exchange, exchange_id)
    assert kept.fuller_sent_at is None


@pytest.mark.asyncio
async def test_backfill_marks_the_activity_after_a_database_error(real_db):
    # A DB error inside one activity's analysis made the attempted-marker commit
    # raise, sinking the batch, and the activity stayed eligible forever.
    from app.jobs import backfill_streams as mod

    user = _user(real_db)
    real_db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=9102, access_token="t", refresh_token="r",
        expires_at=9999999999, scope="read,activity:read_all",
    ))
    real_db.commit()
    activity = _activity(real_db, user, 9102)
    activity_id = activity.id

    async def _breaks_the_session(db, _activity_id):
        _fail_a_flush(db)

    with patch.object(mod, "analyze_with_streams", _breaks_the_session):
        result = await mod.backfill_streams_batch(real_db, limit=5, user_id=str(user.id))

    assert result.processed == [9102]
    kept = _fresh(real_db).get(Activity, activity_id)
    assert kept.streams_backfilled_at is not None


def test_schedule_draft_is_failed_after_a_database_error(real_db):
    # The job's recovery read the plan on the broken session, raised, and the
    # plan stayed "drafting" until the staleness sweep.
    from app.jobs import generate_schedule as job_mod
    from app.models.thread import Thread
    from app.services.schedule import store

    user = _user(real_db)
    thread = Thread(user_id=user.id)
    real_db.add(thread)
    real_db.commit()
    plan = store.create_drafting_plan(real_db, user.id)
    plan_id = plan.id

    async def _breaks_the_session(db, *_args, **_kwargs):
        _fail_a_flush(db)

    reader = _fresh(real_db)
    with patch.object(job_mod, "SessionLocal", return_value=real_db), patch.object(
        job_mod, "_ensure_season", _breaks_the_session
    ):
        job_mod.generate_schedule_job(str(user.id), str(plan_id), str(thread.id), "Plan it")

    assert reader.get(store.TrainingPlan, plan_id).status == store.FAILED


def test_period_report_is_failed_after_a_database_error(real_db):
    from app.jobs import generate_period_report as job_mod
    from app.models.period_report import PeriodReport
    from app.services.coach import period_report_store as store

    user = _user(real_db)
    report = store.create_generating_report(
        real_db, user.id, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        disciplines=["run"], prompt_id="p", schema_version="1",
    )
    report_id = report.id

    async def _breaks_the_session(db, *_args, **_kwargs):
        _fail_a_flush(db)

    reader = _fresh(real_db)
    with patch.object(job_mod, "SessionLocal", return_value=real_db), patch.object(
        job_mod, "generate_period_report", _breaks_the_session
    ):
        job_mod.generate_period_report_job(str(user.id), str(report_id))

    assert reader.get(PeriodReport, report_id).status == store.FAILED
