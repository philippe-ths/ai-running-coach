"""#400: the deterministic volume-vs-norm signal (pure builder).

The acceptance: a deliberate easy week reads as `down` vs the runner's norm
(not alarming), a normal week reads `in_line`, a ramp reads `up`, the runs-only
figure rides alongside the holistic total, and thin history abstains (`no_norm`).
"""

from datetime import date, timedelta
from types import SimpleNamespace

from app.services.coach.volume import build_training_volume, build_volume_report


def _fact(d: date, *, type="Run", distance_m=10000, moving_time_s=3600, effort_score=50.0):
    return SimpleNamespace(
        local_date=d, activity_type=type,
        distance_m=distance_m, moving_time_s=moving_time_s, effort_score=effort_score,
    )


def _baseline(as_of: date):
    """13 identical prior weeks, each 5 sessions (4 runs + 1 walk), 50km, 18000s,
    250 effort. The runner thus has continuous history extending BEYOND the 12-week
    (84-day) norm window, so the clamped per-day-rate norm (#451) is computed over the
    full window and equals the clean per-week figure by construction: 5 sessions /
    50km / 250 effort per week. (A runner with LESS history is the divergence case —
    see test_short_history_uses_honest_per_day_rate.)"""
    baseline_end = as_of - timedelta(days=7)
    facts = []
    for week in range(13):
        wk_anchor = baseline_end - timedelta(days=week * 7)
        for offset in range(4):  # 4 runs
            facts.append(_fact(wk_anchor - timedelta(days=offset)))
        facts.append(_fact(wk_anchor - timedelta(days=4), type="Walk"))  # 1 walk
    return facts


def _by_metric(window):
    return {m.metric: m for m in window.metrics}


def test_deliberate_easy_week_reads_down():
    """The headline acceptance: a light current week is `down` vs norm, not alarming."""
    as_of = date(2026, 6, 20)
    facts = _baseline(as_of)
    # Current 7d: just 2 easy runs (20km) — well under the 50km/5-session norm.
    facts += [_fact(as_of), _fact(as_of - timedelta(days=2))]

    vol = build_training_volume(facts, as_of)
    assert vol.has_baseline is True
    r = _by_metric(vol.rolling_7d)
    assert r["sessions"].current_all == 2
    assert r["sessions"].norm_weekly == 5.0
    assert r["sessions"].direction == "down"
    assert r["distance_m"].direction == "down"
    assert r["moving_time_s"].direction == "down"
    assert r["effort_score"].direction == "down"
    assert r["distance_m"].pct_vs_norm == -60.0  # 20km vs 50km


def test_short_history_uses_honest_per_day_rate_not_deflated_per_week():
    """#451: 'typical' is the clamped per-day rate, not total / a fixed 12 weeks. A
    runner with only ~4 weeks of history (28 consecutive daily runs) has a true rate of
    7 sessions / 70km a week; the per-day rate reports that honestly, where the old
    divide-by-12 would deflate it to ~2.3/week and mislabel a normal week as a spike."""
    as_of = date(2026, 6, 20)
    baseline_end = as_of - timedelta(days=7)
    # 28 consecutive days of one 10km run/day, all BEFORE the current 7-day window.
    facts = [_fact(baseline_end - timedelta(days=d)) for d in range(28)]
    # A normal current week for this runner: 7 daily runs.
    facts += [_fact(as_of - timedelta(days=d)) for d in range(7)]

    vol = build_training_volume(facts, as_of)
    assert vol.has_baseline is True
    r = _by_metric(vol.rolling_7d)
    # Clamped window = the 28 actual days; per-day = 28/28 = 1 -> weekly norm = 7.0,
    # NOT 28/12 ~= 2.3 (the deflated per-week figure the old definition produced).
    assert r["sessions"].norm_weekly == 7.0
    assert r["distance_m"].norm_weekly == 70000.0  # 280km / 28d * 7
    # The current week matches that rate, so it reads in_line, not a false 'up' spike.
    assert r["sessions"].current_all == 7
    assert r["sessions"].direction == "in_line"


def test_normal_week_reads_in_line():
    as_of = date(2026, 6, 20)
    facts = _baseline(as_of)
    # Current 7d mirrors a baseline week: 4 runs + 1 walk.
    for offset in range(4):
        facts.append(_fact(as_of - timedelta(days=offset)))
    facts.append(_fact(as_of - timedelta(days=4), type="Walk"))

    r = _by_metric(build_training_volume(facts, as_of).rolling_7d)
    assert r["sessions"].direction == "in_line"
    assert r["distance_m"].direction == "in_line"


