"""#887: a session card could show a duration while the week counted its distance.

A planned session carrying rep structure AND a duration showed the duration on
its card and never a distance, because `SessionCard.targetLine` returned early
on either stored target and so never reached its own structured total. The
week's headline, meanwhile, summed that session's structured kilometres through
`services/schedule/planned_distance.py`. One row, two readings, and the runner
read the one the total was not made of.

The frontend had a second definition of a session's distance to reach in the
first place — a hand-reimplementation of `structured_distance_m` — which is
exactly what #876 collapsed on the backend. So the fix carries the number:
`PlannedSessionRead.planned_distance_m` is computed from the one definition, the
week's headline is summed from the carried value, and the card reads it rather
than recomputing.

These tests pin the property rather than the instance: whatever a session is
made of, the number on its card is the number it contributed to the headline.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import uuid4

from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule.planned_distance import planned_distance_m
from app.services.schedule.week import build_week

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
THU = MON + timedelta(days=3)
FRI = MON + timedelta(days=4)


def _seed_user(db) -> User:
    user = User(email=f"s887-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id, goal_type="general", experience_level="intermediate",
            weekly_days_available=4, max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_plan(db, user: User) -> TrainingPlan:
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _seed_session(db, plan, *, start, **kwargs):
    session = PlannedSession(
        plan_id=plan.id, user_id=plan.user_id,
        window_start=start, window_end=kwargs.pop("end", None) or start,
        intent=kwargs.pop("intent", "easy"),
        discipline=kwargs.pop("discipline", "run"),
        commitment=kwargs.pop("commitment", "committed"),
        title=kwargs.pop("title", "Session"),
        **kwargs,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# The shape #887 was found on: rep structure AND a duration, no stated total.
_REPS_AND_A_DURATION = dict(
    intent="quality",
    title="4 x 1000m",
    target_distance_m=None,
    target_duration_s=2400,
    structure={
        "reps_planned": 4, "rep_distance_m": 1000, "rest_s": 90,
        "warmup_distance_m": 2000, "cooldown_distance_m": 2000,
    },
)


def test_a_session_with_reps_and_a_duration_carries_the_distance_the_week_counted(db):
    """The defect, directly: the card's number used to be the duration alone."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=WED, **_REPS_AND_A_DURATION)

    week = build_week(db, user, target_week=MON, today=MON)

    (session,) = week.sessions
    # warm-up 2000 + 4 x 1000 + cool-down 2000
    assert session.planned_distance_m == 8000.0
    assert session.target_duration_s == 2400
    assert week.headline.planned_running_distance_m == 8000.0


def test_every_session_carries_the_number_it_contributed_to_the_headline(db):
    """The property, over every shape a session can take.

    Not "both call the same helper" — that is a fact about today's imports. This
    is a fact about the two answers the runner can see: the sum of what the
    cards show is what the headline shows.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=MON, title="Easy 8k", target_distance_m=8000)
    _seed_session(db, plan, start=TUE, **_REPS_AND_A_DURATION)
    _seed_session(
        db, plan, start=WED, intent="quality", title="6x400m",
        structure={
            "reps_planned": 6, "rep_distance_m": 400,
            "warmup_distance_m": 1050, "cooldown_distance_m": 1050,
        },
    )
    _seed_session(db, plan, start=THU, title="Timed easy", target_duration_s=1800)
    _seed_session(
        db, plan, start=FRI, intent="strength", discipline="strength",
        title="Lower body", target_duration_s=2700,
    )

    week = build_week(db, user, target_week=MON, today=MON)

    assert len(week.sessions) == 5
    from_the_cards = sum(
        s.planned_distance_m for s in week.sessions if s.discipline == "run"
    )
    assert from_the_cards == week.headline.planned_running_distance_m
    assert from_the_cards == 8000.0 + 8000.0 + 4500.0 + 0.0


def test_a_stated_total_still_wins_over_the_structure_it_contains(db):
    """The precedence #876 set, unchanged by carrying the value."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=WED, intent="quality", title="10k with reps",
        target_distance_m=10000,
        structure={"reps_planned": 6, "rep_distance_m": 800, "rest_s": 90},
    )

    week = build_week(db, user, target_week=MON, today=MON)

    (session,) = week.sessions
    assert session.planned_distance_m == 10000.0
    assert week.headline.planned_running_distance_m == 10000.0


