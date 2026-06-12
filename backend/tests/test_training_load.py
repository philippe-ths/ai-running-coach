"""Weekly training-load aggregation and banding for the Load view (#209)."""

import uuid
from datetime import date, datetime, timezone, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.training_load import LoadFact, build_load_report, week_start


TODAY = date(2026, 6, 12)  # a Friday; week starts Mon 2026-06-08


def _fact(day: date, score: float, name: str = "Run") -> LoadFact:
    return LoadFact(activity_id=uuid.uuid4(), name=name, day=day, effort_score=score)


def _weekly(scores_by_weeks_ago: dict[int, float]) -> list[LoadFact]:
    """One fact per week, `n` weeks before the current week (on the Tuesday)."""
    current = week_start(TODAY)
    return [
        _fact(current - timedelta(weeks=n, days=-1), score)
        for n, score in scores_by_weeks_ago.items()
    ]


def test_week_starts_on_monday():
    assert week_start(date(2026, 6, 12)) == date(2026, 6, 8)
    assert week_start(date(2026, 6, 8)) == date(2026, 6, 8)
    assert week_start(date(2026, 6, 14)) == date(2026, 6, 8)


def test_weekly_score_sums_and_daily_breakdown():
    facts = [
        _fact(date(2026, 6, 8), 50.0),   # Monday
        _fact(date(2026, 6, 8), 10.0),   # second activity same day
        _fact(date(2026, 6, 11), 40.0),  # Thursday
    ]
    report = build_load_report(facts, TODAY)
    week = report.weeks[-1]
    assert week.week_start == date(2026, 6, 8)
    assert week.score == 100.0
    assert week.daily == [60.0, 0.0, 0.0, 40.0, 0.0, 0.0, 0.0]
    assert len(week.activities) == 3


def test_band_from_trailing_four_week_average():
    facts = _weekly({4: 100.0, 3: 100.0, 2: 100.0, 1: 100.0, 0: 100.0})
    week = build_load_report(facts, TODAY).weeks[-1]
    assert week.target_min == 80.0
    assert week.target_max == 130.0
    assert week.status == "optimal"


def test_status_below_and_high():
    base = {4: 100.0, 3: 100.0, 2: 100.0, 1: 100.0}
    low = build_load_report(_weekly({**base, 0: 50.0}), TODAY).weeks[-1]
    assert low.status == "below"
    high = build_load_report(_weekly({**base, 0: 200.0}), TODAY).weeks[-1]
    assert high.status == "high"


def test_no_baseline_with_thin_history():
    # Only two prior weeks: the current week must abstain, not judge.
    facts = _weekly({2: 100.0, 1: 100.0, 0: 100.0})
    week = build_load_report(facts, TODAY).weeks[-1]
    assert week.status == "no_baseline"
    assert week.target_min is None


def test_zero_chronic_average_abstains():
    # Four empty prior weeks then a comeback run: no band to judge against.
    facts = _weekly({5: 80.0, 0: 60.0})
    week = build_load_report(facts, TODAY).weeks[-1]
    assert week.status == "no_baseline"


def test_empty_history_yields_single_empty_current_week():
    report = build_load_report([], TODAY)
    assert len(report.weeks) == 1
    assert report.weeks[0].score == 0.0
    assert report.weeks[0].status == "no_baseline"


def test_endpoint_returns_load_report(client, db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"load_{user_id}@example.com"))
    db.flush()
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Morning Run",
        type="Run",
        start_date=datetime.now(timezone.utc),
        distance_m=10000,
        moving_time_s=3000,
        elapsed_time_s=3100,
        elev_gain_m=50.0,
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(activity_id=a.id, effort_score=72.0, confidence="high"))
    db.commit()

    resp = client.get("/api/trends/load")
    assert resp.status_code == 200, resp.text
    weeks = resp.json()["weeks"]
    assert weeks[-1]["score"] == 72.0
    assert weeks[-1]["activities"][0]["name"] == "Morning Run"