def test_ramp_week_reads_up():
    as_of = date(2026, 6, 20)
    facts = _baseline(as_of)
    # Current 7d well above norm: 8 runs / 80km.
    for offset in range(7):
        facts.append(_fact(as_of - timedelta(days=offset)))
    facts.append(_fact(as_of))  # an extra same-day session

    r = _by_metric(build_training_volume(facts, as_of).rolling_7d)
    assert r["sessions"].direction == "up"
    assert r["distance_m"].direction == "up"


def test_runs_only_breakdown_rides_alongside_holistic():
    as_of = date(2026, 6, 20)
    facts = _baseline(as_of)
    # Current 7d: 1 run + 2 walks + 1 ride = 4 sessions, 1 of them a run.
    facts += [
        _fact(as_of),
        _fact(as_of - timedelta(days=1), type="Walk"),
        _fact(as_of - timedelta(days=2), type="Walk"),
        _fact(as_of - timedelta(days=3), type="Ride"),
    ]
    r = _by_metric(build_training_volume(facts, as_of).rolling_7d)
    assert r["sessions"].current_all == 4
    assert r["sessions"].current_runs == 1
    assert r["distance_m"].current_all == 40000
    assert r["distance_m"].current_runs == 10000


def test_thin_history_abstains():
    as_of = date(2026, 6, 20)
    # Only 2 prior activities — below the baseline threshold.
    facts = [
        _fact(as_of - timedelta(days=20)),
        _fact(as_of - timedelta(days=30)),
        _fact(as_of),  # current
    ]
    vol = build_training_volume(facts, as_of)
    assert vol.has_baseline is False
    r = _by_metric(vol.rolling_7d)
    assert r["sessions"].norm_weekly is None
    assert r["sessions"].direction == "no_norm"
    assert r["sessions"].pct_vs_norm is None


def test_calendar_week_is_partial_and_prorated():
    """A partial Mon-Sun week is judged against the norm pro-rated to elapsed days."""
    # 2026-06-17 is a Wednesday -> 3 days elapsed (Mon, Tue, Wed).
    as_of = date(2026, 6, 17)
    assert as_of.weekday() == 2  # Wednesday
    facts = _baseline(as_of)
    # This week so far: 3 runs (30km) over Mon-Wed. Norm/week is 50km; pro-rated to
    # 3/7 of a week that's ~21.4km, so 30km is actually ABOVE the pro-rated norm.
    facts += [_fact(as_of), _fact(as_of - timedelta(days=1)), _fact(as_of - timedelta(days=2))]

    cw = build_training_volume(facts, as_of).calendar_week
    assert cw.days_elapsed == 3
    assert cw.complete is False
    d = _by_metric(cw)["distance_m"]
    assert d.current_all == 30000
    assert d.direction == "up"  # 30km vs the ~21.4km pro-rated norm


# --- range-aware report (Trends page) -----------------------------------------


def _history(as_of: date, n_days: int, *, per_day=1, distance_m=10000):
    """`per_day` activities per day for `n_days` days ending on as_of."""
    facts = []
    for i in range(n_days):
        d = as_of - timedelta(days=i)
        for _ in range(per_day):
            facts.append(_fact(d, distance_m=distance_m))
    return facts


def _by(framing):
    return {m.metric: m for m in framing.metrics}


def test_report_range_drives_labels_and_windows():
    as_of = date(2026, 6, 20)  # June, a Saturday
    facts = _history(as_of, 200)
    r = build_volume_report(facts, as_of, "30D")
    assert r.range == "30D"
    assert r.rolling.label == "30-day rolling"
    assert r.rolling.window_days == 30
    assert r.calendar.label == "This month"
    assert r.calendar.window_days == 30          # June has 30 days
    assert r.calendar.days_elapsed == 20         # 1st..20th
    assert r.calendar.complete is False


def test_report_consistent_history_reads_in_line():
    as_of = date(2026, 6, 20)
    r = build_volume_report(_history(as_of, 200), as_of, "30D")  # uniform density
    by = _by(r.rolling)
    assert by["sessions"].direction == "in_line"
    assert by["distance_m"].direction == "in_line"


def test_report_light_current_window_reads_down():
    as_of = date(2026, 6, 20)
    facts = _history(as_of - timedelta(days=30), 170)  # dense history ending 30d ago
    for i in range(0, 30, 3):                            # a sparse current 30 days
        facts.append(_fact(as_of - timedelta(days=i)))
    by = _by(build_volume_report(facts, as_of, "30D").rolling)
    assert by["sessions"].direction == "down"


