"""#1122 / #1037: one re-analysis pass leaves the flags a second pass would.

Load spike compares a run's load with its seven most recent predecessors'
stored metrics. Two things made a pass disagree with the next one: the pass
walked newest first, so each activity was judged against predecessors still on
the old derivation, and the predecessors' metric rows were fetched in no order
before the first seven were taken.
"""

from datetime import date, timedelta

from app.models import DerivedMetric
from app.services.analysis._orchestrator import _stage_history_metrics
from app.services.analysis.stages import StageContext
from tests.test_reanalyze_history_job import _seed_activity, _seed_user
from tests.test_schedule_completion import _seed_activity as _seed_on_day


def test_history_metrics_follow_the_history_not_the_row_order(db):
    """The metric of the OLDEST predecessor is written first, so a database
    returning rows in insertion order would put it inside the seven."""
    user = _seed_user(db)
    history = [
        _seed_on_day(db, user, day=date(2026, 9, 1) + timedelta(days=n))
        for n in range(8)
    ]
    for activity in history:  # oldest first, as a newest-last insert would leave it
        db.add(DerivedMetric(activity_id=activity.id, effort_score=10.0, confidence="high"))
    db.commit()
    most_recent_first = list(reversed(history))

    ctx = StageContext(
        db=db, activity=None, history=most_recent_first, streams_dict={},
        check_in=None, profile=None, max_hr=190, zone_boundaries=None, state={},
    )
    _stage_history_metrics(ctx)

    assert [m.activity_id for m in ctx.get("history_metrics")] == [
        a.id for a in most_recent_first
    ]


def test_a_pass_walks_the_oldest_activity_first(db):
    from app.jobs.reanalyze_history import reanalyze_history_batch

    user = _seed_user(db)
    for sid in (9903, 9901, 9902):
        _seed_activity(db, user, sid)

    assert reanalyze_history_batch(db, limit=10).processed == [9901, 9902, 9903]
