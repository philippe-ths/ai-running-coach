"""#830: placement, effective window and status — derived, never stored.

A planned session stores exactly one thing about where it sits: an inclusive
`[window_start, window_end]`. This file pins everything the screen shows about
placement being computed from that pair plus today — the pinned/window/week
reading, the window narrowing as days pass, the status precedence, and the
candidate-day domain rule checking asks for.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from types import SimpleNamespace

from app.services.schedule.placement import (
    candidate_days,
    derive_placement,
    effective_window,
    has_narrowed,
    session_status,
)
from app.services.weeks import MONDAY, SUNDAY

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
THU = MON + timedelta(days=3)
FRI = MON + timedelta(days=4)
SAT = MON + timedelta(days=5)
SUN = MON + timedelta(days=6)
NEXT_MON = MON + timedelta(days=7)


def _session(*, start, end, completed_at=None, dismissed_at=None):
    """A duck-typed stand-in: placement reads only these four attributes."""
    return SimpleNamespace(
        window_start=start,
        window_end=end,
        completed_at=completed_at,
        dismissed_at=dismissed_at,
    )


# --- placement -------------------------------------------------------------


def test_a_single_day_window_is_pinned():
    assert derive_placement(SAT, SAT) == "pinned"


def test_a_window_covering_the_whole_week_reads_as_week():
    assert derive_placement(MON, SUN) == "week"


def test_a_window_between_one_day_and_a_whole_week_reads_as_window():
    assert derive_placement(THU, SAT) == "window"


def test_whole_week_is_read_against_the_runners_own_week_boundary():
    """Sunday-to-Saturday is the whole week for a Sunday-start runner, and only a
    window for a Monday-start one. The same stored dates, two honest readings."""
    sun_start = SUN
    sat_end = SUN + timedelta(days=6)

    assert derive_placement(sun_start, sat_end, SUNDAY) == "week"
    assert derive_placement(sun_start, sat_end, MONDAY) == "window"


# --- effective window ------------------------------------------------------


def test_a_window_that_has_not_opened_yet_is_untouched():
    """A session planned for Saturday has not narrowed just because it is Monday."""
    assert effective_window(THU, SAT, MON) == (THU, SAT)


def test_the_effective_window_starts_at_today_once_the_window_has_opened():
    assert effective_window(THU, SAT, FRI) == (FRI, SAT)


def test_the_effective_window_is_none_once_the_window_has_passed():
    assert effective_window(THU, SAT, SUN) is None


def test_the_stored_window_never_moves_however_it_is_read():
    """Narrowing is a read, not a write: the same stored pair still answers with
    its full original window when asked as of an earlier day."""
    stored = (THU, SAT)

    assert effective_window(*stored, FRI) == (FRI, SAT)
    assert effective_window(*stored, MON) == (THU, SAT)
    assert stored == (THU, SAT)


def test_has_narrowed_is_true_exactly_when_today_has_eaten_into_the_window():
    assert has_narrowed(THU, SAT, MON) is False  # not opened yet
    assert has_narrowed(THU, SAT, THU) is False  # opened, nothing lost
    assert has_narrowed(THU, SAT, FRI) is True  # Thursday is gone
    assert has_narrowed(THU, SAT, SAT) is True  # only the last day left
    assert has_narrowed(THU, SAT, SUN) is False  # lapsed: there is no window


# --- status ----------------------------------------------------------------


def test_completed_wins_over_every_other_reading():
    """A session done on Thursday still reads done next week."""
    session = _session(
        start=THU,
        end=SAT,
        completed_at=date(2026, 8, 13),
        dismissed_at=date(2026, 8, 13),
    )

    assert session_status(session, SUN) == "done"


def test_dismissed_wins_over_a_passed_window():
    """The runner made a choice there; they did not fail to act."""
    session = _session(start=THU, end=SAT, dismissed_at=date(2026, 8, 13))

    assert session_status(session, SUN) == "dismissed"


def test_a_passed_window_with_no_outcome_reads_as_missed():
    assert session_status(_session(start=THU, end=SAT), SUN) == "missed"


def test_a_live_window_reads_as_upcoming():
    assert session_status(_session(start=THU, end=SAT), FRI) == "upcoming"


# --- candidate days --------------------------------------------------------


def test_a_pinned_session_has_a_domain_of_one_day():
    assert candidate_days(_session(start=SAT, end=SAT), MON) == [SAT]


def test_a_floating_session_offers_every_day_of_its_span_earliest_first():
    assert candidate_days(_session(start=THU, end=SAT), MON) == [THU, FRI, SAT]


def test_a_lapsed_session_offers_no_days_at_all():
    assert candidate_days(_session(start=THU, end=SAT), SUN) == []


def test_without_a_today_the_domain_is_the_stored_window():
    """Rule checking a week in the abstract (a coach's draft for next week) asks
    the stored window, not one narrowed by the calendar."""
    assert candidate_days(_session(start=MON, end=SUN)) == [
        MON,
        TUE,
        WED,
        THU,
        FRI,
        SAT,
        SUN,
    ]
    assert candidate_days(_session(start=THU, end=SAT), NEXT_MON) == []


# --- the window invariants, enforced rather than asserted in prose -----------


def test_validate_session_window_rejects_an_inverted_window():
    """An inverted window read as `pinned` while having no days left at all —
    pinned and missed at once. Nothing enforced ordering, so this does."""
    import pytest

    from app.services.schedule.placement import validate_session_window

    with pytest.raises(ValueError, match="after"):
        validate_session_window(SAT, THU)


def test_validate_session_window_rejects_a_window_crossing_a_week_boundary():
    """`sessions_in_range` places a session in a week by its window_start and the
    horizon rolls up on that basis, so a window spilling past the boundary would
    put a session's load in one week and its day in another."""
    import pytest

    from app.services.schedule.placement import validate_session_window

    with pytest.raises(ValueError, match="week"):
        validate_session_window(SAT, SAT + timedelta(days=3))


def test_validate_session_window_accepts_the_three_real_placements():
    from app.services.schedule.placement import validate_session_window

    validate_session_window(THU, THU)  # pinned
    validate_session_window(THU, SAT)  # a window
    validate_session_window(MON, SUN)  # the whole week
