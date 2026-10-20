"""What an amendment is: its limits, its cost estimates, its tool and prompt,
and the two result shapes (#981).

Split out of `amend.py`; import everything from there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.schedule import store
from app.services.schedule.draft import PLACING_AND_COMMITTING, WRITING_A_SESSION
from app.services.schedule.draft_contract import SESSION_PROPERTIES, DraftedWeek

# How far an amendment may reach. Wide enough for the two real cases (the rest of
# this week; the next block, written out of its sketch) and narrow enough that
# "amend" cannot quietly become "redraft" and take the plan with it.
MAX_AMEND_WEEKS = 6

# How long the amendment's own note about itself may be. Stated in the schema
# and truncated on the way in, because it is the one field here that is purely
# cosmetic: a summary running long threw away the whole amendment, every
# session of both weeks, on `String should have at most 600 characters` (#1001).
SUMMARY_MAX_CHARS = 600


# What one generation of an N-week amendment actually costs, measured (#995).
#
# Against the real production plan on 2026-08-30, per generation:
#
#     window   per-generation seconds
#     1 week   23.5
#     2 weeks  34.6, 40.6
#     4 weeks  53.8, 60.9
#     6 weeks  ~65
#
# which `14 + 10 * weeks` tracks closely and slightly conservatively. This is an
# estimate of the MODEL, so it is a straight line fitted to observation rather
# than anything derivable: when the model or the prompt changes materially,
# re-measure rather than reason about it.
_ESTIMATED_BASE_SECONDS = 14.0
_ESTIMATED_SECONDS_PER_WEEK = 10.0


def estimated_seconds(weeks: int) -> float:
    """Wall-clock a single amendment generation of this size should be given."""
    return _ESTIMATED_BASE_SECONDS + _ESTIMATED_SECONDS_PER_WEEK * max(1, weeks)


def weeks_that_fit(budget_seconds: float) -> int:
    """The widest window one generation can settle inside `budget_seconds`.

    Zero when not even a single week fits, which the caller must read as "do not
    start" rather than as "ask for one week".
    """
    fits = int((budget_seconds - _ESTIMATED_BASE_SECONDS) // _ESTIMATED_SECONDS_PER_WEEK)
    return max(0, min(MAX_AMEND_WEEKS, fits))


# Output room for an N-week amendment. Sized to the work rather than flat, so the
# ceiling `structured_timeout_for` derives from it is a truthful statement about
# THIS call — a flat 8192 claimed 205 seconds for a one-week rewrite that takes
# 24, which is how a generation came to outlive the request carrying it (#995).
#
# Generous against the observed shape: a settled 4-week window came back as 28
# sessions, so ~1300 tokens per week leaves real headroom. Undersizing this is
# not a soft failure — `generate_structured` raises on a `max_tokens` stop by
# design (#931) rather than returning half a plan.
_MAX_TOKENS_BASE = 1200
_MAX_TOKENS_PER_WEEK = 1300


def amend_max_tokens(weeks: int) -> int:
    return min(8192, _MAX_TOKENS_BASE + _MAX_TOKENS_PER_WEEK * max(1, weeks))


class AmendedPlan(BaseModel):
    """The weeks the amendment rewrites. Sessions only, deliberately.

    No rules and no sketch weeks: the plan already has both and an amendment
    does not get to change them. Reusing `DraftedWeek` rather than declaring a
    parallel shape means an amended session is held to exactly the contract a
    drafted one is, down to the rep structure and the warm-up in metres.
    """

    model_config = ConfigDict(extra="forbid")

    weeks: List[DraftedWeek] = Field(default_factory=list, max_length=MAX_AMEND_WEEKS)
    summary: Optional[str] = Field(default=None, max_length=SUMMARY_MAX_CHARS)


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
                                    "target_duration_s",
                                ],
                                "properties": SESSION_PROPERTIES,
                            },
                        },
                    },
                },
            },
            "summary": {
                "type": "string",
                "maxLength": SUMMARY_MAX_CHARS,
                "description": (
                    "One or two sentences on what you changed and why, in the "
                    f"runner's terms. At most {SUMMARY_MAX_CHARS} characters."
                ),
            },
        },
    },
}


_SYSTEM_PROMPT = (
    """You are a running coach amending this runner's existing training plan.

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

"""
    + PLACING_AND_COMMITTING
    + WRITING_A_SESSION
    + "Answer only by calling record_plan_amendment."
)


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
    # What code lengthened or added to hold a challenge or the usual walking after
    # the retry (#1064), and what is still short once it had. Both also ride in
    # `changes`, so the card and the ledger say them.
    repairs: List[str] = field(default_factory=list)
    shortfalls: List[str] = field(default_factory=list)
