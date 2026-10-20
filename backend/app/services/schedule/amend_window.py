"""Which weeks an amendment covers, which rows it may replace, and the context
the coach is given for them (#981).

Split out of `amend.py`; import everything from there.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.services.schedule import store
from app.services.schedule.draft import build_draft_context
from app.services.schedule.preferences import describe_preference, plan_preferences
from app.services.schedule.runner_rules import describe_for_coach, with_runner_rules
from app.services.weeks import (
    MONDAY,
    describe_week_span,
    resolve_week_start,
    week_start,
)

def resolve_window(
    today: date, starts_on: int, *, weeks_from: int, weeks_through: int
) -> tuple:
    """Week offsets to real dates, resolved HERE and never supplied by a model.

    The `ScreenPointer` discipline applied to a write: the coach names WHICH
    weeks relative to now, the server works out what those are. A model-supplied
    date is a date that can be wrong by a week without anything noticing, and
    this one decides which of the runner's sessions get overwritten.
    """
    first = week_start(today, starts_on) + timedelta(days=7 * max(0, weeks_from))
    last_start = week_start(today, starts_on) + timedelta(
        days=7 * max(weeks_from, weeks_through)
    )
    return first, last_start + timedelta(days=6)


def _weeks_in(start: date, end: date, starts_on: int) -> List[date]:
    """Every week the window covers, by its start date."""
    weeks: List[date] = []
    cursor = week_start(start, starts_on)
    last = week_start(end, starts_on)
    while cursor <= last:
        weeks.append(cursor)
        cursor = cursor + timedelta(days=7)
    return weeks


def sessions_in_window(
    db: Session, user_id, plan: TrainingPlan, start: date, end: date
) -> List[PlannedSession]:
    return store.sessions_in_range(db, user_id, start, end, plan_id=plan.id)


def _is_replaceable(session: PlannedSession, today: date) -> bool:
    """Whether an amendment may overwrite this row.

    A completed session is a record of what the runner did and survives every
    amendment. A session whose window has already closed is history too: it
    cannot be re-planned, only mis-stated, and `plan_validator` would reject a
    replacement for it as being in the past.
    """
    if session.completed_at is not None:
        return False
    return session.window_end >= today


def _weeks_in(start: date, end: date, starts_on: int) -> List[date]:
    """Every week start in the window, first to last."""
    weeks: List[date] = []
    cursor = week_start(start, starts_on)
    while cursor <= end:
        weeks.append(cursor)
        cursor = cursor + timedelta(days=7)
    return weeks


def _shape_lines(
    plan: TrainingPlan, start: date, end: date, starts_on: int = MONDAY
) -> List[str]:
    """The agreed shape for the weeks being written out, when there is one.

    This is what makes rolling the plan forward a continuation rather than a
    fresh plan: the sketch already carries the long run and the focus the runner
    settled on (#980), so the amendment honours a decision instead of taking it
    again.

    Each week names both its ends (#1001). A rule like "long run on Saturday or
    Sunday" is a claim about weekdays, and resolving it to dates from a start
    alone is arithmetic the model gets wrong at exactly the boundary that
    matters.
    """
    lines: List[str] = []
    for shape in store.plan_week_shapes(plan):
        if not (start <= shape.week_start <= end):
            continue
        if store.is_phase_only(shape):
            # A concrete week's phase marker, not an agreed shape to honour.
            continue
        bits = [
            f"- Week of {shape.week_start.isoformat()} "
            f"({describe_week_span(shape.week_start, starts_on)})"
        ]
        if shape.phase:
            bits.append(f"phase {shape.phase}")
        if shape.target_running_distance_m:
            bits.append(f"{shape.target_running_distance_m / 1000:.0f} km running")
        if shape.target_walking_distance_m:
            bits.append(f"{shape.target_walking_distance_m / 1000:.0f} km walking")
        if shape.target_duration_s:
            bits.append(f"{shape.target_duration_s / 3600:.1f} h in all")
        if shape.long_run_distance_m:
            bits.append(f"long run {shape.long_run_distance_m / 1000:.1f} km")
        if shape.quality_focus:
            bits.append(f"quality: {shape.quality_focus}")
        lines.append(", ".join(bits))
    return lines


def build_amend_context(
    db: Session,
    user: User,
    plan: TrainingPlan,
    *,
    today: date,
    start: date,
    end: date,
    instruction: str,
    facts: Optional[List[Any]] = None,
) -> str:
    """What the coach needs to amend this window, and nothing more.

    Built on top of the drafting context rather than beside it: the runner, their
    recent training, their condition and their ceiling are the same facts either
    way, and two builders would drift into two opinions about the same runner.
    What is added here is the part that only an amendment has: the plan as it
    stands, the window, and what is being asked.
    """
    rows = sessions_in_window(db, user.id, plan, start, end)
    keeping = [r for r in rows if not _is_replaceable(r, today)]
    starts_on = resolve_week_start(getattr(user, "profile", None))

    parts: List[str] = [
        build_draft_context(db, user, today=today, facts=facts),
        "\n## WHAT YOU ARE BEING ASKED TO DO",
        instruction.strip() or "Rewrite this window so the plan still works.",
        "\n## THE WINDOW",
        f"Rewrite the weeks from {start.isoformat()} to {end.isoformat()} "
        f"inclusive. Nothing outside those dates changes.",
        # The weeks written out rather than implied by their first day (#1001).
        # Every rule the plan carries is a claim about weekdays, and a session's
        # window has to sit inside ONE of these; both of those need a day-to-date
        # mapping the model was previously left to work out, and got wrong at the
        # join between two weeks.
        "These are the weeks. Every session's window sits inside ONE of them:",
        *(
            f"- {describe_week_span(ws, starts_on)}"
            for ws in _weeks_in(start, end, starts_on)
        ),
    ]

    rules = with_runner_rules(store.plan_rules(plan), getattr(user, "profile", None))
    if rules:
        parts.append("\n## THE PLAN'S RULES (unchanged, and still enforced)")
        parts.extend(f"- {describe_for_coach(rule)}" for rule in rules)

    # #1082: the plan's preferences hold for the amendment too; it keeps them.
    prefs = plan_preferences(plan)
    if prefs:
        parts.append("\n## THE PLAN'S PREFERENCES (unchanged; they rank, never forbid)")
        parts.extend(f"- {describe_preference(pref)}" for pref in prefs)

    shape = _shape_lines(plan, start, end, starts_on)
    if shape:
        parts.append("\n## THE SHAPE ALREADY AGREED FOR THESE WEEKS")
        parts.extend(shape)

    if keeping:
        parts.append("\n## ALREADY DONE OR PAST, IN THESE WEEKS (leave these alone)")
        for row in keeping:
            state = "done" if row.completed_at is not None else "passed"
            parts.append(
                f"- {row.window_start.isoformat()} {row.title} "
                f"({row.intent}, {row.discipline}, {state})"
            )

    replacing = [r for r in rows if _is_replaceable(r, today)]
    if replacing:
        parts.append("\n## WHAT THE WINDOW HOLDS NOW (this is what you are replacing)")
        for row in replacing:
            parts.append(
                f"- {row.window_start.isoformat()}..{row.window_end.isoformat()} "
                f"{row.title} ({row.intent}, {row.discipline})"
            )
    else:
        parts.append(
            "\n## WHAT THE WINDOW HOLDS NOW\nNothing is written in these weeks yet."
        )

    return "\n".join(parts)
