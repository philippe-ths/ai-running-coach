"""The chat coach is told the weekly TIME limit a drafted plan is checked against (#1047).

#1044 added an hours ceiling to the gate and stated it in the drafting context,
but the conversation only carried the running-km limit, so a block agreed in a
thread could be rejected against a number the coach never saw: the #859 failure,
for hours. Every number here is read from the module that owns it.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import re
import uuid
from datetime import datetime, timedelta

from app.models import Activity, DerivedMetric
from app.services.activity_facts import query_facts
from app.services.coach import thread_turn
from app.services.schedule.draft import build_draft_context
from app.services.schedule.norms import weekly_hours_norm_s
from app.services.schedule.plan_validator import hours_ceilings, stated_hours
from tests.test_schedule_draft_rejection_859 import TODAY, _seed_running_history, _user


def _seed_walks_only(db, user):
    for offset in range(8, 92, 2):
        day = TODAY - timedelta(days=offset)
        activity = Activity(
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            start_date=datetime(day.year, day.month, day.day, 9, 0),
            type="Walk",
            name="Walk",
            distance_m=5000,
            moving_time_s=3600,
            elapsed_time_s=3600,
            elev_gain_m=0.0,
            raw_summary={},
        )
        db.add(activity)
        db.commit()
        db.add(DerivedMetric(activity_id=activity.id, effort_score=10.0, confidence="high"))
    db.commit()


def _hours_norm_and_ceilings(db, user):
    facts = query_facts(
        db, TODAY - timedelta(days=200), TODAY + timedelta(days=1), user_id=user.id
    )
    norm_s = weekly_hours_norm_s(facts, TODAY)
    return (norm_s, *hours_ceilings(norm_s))


def test_the_conversation_carries_the_weekly_hours_limit(db):
    user = _user(db)
    _seed_running_history(db, user)
    norm_s, concrete, sketched = _hours_norm_and_ceilings(db, user)

    section = thread_turn._build_baseline_sections(db, user)["running_norm"]

    assert section["typical_weekly_hours"] == round(norm_s / 3600, 1)
    assert section["max_weekly_hours"] == stated_hours(concrete)
    assert section["max_sketched_weekly_hours"] == stated_hours(sketched)


def test_the_stated_limit_is_rounded_down_so_a_week_under_it_passes(db):
    assert stated_hours(5.99 * 3600) == 5.9
    assert stated_hours(6.0 * 3600) == 6.0


def test_the_conversation_and_the_draft_name_one_hours_limit(db):
    user = _user(db)
    _seed_running_history(db, user)

    conversation = thread_turn._build_baseline_sections(db, user)["running_norm"]
    drafting = build_draft_context(db, user, today=TODAY)
    stated_in_draft = float(
        re.search(r"A week above ([\d.]+) h of committed time", drafting).group(1)
    )

    assert conversation["max_weekly_hours"] == stated_in_draft


def test_the_hours_limit_is_said_as_every_activity_and_as_a_limit(db):
    user = _user(db)
    _seed_running_history(db, user)
    section = thread_turn._build_baseline_sections(db, user)["running_norm"]

    rendered = thread_turn._render_baseline_block({"running_norm": section})

    assert "every activity together" in rendered
    assert "not running time" in rendered
    assert f"more than {section['max_weekly_hours']} h of committed time" in rendered
    assert "a limit, not a target" in rendered


def test_a_runner_who_only_walks_still_hears_the_hours_limit(db):
    """No running norm means no km limit, but the hours gate still bites."""
    user = _user(db)
    _seed_walks_only(db, user)

    section = thread_turn._build_baseline_sections(db, user)["running_norm"]
    rendered = thread_turn._render_baseline_block({"running_norm": section})

    assert "max_weekly_running_km" not in section
    assert section["max_weekly_hours"] > 0
    assert "TYPICAL RUNNING WEEK" not in rendered
    assert "TYPICAL TRAINING TIME" in rendered


def test_the_period_report_keeps_its_running_only_norm(db):
    """The period report states the norm under a RUNNING heading, so the hours
    limit stays out of it rather than being read as running time."""
    from app.services.coach.period_report_pack import build_period_report_pack

    runner = _user(db)
    _seed_running_history(db, runner)
    walker = _user(db)
    _seed_walks_only(db, walker)

    def pack(user):
        return build_period_report_pack(
            db, user, period_start=TODAY - timedelta(days=28), period_end=TODAY,
            disciplines=[],
        )

    assert set(pack(runner).running_norm) == {
        "typical_weekly_running_km", "max_weekly_running_km", "max_sketched_weekly_running_km",
    }
    assert pack(walker).running_norm is None
