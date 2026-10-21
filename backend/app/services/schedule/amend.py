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
import time
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.services.coach import turn
from app.services.coach.llm import is_unreachable
from app.services.schedule import goals
from app.services.schedule import season_store, store
from app.services.schedule.draft import fetch_draft_facts
from app.services.schedule.effort import build_load_model
from app.services.schedule.frames import build_frames
from app.services.schedule.norms import running_norm_weekly_m, weekly_hours_norm_s
from app.services.schedule.plan_validator import VOLUME_CEILING, validate_amendment
from app.services.schedule.repair import repair_weeks
from app.services.schedule.runner_rules import with_runner_rules
from app.services.weeks import resolve_week_start, week_start

# The amendment's pieces live in sibling modules; they are imported here so every
# name other modules and tests import from here still resolves (#1098).
from app.services.schedule.amend_contract import (  # noqa: F401
    MAX_AMEND_WEEKS,
    RECORD_AMENDMENT_TOOL,
    SUMMARY_MAX_CHARS,
    AmendedPlan,
    AmendOutcome,
    AmendProposal,
    _ESTIMATED_BASE_SECONDS,
    _ESTIMATED_SECONDS_PER_WEEK,
    _MAX_TOKENS_BASE,
    _MAX_TOKENS_PER_WEEK,
    _SYSTEM_PROMPT,
    amend_max_tokens,
    estimated_seconds,
    weeks_that_fit,
)
from app.services.schedule.amend_window import (  # noqa: F401
    _is_replaceable,
    _shape_lines,
    _weeks_in,
    build_amend_context,
    resolve_window,
    sessions_in_window,
)
from app.services.schedule.amend_write import (  # noqa: F401
    _apply,
    _describe_change,
    _describe_row,
    _normalise,
    _restate_shortfalls,
    _shape_week,
)

logger = logging.getLogger(__name__)


