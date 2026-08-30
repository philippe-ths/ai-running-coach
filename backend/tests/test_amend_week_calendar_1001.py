"""#1001: the amendment is told which dates the days fall on.

A plan's rules are claims about WEEKDAYS ("long run on Saturday or Sunday"), and
a session's window has to sit inside one week. Given only each week's first day,
the model had to derive both, and it got them wrong at the join: two separate
generations produced the identical window `2026-09-06..2026-09-07`, the Sunday of
one week and the Monday of the next. That is the exact pair `WRITING_A_SESSION`
uses as its counter-example, so the rule was stated, understood, and unusable.

Naming both ends of every week took two-week first-pass success from 1/2 to 3/4
against the real plan, with the boundary failure gone from four runs.

The second half is the summary. A note ABOUT the amendment threw away the whole
amendment, every session of both weeks, on a length the schema never advertised.
"""

from datetime import date

import pytest

from app.services.schedule import amend
from app.services.weeks import MONDAY, SUNDAY, describe_week_span, week_end


# --- the calendar itself -----------------------------------------------------


def test_a_week_is_described_by_both_its_ends():
    assert describe_week_span(date(2026, 8, 31), MONDAY) == (
        "Mon 2026-08-31 to Sun 2026-09-06"
    )


def test_the_sunday_belongs_to_the_week_it_ends():
    """2026-09-06 is the date the live failures put on the wrong side."""
    assert describe_week_span(date(2026, 9, 6), MONDAY) == (
        "Mon 2026-08-31 to Sun 2026-09-06"
    )
    assert describe_week_span(date(2026, 9, 7), MONDAY) == (
        "Mon 2026-09-07 to Sun 2026-09-13"
    )


def test_the_span_follows_the_runners_own_week_start():
    assert describe_week_span(date(2026, 8, 31), SUNDAY) == (
        "Sun 2026-08-30 to Sat 2026-09-05"
    )
    assert week_end(date(2026, 8, 31), SUNDAY) == date(2026, 9, 5)


@pytest.mark.parametrize("starts_on", [MONDAY, SUNDAY])
def test_every_week_in_the_window_is_listed_once(starts_on):
    weeks = amend._weeks_in(date(2026, 8, 31), date(2026, 9, 13), starts_on)
    assert len(weeks) == len(set(weeks))
    assert all(w == amend.week_start(w, starts_on) for w in weeks)
    assert weeks[0] <= date(2026, 8, 31)
    assert weeks[-1] <= date(2026, 9, 13)


# --- the summary no longer costs the weeks -----------------------------------


def test_a_long_summary_is_trimmed_rather_than_losing_the_amendment():
    raw = {"weeks": [], "summary": "x" * 5_000}
    normalised = amend._normalise(raw)
    assert len(normalised["summary"]) == amend.SUMMARY_MAX_CHARS
    # The point of the trim: this must now VALIDATE.
    assert amend.AmendedPlan.model_validate(normalised) is not None


def test_the_summary_cap_is_advertised_in_the_schema_that_enforces_it():
    prop = amend.RECORD_AMENDMENT_TOOL["input_schema"]["properties"]["summary"]
    assert prop["maxLength"] == amend.SUMMARY_MAX_CHARS
    assert str(amend.SUMMARY_MAX_CHARS) in prop["description"]


def test_nothing_structural_is_repaired_on_the_way_in():
    """Only the summary. A silently fixed session would be one the coach never wrote."""
    raw = {"weeks": [{"week_start": "2026-08-31", "sessions": []}], "summary": "fine"}
    assert amend._normalise(raw) == raw


# --- the context the model actually reads ------------------------------------


def test_the_window_block_names_every_week_by_both_its_ends(db):
    """The fix is only real if it reaches the prompt."""
    from datetime import date as _date

    from app.services.schedule.draft import fetch_draft_facts
    from tests.test_schedule_amend_decides_first_987 import _plan, _user

    user = _user(db)
    plan = _plan(db, user)
    today = _date(2026, 8, 30)
    start, end = amend.resolve_window(today, MONDAY, weeks_from=1, weeks_through=2)

    ctx = amend.build_amend_context(
        db, user, plan, today=today, start=start, end=end,
        instruction="write these weeks", facts=fetch_draft_facts(db, user, today),
    )

    window = ctx[ctx.index("## THE WINDOW"):]
    for week_start_date in amend._weeks_in(start, end, MONDAY):
        span = describe_week_span(week_start_date, MONDAY)
        assert span in window, (
            f"the week {span!r} is not stated, so the model is left deriving it "
            "from a first day, which is how the long run crossed a boundary"
        )
    assert "sits inside ONE of them" in window
