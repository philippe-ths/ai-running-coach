"""#830: `right_now.schedule` — the runner's plan, as the coach receives it.

The named guard this section owes. `right_now.schedule` is a grouped-lineage-only
additive feature, so it sits outside the structural pack guards that build their
pack under `fullest_message_prompt_id` — which is exactly why it needs a test of
its own rather than inheriting coverage it does not have.

Two claims are load-bearing here.

**Byte-stability.** Under every prompt that is not schedule-aware the pack must be
byte-identical to what it was before this signal existed. A section that leaks
into an older prompt changes what a live prod prompt sends.

**The framing.** Handed a plan and a result, a model reaches for a compliance
verdict. This section carries no adherence label, no percentage and no per-session
hit/miss — it states intent and lets the coach compare. That is not a stylistic
preference: it is the nagging the runner-memory redesign (ADR 0025) removed, and
a scorecard-shaped section would put it straight back.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule.coach_view import build_schedule_context

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
THU = MON + timedelta(days=3)
SAT = MON + timedelta(days=5)
SUN = MON + timedelta(days=6)

SCHEDULE_PROMPT = "coach_message_lean_grouped_v9"
PRIOR_PROMPT = "coach_message_lean_grouped_v8"


def _seed_user(db, *, week_starts_on: int | None = None) -> User:
    user = User(email=f"sched-pack-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=5,
            max_hr=190,
            week_starts_on=week_starts_on,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_activity(db, user: User, *, day: date = TUE, activity_type: str = "Run"):
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc),
        type=activity_type,
        name="Session",
        distance_m=9000,
        moving_time_s=2700,
        elapsed_time_s=2700,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def _seed_plan(db, user: User, *, rules=None) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id, status="active", rules=rules or [], week_shapes=[]
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _seed_session(db, plan, **kw) -> PlannedSession:
    payload = {
        "plan_id": plan.id,
        "user_id": plan.user_id,
        "window_start": TUE,
        "window_end": TUE,
        "intent": "quality",
        "discipline": "run",
        "commitment": "committed",
        "title": "6x800m",
        "target_distance_m": 9000,
    }
    payload.update(kw)
    session = PlannedSession(**payload)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# --- the section says what a session was FOR --------------------------------


def test_the_coach_learns_what_this_run_was_meant_to_be(db):
    """The difference between "you ran with some fast bits" and "you hit the 800s"."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        structure={"reps_planned": 6, "rep_distance_m": 800, "rest_s": 90},
        detail="5k effort",
    )
    activity = _seed_activity(db, user)

    section = build_schedule_context(db, activity)

    assert section is not None
    planned = section.planned_for_this_activity
    assert planned is not None
    assert planned.title == "6x800m"
    assert planned.intent == "quality"
    # The session's own distance leads and the prescription survives it (#880),
    # exactly as the runner's card reads. The prescription is what they go out
    # and do; the distance is the number their week is summed from, and a coach
    # that could not see it invented an explanation for it.
    assert planned.target == "9.00 km (6 x 800 m off 90 s)"


