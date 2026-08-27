"""Amending a plan in part, and keeping the rest (#981).

The plan had one verb: REPLACE. `draft_plan` writes a new block and supersedes
the old one, and everything else in the package edits a single field of a single
session. Between those two there was nothing, so every real request a runner
makes of a living schedule ("I'm sore, drop something this week", "we've reached
the end of what's written, build the next block", "add a fourth run") could only
be served by throwing the whole plan away and generating another one. That is how
a runner came to confirm a second draft ninety seconds after agreeing the first
and lose the block they had just settled: replacement was the only move on the
board.

This is the missing verb. An amendment rewrites the sessions inside a BOUNDED
WINDOW and leaves everything else exactly as it is: the same plan row, the same
rules, the same race, the same completions, the same agreed sessions outside the
window. It is what makes the schedule something the runner lives alongside rather
than a document that is periodically discarded.

What it may not do
------------------
Not the rules, and not the race. Those are the plan's identity; a coach that
rewrites them has redrafted, and the runner is owed the bigger card that says so.
Not a completed session, ever: what the runner did is a record, not a plan.
Not a session outside the window, which is the whole promise the card makes.

All-or-nothing, like the draft. A half-applied amendment is a week nobody wrote,
and the runner would train it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.services.coach import turn
from app.services.schedule import store
from app.services.schedule.draft import build_draft_context, fetch_draft_facts
from app.services.schedule.draft_contract import SESSION_PROPERTIES, DraftedWeek
from app.services.schedule.effort import build_load_model, estimate_effort
from app.services.schedule.norms import running_norm_weekly_m
from app.services.schedule.plan_validator import VOLUME_CEILING, validate_amendment
from app.services.schedule.rule_text import describe_rule
from app.services.weeks import resolve_week_start, week_start

logger = logging.getLogger(__name__)

# How far an amendment may reach. Wide enough for the two real cases (the rest of
# this week; the next block, written out of its sketch) and narrow enough that
# "amend" cannot quietly become "redraft" and take the plan with it.
MAX_AMEND_WEEKS = 6


class AmendedPlan(BaseModel):
    """The weeks the amendment rewrites. Sessions only, deliberately.

    No rules and no sketch weeks: the plan already has both and an amendment
    does not get to change them. Reusing `DraftedWeek` rather than declaring a
    parallel shape means an amended session is held to exactly the contract a
    drafted one is, down to the rep structure and the warm-up in metres.
    """

    model_config = ConfigDict(extra="forbid")

    weeks: List[DraftedWeek] = Field(default_factory=list, max_length=MAX_AMEND_WEEKS)
    summary: Optional[str] = Field(default=None, max_length=600)


RECORD_AMENDMENT_TOOL: Dict[str, Any] = {
    "name": "record_plan_amendment",
    "description": (
        "Record the amended weeks. This is the only way to return your answer. "
        "You are rewriting the sessions inside the named window ONLY: everything "
        "else in this runner's plan stays exactly as it is, including its rules, "
        "its race, the sessions outside the window, and anything they have "
        "already done. Give every session the window needs, not only the ones "
        "you are changing, because what you record REPLACES the window."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["weeks"],
        "properties": {
            "weeks": {
                "type": "array",
                "description": (
                    "One entry per week in the window, each holding every session "
                    "that week should now contain."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["week_start", "sessions"],
                    "properties": {
                        "week_start": {"type": "string"},
                        "phase": {"type": "string"},
                        "sessions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "window_start",
                                    "window_end",
                                    "intent",
                                    "discipline",
                                    "title",
                                ],
                                "properties": SESSION_PROPERTIES,
                            },
                        },
                    },
                },
            },
            "summary": {
                "type": "string",
                "description": (
                    "One or two sentences on what you changed and why, in the "
                    "runner's terms."
                ),
            },
        },
    },
}


_SYSTEM_PROMPT = """You are a running coach amending this runner's existing training plan.

You are NOT writing a new plan. They have one, they agreed to it, and they are \
living inside it. Your job is to rewrite the sessions inside one named window so \
the plan still works, and to leave everything else alone.

# WHAT YOU ARE CHANGING

