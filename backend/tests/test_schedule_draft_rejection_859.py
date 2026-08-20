"""A block settled in conversation is not rejected against a number nobody stated (#859).

The defect was observed in normal use: a runner settled a training block with the
coach in conversation, confirmed the `draft_plan` card, and the deterministic
coherence gate threw the whole plan away. Nothing was stored and the runner read
one generic sentence.

Three seams had to be true for that, and each has its own tests here:

1. The drafting coach was never told the volume ceiling. Every OTHER gate it is
   judged against is stated in its prompt; this one was enforced silently.
2. The CONVERSATIONAL coach — the one that actually settles the block — did not
   have the runner's running norm at all, so it could agree to a week that the
   gate would reject on sight.
3. The rejection reason was discarded, so every failure reached the runner as the
   same sentence, and the one thing they could act on was not in it.

The property that ties them together, and the one this file exists for: the
ceiling the coach is TOLD is the ceiling that REJECTS it. Nothing here restates
the multiple — every number is read from the module that owns it, because a test
that hardcodes 2.0 twice would agree with a prompt that had drifted from the gate.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.core.config import settings
from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.activity_facts import query_facts
from app.services.coach import thread_turn
from app.services.schedule.norms import running_norm_weekly_m
from app.services.schedule.plan_validator import volume_ceilings

TODAY = date.today()


def _user(db) -> User:
    user = User(email=f"norm-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_running_history(db, user: User, *, distance_m: float = 10000) -> None:
    """Twelve weeks of running every other day, so the norm is exact."""
    for offset in range(8, 92, 2):
        day = TODAY - timedelta(days=offset)
        activity = Activity(
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            start_date=datetime(day.year, day.month, day.day, 9, 0),
            type="Run",
            name="Run",
            distance_m=distance_m,
            moving_time_s=3000,
            elapsed_time_s=3000,
            elev_gain_m=0.0,
            raw_summary={},
        )
        db.add(activity)
        db.commit()
        db.add(
            DerivedMetric(
                activity_id=activity.id, effort_score=30.0, confidence="high"
            )
        )
    db.commit()


def _norm_and_ceilings(db, user):
    facts = query_facts(
        db, TODAY - timedelta(days=200), TODAY + timedelta(days=1), user_id=user.id
    )
    norm = running_norm_weekly_m(facts, TODAY)
    return (norm, *volume_ceilings(norm))


# --- 2. the coach that settles the block can see the number ------------------


def test_the_conversation_carries_the_runners_own_typical_running_week(db):
    """The coach that SETTLES a block had the plan but not the volume it is
    measured against, so it could agree to a block the drafting gate then
    rejected outright."""
    user = _user(db)
    _seed_running_history(db, user)
    norm, concrete, sketched = _norm_and_ceilings(db, user)

    sections = thread_turn._build_baseline_sections(db, user)

    assert sections["running_norm"] == {
        "typical_weekly_running_km": round(norm / 1000, 1),
        "max_weekly_running_km": round(concrete / 1000),
        "max_sketched_weekly_running_km": round(sketched / 1000),
    }


def test_the_conversation_states_the_ceiling_as_a_limit_rather_than_a_target(db):
    """The North Star's second question applied to a bound: handed "70 km" beside
    a typical 35, a model reads the larger figure as the one to aim at."""
    user = _user(db)
    _seed_running_history(db, user)
    _norm, concrete, _sketched = _norm_and_ceilings(db, user)

    rendered = thread_turn._render_baseline_block(
        thread_turn._build_baseline_sections(db, user)
    )

    assert "THEIR TYPICAL RUNNING WEEK" in rendered
    assert f"above {round(concrete / 1000)} km of running is rejected" in rendered
    assert "that is a limit, not a target" in rendered


def test_a_runner_with_no_running_history_is_given_no_ceiling(db):
    """The gate abstains without a norm, so there is nothing to say. A bound
    invented here would be a population figure in front of the coach, on exactly
    the runner it would serve worst."""
    user = _user(db)

    sections = thread_turn._build_baseline_sections(db, user)

    assert sections["running_norm"] is None
    assert "TYPICAL RUNNING WEEK" not in thread_turn._render_baseline_block(sections)


def test_the_switch_that_hides_the_schedule_hides_the_norm_with_it(db, monkeypatch):
    """`COACH_SCHEDULE_ENABLED` is the input switch for the runner's plan, and
    this is a schedule-shaped fact: it must go dark with the rest of the schedule
    input rather than surviving the switch on its own."""
    user = _user(db)
    _seed_running_history(db, user)
    monkeypatch.setattr(settings, "COACH_SCHEDULE_ENABLED", False)

    sections = thread_turn._build_baseline_sections(db, user)

    assert "running_norm" not in sections
    assert "TYPICAL RUNNING WEEK" not in thread_turn._render_baseline_block(sections)


def test_a_norm_fault_costs_the_section_not_the_reply(db):
    """A background read failing must never be the reason a runner gets no
    answer — the rule the readiness and schedule reads already follow."""
    user = _user(db)
    _seed_running_history(db, user)

    with patch(
        "app.services.schedule.norms.running_norm_weekly_m",
        side_effect=RuntimeError("boom"),
    ):
        sections = thread_turn._build_baseline_sections(db, user)

    assert sections["running_norm"] is None
    assert "schedule" in sections  # the rest of the baseline survives


# --- the property all three seams rest on ------------------------------------


def test_the_conversation_the_draft_and_the_gate_name_one_ceiling(db):
    """One number, three surfaces.

    The conversation states a ceiling, the drafting context states a ceiling, and
    the gate enforces one. If any two disagreed, a runner could settle a block
    inside what they were told and still lose it — which is the issue.

    Everything here is read from the module that owns it. There is no `2.0` in
    this test, deliberately: a restated multiple would agree with a prompt that
    had already drifted.
    """
    import re

    from app.services.schedule.draft import build_draft_context

    user = _user(db)
    _seed_running_history(db, user)
    _norm, concrete, _sketched = _norm_and_ceilings(db, user)

    conversation = thread_turn._build_baseline_sections(db, user)["running_norm"]
    drafting = build_draft_context(db, user, today=TODAY, weeks=12)
    stated_in_draft = float(
        re.search(r"above ([\d.]+) km of committed running", drafting).group(1)
    )

    assert conversation["max_weekly_running_km"] == round(concrete / 1000)
    assert stated_in_draft == round(concrete / 1000)