def test_the_coach_can_read_the_number_the_runners_week_headline_shows(db):
    """#880: the figure the runner is actually looking at when they ask about it.

    A runner asked why their week read 25 km when 28 had been agreed. The coach
    had never been shown either number — the section carried prescriptions and
    counts and no total — so it invented a mechanism ("counting the sessions at
    their minimum distances"), then diagnosed the app ("a display issue on the
    app's side") about data that was simply stored short.

    Summed through the same `planned_distance_m` the screen uses, so the two
    cannot hold different opinions about one week.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    _seed_session(
        db,
        plan,
        window_start=WED,
        window_end=WED,
        title="6x400m",
        target_distance_m=None,
        structure={
            "reps_planned": 6,
            "rep_distance_m": 400,
            "warmup_distance_m": 1050,
            "cooldown_distance_m": 1050,
        },
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.planned_running_this_week == "9.50 km"


def test_a_bike_session_is_not_counted_as_running(db):
    """The headline the runner reads is running km, so this has to mean the same
    thing. A week whose load is half riding must not report it as mileage."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy run", intent="easy", target_distance_m=5000)
    _seed_session(
        db,
        plan,
        window_start=WED,
        window_end=WED,
        title="Turbo",
        intent="easy",
        discipline="bike",
        target_distance_m=30000,
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.planned_running_this_week == "5.00 km"


def test_a_total_that_omits_a_session_says_so_rather_than_reading_as_the_week(db):
    """A run CAN legally state no distance: "4 reps off 90 s" is a real
    prescription and one is sitting in a live plan right now. It contributes
    nothing, so the total is a real number that is not the whole week.

    Left bare that is the north star's second question failing — whatever is
    ambiguous is read wrong eventually, and a coach reading this as the complete
    week would build the next one against a figure short by a session. The fix is
    to frame the number, not to withhold it.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    _seed_session(
        db, plan, window_start=WED, window_end=WED, title="4 x 5 min",
        target_distance_m=None,
        structure={"reps_planned": 4, "rest_s": 90},
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.planned_running_this_week == (
        "5.00 km, plus 1 run whose distance was not stated"
    )


def test_a_week_that_states_no_distance_reports_none_rather_than_zero(db):
    """"0.00 km" reads as a week off. A week sized only in minutes has no running
    total to state, and abstaining says that where a zero would lie about it."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, title="Easy hour", intent="easy",
        target_distance_m=None, target_duration_s=3600, structure=None,
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.planned_running_this_week is None


def test_the_coach_and_the_runners_screen_report_the_same_week(db):
    """The property, pinned directly rather than inferred from a shared import.

    Two numbers agreeing today because both call one helper is a fact about the
    code as it stands; this is a fact about the two answers. It is the whole
    point of #880 — a coach that reads a different total from the one on the
    runner's screen is back to explaining a figure it cannot see.
    """
    from app.services.schedule.week import build_week

    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    _seed_session(
        db, plan, window_start=WED, window_end=WED, title="6x400m",
        target_distance_m=None,
        structure={
            "reps_planned": 6, "rep_distance_m": 400,
            "warmup_distance_m": 1050, "cooldown_distance_m": 1050,
        },
    )
    _seed_session(
        db, plan, window_start=WED, window_end=WED, title="Turbo",
        intent="easy", discipline="bike", target_distance_m=30000,
    )
    # A run stating no distance at all, so the agreement is checked on the week
    # shape that actually differs between the two readers rather than only on the
    # easy one. The coach's string then carries its partial-total clause, and the
    # FIGURE still has to be the screen's.
    _seed_session(
        db, plan, window_start=THU, window_end=THU, title="4 x 5 min",
        target_distance_m=None, structure={"reps_planned": 4, "rest_s": 90},
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)
    week = build_week(db, user, today=MON)

    on_screen = week.headline.planned_running_distance_m
    assert section.planned_running_this_week.startswith(f"{on_screen / 1000:.2f} km")


def test_the_week_total_is_what_was_asked_for_and_never_what_was_done(db):
    """The discipline this addition had to respect. Handed a plan AND a result a
    model reaches for a compliance verdict, which is the nagging ADR 0025 exists
    to have removed — so what actually happened stays out of this section, where
    it has always been, reachable through the coach's own tools instead."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)
    dumped = section.model_dump()

    assert "planned_running_this_week" in dumped
    assert not [key for key in dumped if "logged" in key or "actual" in key]


def test_the_prescription_is_never_expressed_as_load(db):
    """`effort_score` is a modelled cumulative number that reads as an intensity
    verdict (#168), and nobody was asked to "do 90 load". The coach is given what
    the runner was actually asked for."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, target_distance_m=None, target_duration_s=2400,
        target_effort_score=91.0, structure=None, intent="easy", title="Easy hour",
    )
    activity = _seed_activity(db, user)

    section = build_schedule_context(db, activity)

    assert section.planned_for_this_activity.target == "40 min"
    assert "91" not in (section.planned_for_this_activity.target or "")
    assert "load" not in section.model_dump_json()


def test_the_session_this_run_is_does_not_also_appear_as_still_to_come(db):
    """Otherwise the coach tells the runner to go and do the run it is writing
    about."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan)
    _seed_session(db, plan, window_start=SAT, window_end=SUN, intent="long",
                  title="Long run", target_distance_m=18000, structure=None)
    activity = _seed_activity(db, user)

    section = build_schedule_context(db, activity)

    titles = [s.title for s in section.still_to_come_this_week]
    assert titles == ["Long run"]
    assert section.planned_for_this_activity.title == "6x800m"


def test_what_is_still_to_come_says_when_in_the_runners_own_terms(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, window_start=SAT, window_end=SUN, intent="long",
                  title="Long run", target_distance_m=18000, structure=None)
    _seed_session(db, plan, window_start=MON, window_end=SUN, intent="strength",
                  discipline="strength", title="Gym", target_duration_s=2400,
                  structure=None)
    activity = _seed_activity(db, user, activity_type="Walk")

    section = build_schedule_context(db, activity)
    when = {s.title: s.when for s in section.still_to_come_this_week}

    assert when["Long run"] == "Sat-Sun"
    assert when["Gym"] == "any day"


# --- #943: the one bounded look past this week -------------------------------

NEXT_MON = MON + timedelta(days=7)
NEXT_SAT = SAT + timedelta(days=7)
NEXT_SUN = SUN + timedelta(days=7)


def test_next_weeks_committed_session_carries_the_real_distance(db):
    """#943: a runner's schedule showed an 18 km long run next week; the coach
    reached for it anyway and, with no real figure in front of it, said "16.5km".
    The session now rides the pack with its own stated number."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, window_start=NEXT_SAT, window_end=NEXT_SAT, intent="long",
        title="Long run", target_distance_m=18000, structure=None,
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert [s.title for s in section.next_week_committed] == ["Long run"]
    assert section.next_week_committed[0].target == "18.0 km"


def test_a_next_week_suggestion_earns_no_forward_number(db):
    """A suggestion the runner may still dismiss is not something to hand the
    coach a number for — only a session they have actually committed to."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    # A committed session this week, so the section is not dropped outright for
    # having nothing to say at all — the claim under test is that the SUGGESTED
    # next-week session specifically earns no number.
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    _seed_session(
        db, plan, window_start=NEXT_SAT, window_end=NEXT_SAT, intent="long",
        title="Suggested long run", target_distance_m=20000, structure=None,
        commitment="suggested",
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.next_week_committed == []


def test_next_weeks_sessions_say_next_so_the_weekday_is_unambiguous(db):
    """A next-week session's bare weekday ("Sat") reads identically to one from
    this week, so it has to say which week it means."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, window_start=NEXT_SAT, window_end=NEXT_SAT, intent="long",
        title="Long run", target_distance_m=18000, structure=None,
    )
    _seed_session(
        db, plan, window_start=NEXT_MON, window_end=NEXT_SUN, intent="strength",
        discipline="strength", title="Gym", target_duration_s=2400, structure=None,
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)
    when = {s.title: s.when for s in section.next_week_committed}

    assert when["Long run"] == "next Sat"
    assert when["Gym"] == "next week, any day"