The window is stated below, as whole weeks. Record every session those weeks \
should now hold, because what you record replaces them. A session in the window \
you did not mean to change is a session you must write again as it was.

Sessions the runner has already completed are NOT yours to touch and are listed \
separately. They stay. Write around them: they still occupy their day, and the \
plan's spacing rules still count them.

# WHAT YOU ARE NOT CHANGING

The plan's rules, its goal race, its phases beyond the window, and every session \
outside it. If what the runner needs cannot be done without changing those, say \
so rather than doing it quietly: a plan rewritten under the name of a small \
change is the one thing this must never be.

# HOW TO AMEND

Change as little as the reason requires. A runner reporting a sore leg needs \
this week softened, not their block redesigned. A plan reaching the end of its \
written weeks needs the next weeks written out of the shape that was already \
agreed, in the direction it was already going, not a fresh idea about their \
training.

Keep the block's backbone. If the shape below names a long run distance and a \
focus for a week you are writing out, honour them: the runner agreed to that \
progression and it is usually the reason they have a plan at all.

Everything the plan's own rules require still binds. A week that cannot satisfy \
them is rejected and you will be asked again.

If the change you were asked to make CANNOT be made without touching something \
the runner did not agree to lose, make no change at all and say so. This is the \
one instruction here that overrides the others. The runner confirmed a specific \
change, and a different change is not a smaller version of it: it is one they \
never agreed to. Their rules can make a request genuinely impossible - adding a \
second quality session to a week whose rules space quality three days apart, for \
instance - and the honest answer then is that it does not fit, not a rearranged \
week that removes the session they told you to keep.