def test_report_calendar_period_label_per_range():
    as_of = date(2026, 6, 20)
    facts = _history(as_of, 400)
    assert build_volume_report(facts, as_of, "3M").calendar.label == "This quarter"
    assert build_volume_report(facts, as_of, "6M").calendar.label == "This half-year"
    assert build_volume_report(facts, as_of, "1Y").calendar.label == "This year"
    # Q2 (Apr-Jun) is 91 days; first half-year is 181; the year is 365.
    assert build_volume_report(facts, as_of, "3M").calendar.window_days == 91
    assert build_volume_report(facts, as_of, "1Y").calendar.window_days == 365


def test_report_exposes_period_and_baseline_dates():
    as_of = date(2026, 6, 20)
    r = build_volume_report(_history(as_of, 400), as_of, "30D")
    assert r.baseline_label == "the last 6 months"
    assert r.rolling.period_start == date(2026, 5, 22)   # trailing 30 days
    assert r.rolling.period_end == as_of
    assert r.rolling.baseline_end == date(2026, 5, 21)   # day before the window
    assert r.rolling.baseline_start == date(2026, 5, 21) - timedelta(days=167)  # 168d
    assert r.calendar.period_start == date(2026, 6, 1)   # this month


def test_report_baseline_clamped_to_history():
    as_of = date(2026, 6, 20)
    earliest = as_of - timedelta(days=59)
    r = build_volume_report(_history(as_of, 60), as_of, "30D")  # only 60 days of data
    # The 168-day nominal baseline cannot start before the runner's first activity.
    assert r.rolling.baseline_start >= earliest


def test_report_baseline_label_scales_with_term():
    as_of = date(2026, 6, 20)
    facts = _history(as_of, 400)
    assert build_volume_report(facts, as_of, "7D").baseline_label == "the last 12 weeks"
    assert build_volume_report(facts, as_of, "3M").baseline_label == "the last year"


# --- #436: calendar "vs typical" is the FULL-period typical, not pro-rated --------


def test_report_calendar_vs_typical_is_full_period_not_prorated():
    """A partial calendar week is judged against the typical FULL week (#436), so a
    week 3 days in reads `down`, not `in_line`. Contrast with the coach pack's
    calendar_week, which stays pro-rated (test_calendar_week_is_partial_and_prorated)."""
    as_of = date(2026, 6, 17)  # Wednesday -> Mon..Wed elapsed (3 of 7 days)
    assert as_of.weekday() == 2
    # Uniform history: 1 activity/day of 10km, so the per-day norm is exactly
    # 1 session / 10000 m / 3600 s / 50 effort.
    cal = build_volume_report(_history(as_of, 200), as_of, "7D").calendar
    assert cal.days_elapsed == 3
    assert cal.complete is False
    d = _by(cal)["distance_m"]
    assert d.current_all == 30000          # Mon+Tue+Wed at 10km
    assert d.norm == 70000.0               # typical FULL week = 10km/day * 7, not * 3
    assert d.pct_vs_norm == -57.1          # (30000-70000)/70000
    assert d.direction == "down"
    s = _by(cal)["sessions"]
    assert s.current_all == 3
    assert s.norm == 7.0                   # 1/day * 7 days


def test_report_rolling_unchanged_by_full_period_norm():
    """Rolling is a complete window (days_elapsed == window_days), so the #436 change
    is a no-op there: a uniform trailing week still reads in_line against its norm."""
    as_of = date(2026, 6, 17)
    roll = build_volume_report(_history(as_of, 200), as_of, "7D").rolling
    assert roll.days_elapsed == roll.window_days == 7
    d = _by(roll)["distance_m"]
    assert d.current_all == 70000          # full trailing 7 days at 10km
    assert d.norm == 70000.0
    assert d.pct_vs_norm == 0.0
    assert d.direction == "in_line"


def test_report_calendar_full_period_applies_to_longer_terms():
    """The full-period typical applies to every range, not just 7D (#436): a month
    20 days in is judged against the typical full month."""
    as_of = date(2026, 6, 20)  # June (30 days), 20 elapsed
    cal = build_volume_report(_history(as_of, 400), as_of, "30D").calendar
    assert cal.days_elapsed == 20
    d = _by(cal)["distance_m"]
    assert d.current_all == 200000         # 20 days at 10km
    assert d.norm == 300000.0              # typical FULL month = 10km/day * 30
    assert d.pct_vs_norm == -33.3
    assert d.direction == "down"