def test_a_duration_is_never_converted_into_a_distance(db):
    """`planned_distance.py` abstains rather than assume a pace, and so does the card.

    0.0 is the honest answer, and the card falls through to showing the duration
    alone. Turning 30 minutes into "about 6 km" would be the app inventing a
    fact, which is what `effort.py` refuses to do for load and this refuses to
    do for distance.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=WED, title="Timed easy", target_duration_s=1800)

    week = build_week(db, user, target_week=MON, today=MON)

    (session,) = week.sessions
    assert session.planned_distance_m == 0.0
    assert week.headline.planned_running_distance_m == 0.0


def test_the_carried_value_is_the_one_definition_and_not_a_copy_of_it(db):
    """Sensitivity: the field is computed, so it cannot be set to something else.

    If `planned_distance_m` were assigned at construction, a future builder
    could omit it or compute it differently and every test above would still
    pass on the builder that happens to be exercised. It is a computed property,
    so it agrees with the module by construction — checked here directly against
    the module rather than trusted.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=WED, **_REPS_AND_A_DURATION)

    week = build_week(db, user, target_week=MON, today=MON)
    (session,) = week.sessions

    assert session.planned_distance_m == planned_distance_m(session)
    assert "planned_distance_m" in type(session).model_computed_fields
    assert "planned_distance_m" not in type(session).model_fields


# --- the second definition cannot come back ---------------------------------

import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_FRONTEND_SOURCE_DIRS = ("app", "components", "lib", "scripts")

# Each signal is a member-access or identifier form, so the schema DECLARATION
# of a field (`target_distance_m: number | null;`) and the smoke fixture's
# object literals are not matched — only code that reaches for the value.
_ARITHMETIC_SIGNALS = (
    (
        re.compile(r"\bstructuredDistance\b"),
        "declares structuredDistance, the removed copy of structured_distance_m",
    ),
    (
        re.compile(r"[.\[\"'](?:warmup|cooldown)_distance_m"),
        "reads the warm-up/cool-down edges, which exist only to be summed into a total",
    ),
    (
        re.compile(r"\.target_distance_m\b"),
        "reads target_distance_m, whose precedence over the structure it contains "
        "is decided by planned_distance.py",
    ),
)


def planned_distance_arithmetic_in(source: str) -> list[str]:
    """Signs that a frontend file has started computing a session's distance again.

    The three signals are the ingredients, not the answer: a file that reads
    `planned_distance_m` off the session it was served is doing the right thing
    and matches nothing here, while a file reaching for the warm-up edge or the
    stated total is reassembling the sum that #876 collapsed into one place.

    Rendering a warm-up as prose would legitimately trip this. That is intended:
    the moment the client touches the parts again, whether the card and the
    headline still agree becomes a live question, and this failing is the prompt
    to answer it rather than a false alarm to route around.
    """
    return [label for pattern, label in _ARITHMETIC_SIGNALS if pattern.search(source)]


def test_no_frontend_source_file_computes_a_planned_sessions_distance():
    """The single definition, enforced where the defect actually lived.

    #887 was a frontend defect and this repository has no frontend unit or
    component test runner, so the tests above can only pin that the value is
    carried correctly — they cannot see what the card does with it. What is
    checkable from here is that there is no longer any other number for the card
    to show, which is the property that makes the class of defect
    unrepresentable rather than this instance of it fixed.
    """
    problems = []
    for directory in _FRONTEND_SOURCE_DIRS:
        root = _FRONTEND / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
                continue
            for signal in planned_distance_arithmetic_in(path.read_text()):
                problems.append(f"{path.relative_to(_FRONTEND)} {signal}")

    assert not problems, (
        "a planned session's distance is being computed in the frontend again. It "
        "is decided by backend/app/services/schedule/planned_distance.py and "
        "reaches the card as PlannedSessionRead.planned_distance_m:\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_the_arithmetic_guard_notices_the_removed_copy():
    """SENSITIVITY. This is the deleted code, verbatim, plus the early return
    that made the card and the headline disagree."""
    source = (
        "function structuredDistance(session: PlannedSession): number {\n"
        "  const s = session.structure;\n"
        "  if (!s) return 0;\n"
        "  return positive(s.warmup_distance_m) + reps * repDistance"
        " + positive(s.cooldown_distance_m);\n"
        "}\n"
        "if (session.target_distance_m) parts.push(formatDistanceKm(session.target_distance_m));\n"
    )

    problems = planned_distance_arithmetic_in(source)

    assert any("structuredDistance" in p for p in problems)
    assert any("warm-up" in p for p in problems)
    assert any("target_distance_m" in p for p in problems)


def test_the_arithmetic_guard_does_not_fire_on_the_card_as_it_stands():
    """The other side of the line. Showing the carried distance, and rendering
    the rep prescription beside it, are both correct and must stay silent — a
    guard that fires on the fixed code is one people delete."""
    source = (
        "if (session.planned_distance_m > 0) "
        "parts.push(formatDistanceKm(session.planned_distance_m));\n"
        "const reps = session.structure?.reps_planned;\n"
        "const distance = session.structure?.rep_distance_m;\n"
        "return `${reps} × ${Math.round(distance)} m`;\n"
    )

    assert planned_distance_arithmetic_in(source) == []
