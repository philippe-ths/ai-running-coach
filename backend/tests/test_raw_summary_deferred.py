"""raw_summary deferral + N+1 guards (#359 / S9).

Activity.raw_summary is deferred=True so bulk ORM scans that never read it don't
drag the 5-15 KB blob along. The risk of deferral is a silent per-row lazy load
(N+1) on the paths that DO read it. These tests:

  1. prove the column is actually deferred (access triggers a follow-up SELECT),
  2. prove each undefer'd read path loads it WITHOUT a per-row lazy load and that
     its query count does NOT scale with the number of activities.

The scaling assertions (count at N=small == count at N=large) are robust to the
exact absolute count: what matters is that nothing grows per activity.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import undefer

from app.models import Activity, DerivedMetric, User
from app.services import activity_queries
from app.services.analysis import analyze
from app.services.analysis.baseline import recompute_runner_baseline
from app.services.analysis.classifier import Classification, compose_headline
from datetime import datetime, timedelta, timezone


class _capture:
    """Capture SQL statements executed on the test session's bind."""

    def __init__(self, db):
        self.bind = db.get_bind()
        self.statements: list[str] = []

    def _on_exec(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        sa.event.listen(self.bind, "after_cursor_execute", self._on_exec)
        return self

    def __exit__(self, *a):
        sa.event.remove(self.bind, "after_cursor_execute", self._on_exec)

    def containing(self, needle: str) -> list[str]:
        return [s for s in self.statements if needle in s]

    def activity_selects(self) -> list[str]:
        return [
            s for s in self.statements
            if s.lstrip().upper().startswith("SELECT") and "FROM activities" in s
        ]


def _seed_user(db) -> uuid.UUID:
    u = User(id=uuid.uuid4(), email=f"rs_{uuid.uuid4()}@example.com")
    db.add(u)
    db.flush()
    return u.id


def _seed_activity(db, user_id, *, days_ago: int, temp: float = 18.0) -> Activity:
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run",
        type="Run",
        start_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=20.0,
        avg_hr=150.0,
        average_speed_mps=3.3,
        raw_summary={"sport_type": "Run", "average_temp": temp},
    )
    db.add(a)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=a.id,
            effort="moderate",
            effort_score=40.0,
            hr_drift=3.0,
            efficiency_analysis={},
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return a


# --- 1. the column is actually deferred -------------------------------------

def test_raw_summary_is_deferred_and_lazy_loads_on_access(db):
    uid = _seed_user(db)
    a = _seed_activity(db, uid, days_ago=1)
    db.expire_all()  # drop the just-flushed instance state

    plain = db.query(Activity).filter(Activity.id == a.id).first()
    with _capture(db) as cap:
        _ = plain.raw_summary  # deferred -> one follow-up SELECT for the column
    assert len(cap.containing("raw_summary")) == 1, cap.statements


def test_undefer_loads_raw_summary_with_the_row(db):
    uid = _seed_user(db)
    a = _seed_activity(db, uid, days_ago=1)
    db.expire_all()

    eager = (
        db.query(Activity)
        .options(undefer(Activity.raw_summary))
        .filter(Activity.id == a.id)
        .first()
    )
    with _capture(db) as cap:
        _ = eager.raw_summary  # already loaded -> no follow-up query
    assert cap.containing("raw_summary") == [], cap.statements


# --- 2. no N+1 on the read paths (count does not scale with activity count) --

def _list_query_count(db, n_activities: int) -> int:
    uid = _seed_user(db)
    for i in range(n_activities):
        _seed_activity(db, uid, days_ago=i + 1)
    db.expire_all()
    with _capture(db) as cap:
        activities = activity_queries.get_activities(db, limit=100, user_id=uid)
        # replicate the endpoint's per-item headline composition (reads raw_summary)
        for act in activities:
            if act.metrics:
                compose_headline(act, Classification.from_metrics(act.metrics))
    return len(cap.activity_selects())


def test_list_path_has_no_per_activity_raw_summary_query(db):
    few = _list_query_count(db, 3)
    many = _list_query_count(db, 8)
    # Equal count regardless of N => no per-row lazy load (raw_summary rode the
    # main query via undefer). Without the fix this would be ~1+N.
    assert few == many, f"list activity-SELECTs scaled with N: {few} vs {many}"


def _baseline_query_count(db, n_activities: int) -> int:
    uid = _seed_user(db)
    for i in range(n_activities):
        _seed_activity(db, uid, days_ago=i + 1)
    db.expire_all()
    with _capture(db) as cap:
        recompute_runner_baseline(db, uid)
    return len(cap.activity_selects())


def test_baseline_recompute_has_no_per_activity_raw_summary_query(db):
    few = _baseline_query_count(db, 3)
    many = _baseline_query_count(db, 8)
    assert few == many, f"baseline activity-SELECTs scaled with N: {few} vs {many}"


def _analyze_query_count(db, n_history: int) -> int:
    uid = _seed_user(db)
    for i in range(n_history):
        _seed_activity(db, uid, days_ago=i + 5)  # history, older than the subject
    subject = _seed_activity(db, uid, days_ago=1)
    db.expire_all()
    with _capture(db) as cap:
        analyze(db, str(subject.id))
    return len(cap.activity_selects())


def test_analyze_has_no_per_history_raw_summary_query(db):
    # The classifier calls _is_run(a) -> raw_summary on every history row; without
    # the history undefer this would scale with history size.
    few = _analyze_query_count(db, 2)
    many = _analyze_query_count(db, 7)
    assert few == many, f"analyze activity-SELECTs scaled with history size: {few} vs {many}"
