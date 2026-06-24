"""Weekly training-load aggregation and banding for the Load view (#209)."""

import uuid
from datetime import date, datetime, timezone, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.training_load import (
    MAX_WEEKS,
    LoadFact,
    build_load_report,
    get_load_report,
    week_start,
)


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


def test_endpoint_composes_headline_through_projection(client, db):
    """The headline depends on raw_summary (sport_type/trainer) plus the
    classification axes; the column projection (#362) must still produce the
    same headline the full ORM load did. A hilly long run exercises both the
    metric axes and the run-noun composition."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"hl_{user_id}@example.com"))
    db.flush()
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash("hl" + str(uuid.uuid4()))) % 10**9,
        name="Big Sunday Long Run",
        type="Run",
        start_date=datetime.now(timezone.utc),
        distance_m=25000,
        moving_time_s=9000,
        elapsed_time_s=9200,
        elev_gain_m=400.0,
        raw_summary={"sport_type": "Run"},
    )
    db.add(a)
    db.flush()
    db.add(
        DerivedMetric(
            activity_id=a.id,
            effort_score=180.0,
            confidence="high",
            effort="moderate",
            duration_class="long",
            structure="continuous",
            is_hilly=True,
            is_race=False,
        )
    )
    db.commit()

    resp = client.get("/api/trends/load")
    assert resp.status_code == 200, resp.text
    point = resp.json()["weeks"][-1]["activities"][0]
    assert point["name"] == "Big Sunday Long Run"
    assert point["headline"] == "Hilly long run (moderate)"


def test_endpoint_excludes_old_activity_but_preserves_series_length(client, db):
    """An activity older than MAX_WEEKS is dropped from the served weeks, yet
    its existence still anchors the series to the full MAX_WEEKS window — the
    series-start probe (#362) reproduces the prior unbounded build's length."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"old_{user_id}@example.com"))
    db.flush()
    now = datetime.now(timezone.utc)

    def _run(name, when):
        return Activity(
            id=uuid.uuid4(),
            user_id=user_id,
            strava_activity_id=abs(hash(name + str(uuid.uuid4()))) % 10**9,
            name=name,
            type="Run",
            start_date=when,
            distance_m=8000,
            moving_time_s=2400,
            elapsed_time_s=2500,
            elev_gain_m=20.0,
        )

    recent = _run("Recent Run", now)
    old = _run("Ancient Run", now - timedelta(weeks=60))
    db.add_all([recent, old])
    db.flush()
    db.add_all(
        [
            DerivedMetric(activity_id=recent.id, effort_score=60.0, confidence="high"),
            DerivedMetric(activity_id=old.id, effort_score=99.0, confidence="high"),
        ]
    )
    db.commit()

    weeks = client.get("/api/trends/load").json()["weeks"]
    assert len(weeks) == MAX_WEEKS  # old history anchors the full window
    names = [p["name"] for w in weeks for p in w["activities"]]
    assert "Recent Run" in names
    assert "Ancient Run" not in names  # older than the window -> not served


def test_same_day_activities_are_ordered_chronologically(db):
    """build_load_report sorts a week's members by date only, so the query's
    order breaks same-day ties. An explicit chronological ORDER BY makes that
    deterministic — without it the order was whatever physical scan order the
    DB returned (which the #362 join silently perturbed)."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"ord_{user_id}@example.com"))
    db.flush()
    day = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)

    def _run(name, hour):
        a = Activity(
            id=uuid.uuid4(),
            user_id=user_id,
            strava_activity_id=abs(hash(name + str(uuid.uuid4()))) % 10**9,
            name=name,
            type="Run",
            start_date=day.replace(hour=hour),
            distance_m=5000,
            moving_time_s=1500,
            elapsed_time_s=1600,
            elev_gain_m=10.0,
        )
        db.add(a)
        db.flush()
        db.add(DerivedMetric(activity_id=a.id, effort_score=10.0, confidence="high"))

    # Same calendar day, inserted out of chronological order.
    _run("OrdTest-Late", 18)
    _run("OrdTest-Early", 6)
    _run("OrdTest-Mid", 12)
    db.commit()

    resp = get_load_report(db, user_id=user_id, today=day.date())
    names = [a.name for w in resp.weeks for a in w.activities if a.name.startswith("OrdTest")]
    assert names == ["OrdTest-Early", "OrdTest-Mid", "OrdTest-Late"]
