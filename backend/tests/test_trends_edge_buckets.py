"""Edge/partial bucket coverage (#566).

When a week / 2-week / month bucket straddles the selected period boundary,
the builder must report how many of the bucket's days fall inside the period
(``in_period_days``) versus outside it (``out_of_period_days``), so the chart
can fade the partial bar rather than letting it read as a genuine low bucket.
The bucket's aggregated totals are unchanged (still the in-period sum); only
the coverage is added.
"""

from datetime import date, timedelta

from app.services.trends import (
    DailyFact,
    build_period_buckets,
    build_weekly_buckets,
)


def _df(d: date, dist: int = 0) -> DailyFact:
    f = DailyFact(d)
    f.total_distance_m = dist
    return f


# 2026-06-01 is a Monday; 2026-06-30 is a Tuesday.


def test_weekly_trailing_edge_flags_partial_coverage():
    # Calendar June: weeks anchored Mon at Jun 1, 8, 15, 22, 29. The Jun 29 week
    # spans Jun 29 - Jul 5, but the period ends Jun 30, so 2 days are in-period
    # and 5 spill into July.
    facts = [_df(date(2026, 6, 2), 5000), _df(date(2026, 6, 29), 3000)]
    weeks = build_weekly_buckets(facts, since=date(2026, 6, 1), until=date(2026, 6, 30))
    by = {w.week_start: w for w in weeks}

    assert [w.week_start for w in weeks] == [
        date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15),
        date(2026, 6, 22), date(2026, 6, 29),
    ]
    # Full interior weeks carry no spill.
    assert by[date(2026, 6, 1)].out_of_period_days == 0
    assert by[date(2026, 6, 1)].in_period_days == 7
    assert by[date(2026, 6, 8)].out_of_period_days == 0
    # Trailing edge: Jun 29-30 in, Jul 1-5 out.
    assert by[date(2026, 6, 29)].in_period_days == 2
    assert by[date(2026, 6, 29)].out_of_period_days == 5
    # Totals stay the in-period sum (unchanged behaviour).
    assert by[date(2026, 6, 29)].total_distance_m == 3000


def test_weekly_leading_edge_flags_partial_coverage():
    # A window starting mid-week (Wed Jun 3): the leading week (Mon Jun 1) has
    # Jun 1-2 outside the window and Jun 3-7 inside.
    facts = [_df(date(2026, 6, 5), 1000)]
    weeks = build_weekly_buckets(facts, since=date(2026, 6, 3), until=date(2026, 6, 16))
    by = {w.week_start: w for w in weeks}

    assert by[date(2026, 6, 1)].in_period_days == 5
    assert by[date(2026, 6, 1)].out_of_period_days == 2
    # Interior week fully covered.
    assert by[date(2026, 6, 8)].out_of_period_days == 0


def test_all_range_has_no_partial_buckets():
    # ALL (since stays None) frames the whole history, so no bucket is partial.
    facts = [_df(date(2026, 6, 5), 1000)]
    weeks = build_weekly_buckets(
        facts, range_key="ALL", since=None, until=date(2026, 6, 16)
    )
    assert all(w.out_of_period_days == 0 for w in weeks)
    assert all(w.in_period_days == 7 for w in weeks)


def test_monthly_leading_edge_flags_partial_coverage():
    # Window starts mid-May; the May month bucket has May 1-9 outside and
    # May 10-31 inside. June is fully inside; July ends exactly at the window.
    facts = [_df(date(2026, 5, 15), 1000)]
    months = build_period_buckets(
        facts, "monthly", since=date(2026, 5, 10), until=date(2026, 7, 31)
    )
    by = {m.period_start: m for m in months}

    assert by[date(2026, 5, 1)].in_period_days == 22  # May 10-31
    assert by[date(2026, 5, 1)].out_of_period_days == 9  # May 1-9
    assert by[date(2026, 6, 1)].out_of_period_days == 0  # full month
    assert by[date(2026, 7, 1)].out_of_period_days == 0  # ends at window


def test_biweekly_bucket_coverage_spans_fourteen_days():
    # A fortnight bucket straddling the window start splits across 14 days.
    facts = [_df(date(2026, 6, 10), 1000)]
    fortnights = build_period_buckets(
        facts, "biweekly", since=date(2026, 6, 10), until=date(2026, 6, 30)
    )
    for fn in fortnights:
        assert fn.in_period_days + fn.out_of_period_days == 14
        assert fn.in_period_days >= 0 and fn.out_of_period_days >= 0