def test_a_session_this_week_still_says_only_its_bare_weekday(db):
    """The disambiguation is additive: this week's own sessions are unaffected."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, window_start=SAT, window_end=SAT, intent="long",
                  title="Long run", target_distance_m=18000, structure=None)
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.still_to_come_this_week[0].when == "Sat"


def test_a_completed_next_week_session_is_not_shown_as_still_ahead(db):
    """`completion.complete_planned_session` has no window restriction — a
    runner who runs next week's long run early and ticks it off must not still
    read to the coach as something ahead of them. This section is INTENT only,
    and a completed session is no longer intent (the #943 follow-up defect)."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    _seed_session(
        db, plan, window_start=NEXT_SAT, window_end=NEXT_SAT, intent="long",
        title="Long run", target_distance_m=18000, structure=None,
        completed_at=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.next_week_committed == []


def test_a_dismissed_next_week_session_is_not_shown_either(db):
    """Same gate, the other terminal state: a session already declined is not
    still "to come"."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, title="Easy", intent="easy", target_distance_m=5000)
    _seed_session(
        db, plan, window_start=NEXT_SAT, window_end=NEXT_SAT, intent="long",
        title="Long run", target_distance_m=18000, structure=None,
        dismissed_at=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert section.next_week_committed == []


def test_next_week_still_works_when_the_runners_week_starts_on_sunday(db):
    """#949 was a Sunday-boundary bug in this exact area this exact week — the
    week-boundary arithmetic for `next_week_committed` gets its own coverage
    under a non-Monday `week_starts_on` rather than trusting Monday to stand in
    for every runner."""
    from app.services.weeks import SUNDAY

    sun_start = MON - timedelta(days=1)  # 2026-08-09, a Sunday
    next_sun_start = sun_start + timedelta(days=7)  # 2026-08-16
    next_week_wed = next_sun_start + timedelta(days=3)  # 2026-08-19

    user = _seed_user(db, week_starts_on=SUNDAY)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, window_start=next_week_wed, window_end=next_week_wed,
        intent="long", title="Long run", target_distance_m=18000, structure=None,
    )
    activity = _seed_activity(db, user, day=MON)

    section = build_schedule_context(db, activity)

    assert [s.title for s in section.next_week_committed] == ["Long run"]
    # And it must not ALSO leak into this week's list under the Sunday boundary.
    assert section.still_to_come_this_week == []


# --- the framing: intent, never a scorecard ---------------------------------


def test_the_section_carries_no_compliance_verdict(db):
    """THE claim of this signal.

    No adherence label, no percentage, no per-session hit/miss. A model handed a
    plan and a result reaches for "you missed two sessions", which is precisely
    the nagging ADR 0025 removed. The section states intent; comparing is the
    coach's job.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan)
    _seed_session(db, plan, window_start=SAT, window_end=SAT, intent="long",
                  title="Long run", target_distance_m=18000, structure=None)
    activity = _seed_activity(db, user)

    section = build_schedule_context(db, activity)
    payload = section.model_dump()

    forbidden = {"adherence", "compliance", "missed", "hit", "completed", "status"}
    assert not (forbidden & set(payload)), payload.keys()
    for upcoming in payload["still_to_come_this_week"]:
        assert not (forbidden & set(upcoming)), upcoming.keys()
    # The one count that exists, and what it is for: not telling a runner who has
    # done everything that they still have work left.
    assert payload["committed_this_week"] == 2
    assert payload["done_this_week"] == 0


def test_a_finished_session_is_counted_but_never_listed_as_outstanding(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, window_start=MON, window_end=MON, intent="easy", title="Monday easy",
        target_distance_m=8000, structure=None,
        completed_at=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc),
    )
    _seed_session(db, plan, window_start=SAT, window_end=SAT, intent="long",
                  title="Long run", target_distance_m=18000, structure=None)
    activity = _seed_activity(db, user, activity_type="Walk")

    section = build_schedule_context(db, activity)

    assert section.done_this_week == 1
    assert [s.title for s in section.still_to_come_this_week] == ["Long run"]


