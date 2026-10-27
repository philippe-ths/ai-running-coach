"""How far back the schedule draft reads the runner's training (#1104).

Most draft tests stub `fetch_draft_facts`, so the real fetch's window went unchecked:
narrowing it from 200 days to 20 left the whole draft subset green, while quietly
changing what the plan-writing model is told about the runner's history. This pins
both edges of the window against real rows.
"""

from datetime import date, datetime, timedelta

from app.models import Activity, User
from app.services.schedule.draft_facts import fetch_draft_facts

TODAY = date(2026, 10, 27)


def _run(db, user, n, when: datetime) -> None:
    db.add(
        Activity(
            user_id=user.id,
            strava_activity_id=11040 + n,
            start_date=when,
            type="Run",
            name="Run",
            distance_m=8000,
            moving_time_s=2400,
            elapsed_time_s=2400,
        )
    )


def _at(days_ago: int, hour: int = 9) -> datetime:
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour, 0)


def test_draft_reads_exactly_the_last_200_days_through_today(db):
    user = User(email="history-window@example.com")
    db.add(user)
    db.commit()
    seeded = {
        "201 days ago": _at(201),
        "200 days ago": _at(200),
        "100 days ago": _at(100),
        "today": _at(0, hour=18),
        "tomorrow": _at(-1),
    }
    for n, when in enumerate(seeded.values()):
        _run(db, user, n, when)
    db.commit()

    read = {f.start_date.replace(tzinfo=None) for f in fetch_draft_facts(db, user, TODAY)}

    assert {label for label, when in seeded.items() if when in read} == {
        "200 days ago",
        "100 days ago",
        "today",
    }
