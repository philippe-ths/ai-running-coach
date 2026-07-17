"""Unit tests for the single week-boundary definition (#676).

The whole point of `services/weeks.py` is that every coach-pack and Trends
surface derives "the week" from ONE place, parameterized by the runner's
`week_starts_on` (0=Monday .. 6=Sunday). `starts_on=0` must reproduce the
prior hardcoded-Monday idiom byte-for-byte; `starts_on=6` is the net-new
Sunday behavior.
"""

from datetime import date

from app.services.weeks import (
    MONDAY,
    SUNDAY,
    days_into_week,
    is_last_day_of_week,
    resolve_week_start,
    week_start,
)


# A known week: Mon 2024-01-01 .. Sun 2024-01-07, then Mon 2024-01-08.
MON = date(2024, 1, 1)
TUE = date(2024, 1, 2)
WED = date(2024, 1, 3)
THU = date(2024, 1, 4)
FRI = date(2024, 1, 5)
SAT = date(2024, 1, 6)
SUN = date(2024, 1, 7)
NEXT_MON = date(2024, 1, 8)


class TestConstants:
    def test_monday_is_zero_sunday_is_six(self):
        assert MONDAY == 0
        assert SUNDAY == 6


class TestWeekStartMonday:
    """starts_on defaults to Monday and reproduces `d - timedelta(days=d.weekday())`."""

    def test_default_is_monday(self):
        assert week_start(WED) == MON

    def test_every_day_maps_to_its_monday(self):
        for d in (MON, TUE, WED, THU, FRI, SAT, SUN):
            assert week_start(d, MONDAY) == MON

    def test_next_monday_starts_new_week(self):
        assert week_start(NEXT_MON, MONDAY) == NEXT_MON

    def test_matches_weekday_idiom(self):
        # The exact idiom every surface used before the reconcile.
        from datetime import timedelta

        for offset in range(400):
            d = MON + timedelta(days=offset)
            assert week_start(d, MONDAY) == d - timedelta(days=d.weekday())


class TestWeekStartSunday:
    """starts_on=Sunday shifts the boundary back a day."""

    def test_sunday_is_its_own_start(self):
        assert week_start(SUN, SUNDAY) == SUN

    def test_monday_belongs_to_the_prior_sunday(self):
        # Sun 2023-12-31 is the start of the week containing Mon 2024-01-01.
        assert week_start(MON, SUNDAY) == date(2023, 12, 31)

    def test_saturday_still_in_the_sunday_week(self):
        assert week_start(SAT, SUNDAY) == date(2023, 12, 31)

    def test_next_sunday_starts_new_week(self):
        assert week_start(NEXT_MON, SUNDAY) == SUN


class TestDaysIntoWeek:
    """1-based position of d within its week (Mon=1..Sun=7 for a Monday week)."""

    def test_monday_week(self):
        assert days_into_week(MON, MONDAY) == 1
        assert days_into_week(SUN, MONDAY) == 7
        assert days_into_week(WED, MONDAY) == 3

    def test_sunday_week(self):
        assert days_into_week(SUN, SUNDAY) == 1
        assert days_into_week(MON, SUNDAY) == 2
        assert days_into_week(SAT, SUNDAY) == 7


class _Profile:
    def __init__(self, week_starts_on):
        self.week_starts_on = week_starts_on


class TestResolveWeekStart:
    def test_none_profile_is_monday(self):
        assert resolve_week_start(None) == MONDAY

    def test_null_setting_is_monday(self):
        assert resolve_week_start(_Profile(None)) == MONDAY

    def test_explicit_monday(self):
        assert resolve_week_start(_Profile(MONDAY)) == MONDAY

    def test_explicit_sunday(self):
        assert resolve_week_start(_Profile(SUNDAY)) == SUNDAY


class TestIsLastDayOfWeek:
    def test_monday_week_last_day_is_sunday(self):
        assert is_last_day_of_week(SUN, MONDAY) is True
        assert is_last_day_of_week(SAT, MONDAY) is False
        assert is_last_day_of_week(MON, MONDAY) is False

    def test_sunday_week_last_day_is_saturday(self):
        assert is_last_day_of_week(SAT, SUNDAY) is True
        assert is_last_day_of_week(SUN, SUNDAY) is False
        assert is_last_day_of_week(FRI, SUNDAY) is False