def test_the_rules_ride_along_so_advice_does_not_contradict_the_plan(db):
    """The plan forbids training the day after the long run; the coach should
    not then tell the runner to train tomorrow.

    What rides is the DERIVED statement, not the coach's own `label` (#844).
    The label below is the real one from a live plan, and it permits something
    its predicate forbids. Sending it here would leave the runner reading one
    rule on their schedule screen and the coach reasoning from another — a
    disagreement worse than both being wrong together, and live under
    `coach_message_lean_grouped_v9`.
    """
    misleading = "Full rest or easy cross-training only the day after the long run"
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        rules=[
            {
                "kind": "rest_day_after",
                "label": misleading,
                "intent": "long",
                "source": "coach",
            }
        ],
    )
    _seed_session(db, plan)
    activity = _seed_activity(db, user)

    section = build_schedule_context(db, activity)

    assert section.rules_in_play == ["Nothing but rest the day after a long session."]
    assert misleading not in section.rules_in_play
    assert "cross-training" not in " ".join(section.rules_in_play)


# --- byte-stability ----------------------------------------------------------


def test_a_runner_with_no_plan_gets_no_section_at_all(db):
    """None, not an empty shell: the pack stays byte-identical to what it was
    before this signal existed for every runner without a schedule."""
    user = _seed_user(db)
    activity = _seed_activity(db, user)

    assert build_schedule_context(db, activity) is None


def test_the_section_reaches_only_a_schedule_aware_prompt(db, monkeypatch):
    """A section leaking into an older prompt would change what a live prod
    prompt sends."""
    from app.services.coach import context as ctx
    from app.services.coach.read_time_signals import will_run

    assert will_run(ctx._SCHEDULE_SIGNAL, SCHEDULE_PROMPT) is True
    assert will_run(ctx._SCHEDULE_SIGNAL, PRIOR_PROMPT) is False


def test_the_kill_switch_drops_the_section(db, monkeypatch):
    """`COACH_SCHEDULE_ENABLED` off stops the coach seeing the plan and leaves the
    runner's schedule screen entirely alone (that is `SCHEDULE_ENABLED`)."""
    from app.services.coach import context as ctx
    from app.services.coach.read_time_signals import will_run

    monkeypatch.setattr(settings, "COACH_SCHEDULE_ENABLED", False)

    assert will_run(ctx._SCHEDULE_SIGNAL, SCHEDULE_PROMPT) is False
    assert settings.SCHEDULE_ENABLED is True


def test_a_schedule_fault_never_costs_the_runner_their_report(db, monkeypatch):
    """The schedule is an addition to the report, not a dependency of it."""
    from app.services.coach import context as ctx

    def _boom(*args, **kwargs):
        raise RuntimeError("schedule is down")

    monkeypatch.setattr(
        "app.services.schedule.coach_view.build_schedule_context", _boom
    )
    user = _seed_user(db)
    activity = _seed_activity(db, user)

    assert ctx._build_schedule_context(db, activity, None) is None