Answer only by calling record_plan_amendment."""


@dataclass
class AmendOutcome:
    ok: bool
    failures: Optional[List[str]] = None
    failure_kind: str = store.FAILURE_UNKNOWN
    summary: Optional[str] = None
    weeks_touched: int = 0
    sessions_written: int = 0
    # What the amendment ACTUALLY did, derived from the rows it removed and the
    # rows it wrote (#986 follow-up). The card is a forecast: it is minted before
    # the generation runs, so it can only say what was ASKED for. When the two
    # differ the runner has to be able to see it, and a live amendment differed
    # silently: the card promised an easy run would become hill reps and the
    # rewrite removed the week's interval session instead.
    changes: List[str] = field(default_factory=list)


@dataclass
class AmendProposal:
    """A finished amendment that has not been written, and may never be (#987).

    The decision moved in front of the runner's tap. `propose_amendment` runs the
    generation and the whole coherence gate, then stops: the week it settled on
    lives here until the runner has seen it and agreed to it, and `apply_proposal`
    writes exactly this and nothing it recomputes.

    That ordering is the fix for a defect the previous shape could not avoid. When
    the writer ran AFTER the tap, the runner agreed to a forecast and received
    whatever came back, and a rewrite that could not satisfy the plan's rules
    produced a different legal week instead of refusing: a card promising one easy
    run would become hill reps deleted the week's interval session.

    `changes` is that difference, computed from the rows that would go and the
    sessions that would arrive, in the same terms `_describe_change` uses after a
    write. The runner reads it on the card, so a substitution is visible BEFORE it
    can happen rather than recorded after. That is a structural guarantee and it
    does not depend on the prompt refusing correctly.
    """

    ok: bool
    amended: Optional[AmendedPlan] = None
    changes: List[str] = field(default_factory=list)
    start: Optional[date] = None
    end: Optional[date] = None
    failures: Optional[List[str]] = None
    failure_kind: str = store.FAILURE_UNKNOWN


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


def _shape_lines(plan: TrainingPlan, start: date, end: date) -> List[str]:
    """The agreed shape for the weeks being written out, when there is one.

    This is what makes rolling the plan forward a continuation rather than a
    fresh plan: the sketch already carries the long run and the focus the runner
    settled on (#980), so the amendment honours a decision instead of taking it
    again.
    """
    lines: List[str] = []
    for shape in store.plan_week_shapes(plan):
        if not (start <= shape.week_start <= end):
            continue
        bits = [f"- Week of {shape.week_start.isoformat()}"]
        if shape.phase:
            bits.append(f"phase {shape.phase}")
        if shape.target_running_distance_m:
            bits.append(f"{shape.target_running_distance_m / 1000:.0f} km running")
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

    parts: List[str] = [
        build_draft_context(
            db, user, today=today, weeks=1, facts=facts, state_horizon=False
        ),
        "\n## WHAT YOU ARE BEING ASKED TO DO",
        instruction.strip() or "Rewrite this window so the plan still works.",
        "\n## THE WINDOW",
        f"Rewrite the weeks from {start.isoformat()} to {end.isoformat()} "
        f"inclusive. Nothing outside those dates changes.",
    ]

    rules = store.plan_rules(plan)
    if rules:
        parts.append("\n## THE PLAN'S RULES (unchanged, and still enforced)")
        parts.extend(f"- {describe_rule(rule)}" for rule in rules)

    shape = _shape_lines(plan, start, end)
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


async def propose_amendment(
    db: Session,
    user: User,
    plan: TrainingPlan,
    *,
    weeks_from: int,
    weeks_through: int,
    instruction: str,
    today: Optional[date] = None,
) -> AmendProposal:
    """Settle what the window WOULD hold, and write none of it (#987).

    Everything the amendment decides happens here: the same envelope, coercion
    and coherence gate the draft uses, because an amendment reaching the schedule
    through a conversation is not held to a lower bar than a plan is.

    It stops at the point of writing. A proposal that cannot satisfy the plan's
    own rules comes back refused, carrying the rule it could not satisfy, and the
    coach answers the runner with that rather than putting up a card for a change
    it cannot honour.
    """
    today = today or date.today()
    starts_on = resolve_week_start(getattr(user, "profile", None))
    start, end = resolve_window(
        today, starts_on, weeks_from=weeks_from, weeks_through=weeks_through
    )

    if turn.over_budget(user.id):
        return AmendProposal(
            ok=False,
            start=start,
            end=end,
            failures=["over the spend cap for this period"],
            failure_kind=store.FAILURE_OVER_BUDGET,
        )

    facts = fetch_draft_facts(db, user, today)
    context = build_amend_context(
        db, user, plan, today=today, start=start, end=end,
        instruction=instruction, facts=facts,
    )
    client = turn.build_client(turn.TurnKind.SCHEDULE, user.id)
    norm_running = running_norm_weekly_m(facts, today)
    rules = store.plan_rules(plan)
    races = store.list_goal_races(db, user.id, on_or_after=today)
    target_race = next(
        (r for r in races if r.priority == "A"), races[0] if races else None
    )
    race_arg = (target_race.race_date, target_race.distance_m) if target_race else None

    failures: List[str] = []
    failure_kind = store.FAILURE_UNKNOWN
    rewrites_left = 2
    transport_retries_left = 1

    while rewrites_left > 0:
        user_message = context
        if failures:
            # The retry has to carry the refusal option forward with it (#987).
            # Told only to fix the failures, a model does: it finds the nearest
            # legal week, and when the request genuinely does not fit, the
            # nearest legal week is one that drops a session the runner asked to
            # keep. That is how an amendment promising to replace an easy run
            # deleted a week's interval session instead. Refusing outranks the
            # instructions above it, and it has to outrank this one too, or the
            # last thing said wins.
            user_message = (
                f"{context}\n\n## YOUR PREVIOUS ATTEMPT WAS REJECTED\n"
                + "\n".join(f"- {failure}" for failure in failures)
                + "\n\nWrite the amendment again, fixing every one of these. If "
                "fixing them is only possible by changing something the runner "
                "did not ask you to change, do not write an amendment at all: "
                "refuse, exactly as the instructions above require. A legal week "
                "that makes a change they never agreed to is a worse answer than "
                "no change."
            )
        try:
            raw = await client.generate_structured(
                system=_SYSTEM_PROMPT,
                user=user_message,
                tool=RECORD_AMENDMENT_TOOL,
                max_tokens=8192,
            )
        except Exception as exc:  # transport, timeout, refusal
            logger.warning("schedule amend: generation call failed: %s", exc)
            if transport_retries_left > 0:
                transport_retries_left -= 1
                continue
            return AmendProposal(
                ok=False,
                start=start,
                end=end,
                failures=["the coach could not be reached"],
                failure_kind=store.FAILURE_UNREACHABLE,
            )

        rewrites_left -= 1

        try:
            amended = AmendedPlan.model_validate(_normalise(raw))
        except Exception as exc:
            logger.warning("schedule amend: off-contract amendment: %s", exc)
            failures = [f"the amendment was not the shape the tool requires: {exc}"]
            continue

        outside = [
            w.week_start
            for w in amended.weeks
            if not (start <= w.week_start <= end)
        ]
        if outside:
            # The one refusal that is about the promise rather than the plan: a
            # week outside the window is a change the runner did not agree to,
            # whatever else is right about it.
            failures = [
                f"week {w} is outside the window {start}..{end}" for w in outside
            ]
            continue

        rows = sessions_in_window(db, user.id, plan, start, end)
        surviving: Dict[date, List[PlannedSession]] = {}
        for row in rows:
            if _is_replaceable(row, today):
                continue
            key = week_start(row.window_start, starts_on)
            surviving.setdefault(key, []).append(row)

        check = validate_amendment(
            amended.weeks,
            rules=rules,
            surviving_by_week=surviving,
            today=today,
            starts_on=starts_on,
            norm_weekly_running_m=norm_running,
            expected_weeks=_weeks_in(start, end, starts_on),
            race=race_arg,
        )
        if not check.ok:
            logger.info("schedule amend: rejected: %s", check.failures)
            failures = check.failures
            failure_kind = (
                store.FAILURE_TOO_BIG_A_JUMP
                if VOLUME_CEILING in check.codes
                else store.FAILURE_UNKNOWN
            )
            continue

        return AmendProposal(
            ok=True,
            amended=amended,
            changes=_forecast_change(rows, amended, today),
            start=start,
            end=end,
        )

    return AmendProposal(
        ok=False, start=start, end=end, failures=failures, failure_kind=failure_kind
    )


def _forecast_change(
    rows: List[PlannedSession], amended: AmendedPlan, today: date
) -> List[str]:
    """What this proposal WOULD change, in the terms the runner reads after it
    has been applied.

    Derived from the same two sides `_describe_change` compares after a write —
    the replaceable rows that would go, the sessions that would arrive — so the
    line on the card and the line in the ledger are the same sentence about the
    same change. That is the property that makes the card checkable: a rewrite
    that quietly drops a session the runner meant to keep says so here, before
    they agree to it, whatever the prompt did or did not do.
    """
    removed = [_describe_row(row) for row in rows if _is_replaceable(row, today)]
    added = [
        f"{session.window_start.strftime('%a')} {session.title}"
        for week in amended.weeks
        for session in week.sessions
    ]
    return _describe_change(removed, added)


async def amend_plan(
    db: Session,
    user: User,
    plan: TrainingPlan,
    *,
    weeks_from: int,
    weeks_through: int,
    instruction: str,
    today: Optional[date] = None,
) -> AmendOutcome:
    """Propose an amendment and write it in one go, deciding nothing in between.

    The path a runner takes no longer comes through here: the offer proposes and
    the confirm applies, so the runner sees the week before agreeing to it. This
    remains for callers that have no runner in the loop to show it to.
    """
    proposal = await propose_amendment(
        db, user, plan,
        weeks_from=weeks_from, weeks_through=weeks_through,
        instruction=instruction, today=today,
    )
    if not proposal.ok:
        return AmendOutcome(
            ok=False,
            failures=proposal.failures,
            failure_kind=proposal.failure_kind,
        )
    return apply_proposal(db, user, plan, proposal, today=today)


def apply_proposal(
    db: Session,
    user: User,
    plan: TrainingPlan,
    proposal: AmendProposal,
    *,
    today: Optional[date] = None,
) -> AmendOutcome:
    """Write the week the runner already saw and agreed to.

    Deterministic and quick: no generation, no coherence gate, nothing left to
    decide. That is the point of the split. Every judgement was made while the
    runner could still say no, so the tap does one thing, and the only difference
    between the card and the result is a row that stopped being replaceable in
    between — which `changes` reports.
    """
    if not proposal.ok or proposal.amended is None:
        return AmendOutcome(
            ok=False,
            failures=proposal.failures or ["nothing was proposed"],
            failure_kind=proposal.failure_kind,
        )
    today = today or date.today()
    facts = fetch_draft_facts(db, user, today)
    written, changes = _apply(
        db, user, plan, proposal.amended, build_load_model(facts, today),
        start=proposal.start, end=proposal.end, today=today,
    )
    return AmendOutcome(
        ok=True,
        summary=proposal.amended.summary,
        weeks_touched=len(proposal.amended.weeks),
        sessions_written=written,
        changes=changes,
    )


def _normalise(raw: Any) -> Any:
    """The drafted-plan normaliser's amendment-shaped sibling: tolerate a tool
    result handed back as a JSON string, and nothing else."""
    import json

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _apply(
    db: Session,
    user: User,
    plan: TrainingPlan,
    amended: AmendedPlan,
    load_model,
    *,
    start: date,
    end: date,
    today: date,
) -> tuple:
    """Swap the window's replaceable sessions for the amended ones, in one
    transaction, on the plan the runner is already following.

    The plan row keeps its identity: same id, same rules, same race, same
    `generated_at`. A restore still finds the plan it always did, and the
    completions attached to sessions outside the window are untouched because
    those rows are never loaded.

    The week shapes for weeks now written out are dropped, since a week holding
    real sessions is `planned` and a leftover shape beside it would be a second
    answer about the same week.
    """
    removed: List[str] = []
    for row in sessions_in_window(db, user.id, plan, start, end):
        if not _is_replaceable(row, today):
            continue
        removed.append(_describe_row(row))
        db.delete(row)
    replaced = len(removed)

    added: List[str] = []
    written = 0
    for week in amended.weeks:
        for session in week.sessions:
            db.add(
                PlannedSession(
                    plan_id=plan.id,
                    user_id=user.id,
                    window_start=session.window_start,
                    window_end=session.window_end,
                    intent=session.intent,
                    discipline=session.discipline,
                    commitment=session.commitment,
                    title=session.title,
                    detail=session.detail,
                    target_distance_m=session.target_distance_m,
                    target_duration_s=session.target_duration_s,
                    target_effort_score=estimate_effort(
                        load_model,
                        session.discipline,
                        duration_s=session.target_duration_s,
                        distance_m=session.target_distance_m,
                    ),
                    structure=session.structure(),
                )
            )
            added.append(f"{session.window_start.strftime('%a')} {session.title}")
            written += 1

    written_weeks = {w.week_start for w in amended.weeks}
    plan.week_shapes = [
        shape
        for shape in (plan.week_shapes or [])
        if _shape_week(shape) not in written_weeks
    ]
    # `horizon_end` is a floor on the plan's reach, so it only ever grows here:
    # an amendment that writes sessions past it has extended the plan, and one
    # that writes inside it has not shortened anything.
    if plan.horizon_end is None or plan.horizon_end < end:
        plan.horizon_end = end
    db.commit()
    logger.info(
        "schedule: amended plan %s over %s..%s (%s replaced, %s written)",
        plan.id, start, end, replaced, written,
    )
    return written, _describe_change(removed, added)


def _describe_row(row: Any) -> str:
    """One session the amendment removed, in the terms the runner reads."""
    return f"{row.window_start.strftime('%a')} {row.title}"


def _describe_change(removed: List[str], added: List[str]) -> List[str]:
    """What the window actually holds now that it did not, and vice versa.

    Compared as SETS of "day + title", so a session the rewrite reproduced
    unchanged is not reported as churn. What is left is the real difference, and
    it is what the ledger records and the runner is shown, in place of the card's
    forecast.
    """
    gone = [r for r in removed if r not in set(added)]
    new = [a for a in added if a not in set(removed)]
    lines: List[str] = []
    if gone:
        lines.append("Removed: " + "; ".join(gone))
    if new:
        lines.append("Added: " + "; ".join(new))
    if not lines:
        lines.append("No session in these weeks changed.")
    return lines


def _shape_week(shape: Any) -> Optional[date]:
    raw = shape.get("week_start") if isinstance(shape, dict) else None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None