async def propose_amendment(
    db: Session,
    user: User,
    plan: TrainingPlan,
    *,
    weeks_from: int,
    weeks_through: int,
    instruction: str,
    today: Optional[date] = None,
    budget_seconds: Optional[float] = None,
) -> AmendProposal:
    """Settle what the window WOULD hold, and write none of it (#987).

    Everything the amendment decides happens here: the same envelope, coercion
    and coherence gate the draft uses, because an amendment reaching the schedule
    through a conversation is not held to a lower bar than a plan is.

    It stops at the point of writing. A proposal that cannot satisfy the plan's
    own rules comes back refused, carrying the rule it could not satisfy, and the
    coach answers the runner with that rather than putting up a card for a change
    it cannot honour.

    `budget_seconds` is the wall-clock this may take when it is running INSIDE a
    request that will be killed at a ceiling (#995). Given one, a window too wide
    to settle within it is refused BEFORE any tokens are spent, carrying the
    window that would have fitted — so the coach narrows the ask in conversation
    instead of starting work that gets severed halfway. Left None, there is no
    ceiling to respect and the size gate does not apply.
    """
    today = today or date.today()
    starts_on = resolve_week_start(getattr(user, "profile", None))
    start, end = resolve_window(
        today, starts_on, weeks_from=weeks_from, weeks_through=weeks_through
    )
    weeks = max(1, weeks_through - weeks_from + 1)

    if budget_seconds is not None and estimated_seconds(weeks) > budget_seconds:
        fits = weeks_that_fit(budget_seconds)
        if fits <= 0:
            detail = (
                "there is no time left in this message to work out an amendment; "
                "ask the runner to send the request again on its own"
            )
        else:
            detail = (
                f"a {weeks}-week amendment takes longer than one message allows; "
                f"offer to settle {fits} week{'s' if fits > 1 else ''} now "
                f"(weeks_from={weeks_from}, weeks_through={weeks_from + fits - 1}) "
                "and do the rest in a follow-up"
            )
        logger.info(
            "schedule amend: %s-week window refused, %.0fs budget fits %s",
            weeks, budget_seconds, fits,
        )
        return AmendProposal(ok=False, start=start, end=end, failures=[detail])

    # The weeks are framed by the active season, so a season written for goals the
    # runner has since changed would frame them against a plan they no longer
    # have. Refused before any tokens are spent, in words the coach can pass on.
    season_row = season_store.active_season(db, user.id)
    if season_row is not None and season_store.is_stale(db, season_row):
        logger.info("schedule amend: season %s is stale; refusing", season_row.id)
        return AmendProposal(
            ok=False, start=start, end=end, failures=[season_store.GOALS_CHANGED_MESSAGE]
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
    norm_hours = weekly_hours_norm_s(facts, today)
    # The runner's own rules (#1080) bind the amendment, its repair and its
    # check exactly as the plan's do.
    rules = with_runner_rules(store.plan_rules(plan), getattr(user, "profile", None))
    races = store.list_goal_races(db, user.id, on_or_after=today)
    race_arg = goals.validator_race(races)
    # The window's weeks, framed by the active season (or by the runner's usual
    # week alone when there is none), so an amendment is held to the challenge,
    # the walking and the goal days exactly as the draft was.
    first_week = week_start(today, starts_on)
    frames = build_frames(
        season=season_store.season_plan(season_row) if season_row is not None else None,
        goals=store.list_goal_races(db, user.id),
        facts=facts,
        starts_on=starts_on,
        today=today,
        horizon_weeks=max(1, (week_start(end, starts_on) - first_week).days // 7 + 1),
    )
    window_weeks = set(_weeks_in(start, end, starts_on))
    frames = [f for f in frames if f.week_start in window_weeks]
    frames_by_week = {f.week_start: f for f in frames}

    failures: List[str] = []
    failure_kind = store.FAILURE_UNKNOWN
    rewrites_left = 2
    transport_retries_left = 1
    max_tokens = amend_max_tokens(weeks)
    started_at = time.monotonic()
    # The last attempt that was rejected, kept for the repair below: whichever way
    # the attempts end (spent, or stopped by the time budget), a coherent plan that
    # is only short of a number gets its numbers closed in code before it is lost.
    pending = None

    while rewrites_left > 0:
        # #995: a rewrite is a whole second generation, and the budget covers one.
        # Checked per attempt rather than once up front, because the decision is
        # about the time LEFT: a first pass that ran long has already spent what
        # the retry would need, and starting it anyway is how the request comes to
        # be severed with nothing to show. Stopping here returns the real failures
        # instead, which is a worse plan than a rewrite would have found and a far
        # better answer than silence.
        if budget_seconds is not None:
            left = budget_seconds - (time.monotonic() - started_at)
            if left < estimated_seconds(weeks):
                logger.info(
                    "schedule amend: stopping after %d attempt(s), %.0fs left of %.0fs",
                    2 - rewrites_left, left, budget_seconds,
                )
                break

        pending = None
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
                max_tokens=max_tokens,
                # Never outlive the request carrying this call. The derived
                # ceiling is a hung-call guard sized for the work; the budget is
                # what the platform will actually allow. The smaller wins (#995).
                timeout=(
                    None if budget_seconds is None
                    else max(1.0, budget_seconds - (time.monotonic() - started_at))
                ),
            )
        except ValueError as exc:
            # The call ran and its answer failed: cut off, or no tool call. That
            # is the coach's output, not the network, so it spends a rewrite and
            # the coach is told why (#999), as the draft does.
            logger.warning("schedule amend: unusable answer: %s", exc)
            rewrites_left -= 1
            tool_name = RECORD_AMENDMENT_TOOL["name"]
            failures = [
                f"Your previous answer was cut off, or did not call {tool_name}, "
                f"so none of it could be read. Answer only by calling {tool_name}, "
                "with no field longer than it needs to be."
            ]
            continue
        except Exception as exc:
            # Only a call that never got through is "could not be reached". A
            # bad kwarg or any other bug in this code is raised so it is found:
            # caught here, a test double missing the new `timeout` kwarg reported
            # itself to the runner as an unreachable coach after two retries that
            # reached nothing (#995, #999).
            if not is_unreachable(exc):
                raise
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
            norm_weekly_s=norm_hours,
            frames=frames,
        )
        if not check.ok:
            logger.info("schedule amend: rejected: %s", check.failures)
            pending = (amended, check, surviving, rows)
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

    if pending is not None and pending[1].only_numeric:
        amended, check, surviving, rows = pending
        repaired = _repair_amendment(
            amended, check, frames_by_week, rules, surviving, rows, today,
            validate=lambda candidate: validate_amendment(
                candidate.weeks, rules=rules, surviving_by_week=surviving,
                today=today, starts_on=starts_on,
                norm_weekly_running_m=norm_running,
                expected_weeks=_weeks_in(start, end, starts_on),
                race=race_arg, norm_weekly_s=norm_hours, frames=frames,
            ),
            start=start, end=end,
        )
        if repaired is not None:
            return repaired
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


def _repair_amendment(
    amended: "AmendedPlan",
    check,
    frames_by_week,
    rules,
    surviving,
    rows,
    today: date,
    *,
    validate,
    start: date,
    end: date,
) -> Optional["AmendProposal"]:
    """Close a coherent amendment's numeric shortfalls in code (#1064), or None.

    The retry is spent and the only failures left are arithmetic (the challenge,
    the usual walking). Repair lengthens or adds easy sessions where the ceilings
    allow; whatever is still short is said on the card instead of being applied
    silently. A repair that makes the amendment fail anything else is discarded.
    """
    weeks, notes = repair_weeks(
        amended.weeks, check, frames_by_week, rules=rules, fixed_by_week=surviving
    )
    repaired = amended.model_copy(update={"weeks": weeks})
    final = validate(repaired)
    if not (final.ok or final.only_numeric):
        return None
    shortfalls = [f.shortfall for f in final.week_failures if f.shortfall]
    changes = _forecast_change(rows, repaired, today)
    changes.extend(notes)
    changes.extend(f"Still short: {line}" for line in shortfalls)
    return AmendProposal(
        ok=True, amended=repaired, changes=changes, start=start, end=end,
        repairs=notes, shortfalls=shortfalls,
    )


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
        shortfalls=proposal.shortfalls,
    )
    # What code did to hold the numbers, and what is still short once it had, is
    # part of what the amendment DID: the ledger replaces the card's forecast with
    # this, so a line left off here is a line the runner and the coach never see.
    changes = [*changes, *proposal.repairs, *(f"Still short: {l}" for l in proposal.shortfalls)]
    return AmendOutcome(
        ok=True,
        summary=proposal.amended.summary,
        weeks_touched=len(proposal.amended.weeks),
        sessions_written=written,
        changes=changes,
    )

