"""ADR 0026 Slice 2 (#670): the pure `build_recent_weeks` builder.

Ground truth is deterministic arithmetic over constructed facts: the window/rolling
totals are sums of the day rows, the by-type breakdown partitions them, and the
vs-your-typical verdict reuses the volume core's per-day-rate norm. The load-verdict
lives on the always-full rolling-7d window and the complete last week; the partial
this-week carries NO verdict (the #653 fix).
"""

from datetime import date, timedelta

import pytest

from app.services.coach.recent_weeks import CheckInFacts, build_recent_weeks


class FakeFact:
    """Duck-typed ActivityFact for the pure builder (the same attributes context.py's
    `_query_activity_facts(include_session_shape=True)` projection carries)."""

    def __init__(
        self,
        activity_id,
        local_date,
        activity_type="Run",
        distance_m=10000,
        moving_time_s=3000,
        effort_score=60.0,
        effort="easy",
        elev_gain_m=0.0,
        avg_hr=None,
        hr_drift=None,
        structure=None,
        interval_structure=None,
        duration_class=None,
    ):
        self.activity_id = activity_id
        self.local_date = local_date
        self.activity_type = activity_type
        self.distance_m = distance_m
        self.moving_time_s = moving_time_s
        self.effort_score = effort_score
        self.effort = effort
        self.elev_gain_m = elev_gain_m
        self.avg_hr = avg_hr
        self.hr_drift = hr_drift
        self.structure = structure
        self.interval_structure = interval_structure
        self.duration_class = duration_class


# A Wednesday, so this_week is Mon-Wed (partial) and last_week is the prior Mon-Sun.
AS_OF = date(2026, 7, 15)
MONDAY = date(2026, 7, 13)


@pytest.fixture(autouse=True)
def _as_of_is_wednesday():
    assert AS_OF.weekday() == 2  # guard the fixture's calendar assumptions
    assert MONDAY.weekday() == 0


def _baseline_history(n=10):
    """Runs spread across the ~12 weeks BEFORE the recent windows, so the per-day-rate
    norm resolves (>= _MIN_BASELINE_ACTIVITIES fall in every window's baseline)."""
    return [
        FakeFact(f"h{i}", AS_OF - timedelta(days=21 + 7 * i), distance_m=10000,
                 moving_time_s=3000, effort_score=60.0)
        for i in range(n)
    ]


def _recent_facts():
    return [
        # last week (Mon 06 .. Sun 12)
        FakeFact("l_tue", date(2026, 7, 7), distance_m=10000, moving_time_s=3120,
                 effort_score=70.0, effort="easy", elev_gain_m=120.0, avg_hr=140.0, hr_drift=4.2),
        FakeFact("l_wed", date(2026, 7, 8), distance_m=6000, moving_time_s=1800,
                 effort_score=40.0, effort="moderate"),
        FakeFact("l_fri", date(2026, 7, 10), distance_m=14000, moving_time_s=4080,
                 effort_score=180.0, effort="hard", structure="intervals",
                 interval_structure={"work_segments": [{"distance_m": 1000}] * 5}),
        FakeFact("l_sat", date(2026, 7, 11), activity_type="Walk", distance_m=4500,
                 moving_time_s=2880, effort_score=20.0, effort=None),
        FakeFact("l_sun", date(2026, 7, 12), distance_m=22000, moving_time_s=6720,
                 effort_score=205.0, effort="easy", duration_class="long"),
        # this week (Mon 13 .. Wed 15)
        FakeFact("t_tue", date(2026, 7, 14), distance_m=8200, moving_time_s=2530,
                 effort_score=72.0, effort="easy"),
        FakeFact("t_wed", AS_OF, distance_m=12000, moving_time_s=3510, effort_score=141.0,
                 effort="hard", avg_hr=165.0, hr_drift=3.1, structure="intervals",
                 interval_structure={"work_segments": [{"distance_m": 800}] * 6}),
    ]


def _build(checkins=None):
    facts = _baseline_history() + _recent_facts()
    return build_recent_weeks(facts, checkins or {}, AS_OF)


def test_rolling_7d_totals_and_by_type():
    rw = _build()
    roll = rw.rolling_7d
    # Trailing 7 days = Fri10 + Sat11(walk) + Sun12 + Tue14 + Wed15.
    assert roll.totals.all.sessions == 5
    assert roll.totals.all.distance_km == pytest.approx(60.7)
    assert roll.totals.all.load == 618
    by_type = {t.type: t for t in roll.totals.by_type}
    assert by_type["Run"].sessions == 4
    assert by_type["Run"].distance_km == pytest.approx(56.2)
    assert by_type["Run"].load == 598
    assert by_type["Run"].share_pct == pytest.approx(80.0)
    assert by_type["Walk"].sessions == 1
    assert by_type["Walk"].share_pct == pytest.approx(20.0)


def test_rolling_dated_bounds():
    rw = _build()
    assert rw.rolling_7d.start.weekday == "Thu"
    assert rw.rolling_7d.start.date == "09-07-26"
    assert rw.rolling_7d.end.weekday == "Wed"
    assert rw.rolling_7d.end.date == "15-07-26"


def test_this_week_is_partial_and_carries_no_verdict():
    rw = _build()
    tw = rw.this_week
    assert tw.complete is False
    assert tw.days_elapsed == 3
    assert tw.start.date == "13-07-26"
    assert tw.end.date == "15-07-26"
    # The #653 fix: a partial calendar week has NO fudged vs-typical verdict.
    assert tw.vs_your_typical is None
    # Mon is a rest day, Tue + Wed are active.
    assert len(tw.days) == 3
    assert tw.days[0].weekday == "Mon" and tw.days[0].rest is True
    assert tw.days[0].day_totals is None
    assert tw.days[1].weekday == "Tue" and len(tw.days[1].activities) == 1
    assert tw.days[1].day_totals is not None
    assert tw.week_totals.all.sessions == 2
    assert tw.week_totals.all.distance_km == pytest.approx(20.2)


def test_last_week_is_complete_and_carries_a_verdict():
    rw = _build()
    lw = rw.last_week
    assert lw.complete is True
    assert lw.days_elapsed == 7
    assert len(lw.days) == 7
    assert lw.start.date == "06-07-26" and lw.end.date == "12-07-26"
    assert lw.week_totals.all.sessions == 5
    assert lw.week_totals.all.distance_km == pytest.approx(56.5)
    # A complete week gets the vs-typical verdict over all four metrics.
    assert lw.vs_your_typical is not None
    assert lw.vs_your_typical.distance.direction in ("up", "in_line", "down")


def test_per_activity_shape_and_felt_signals():
    checkins = {
        "t_wed": CheckInFacts(rpe=5, pain_score=None, notes=None),
        "l_fri": CheckInFacts(rpe=None, pain_score=2, notes="calf tight late"),
    }
    rw = _build(checkins)
    wed = rw.this_week.days[2].activities[0]  # Wed run
    assert wed.type == "Run"
    assert wed.intensity == "hard"
    assert wed.avg_hr == 165.0
    assert wed.rpe == 5
    assert wed.hr_drift == pytest.approx(3.1)
    assert wed.load == 141
    assert wed.structure == "intervals"
    assert wed.shape == "6x800m"
    # No pain/notes check-in for this run -> those stay None (dropped from serialization).
    assert wed.pain is None and wed.notes is None


def test_non_run_and_indoor_fields_stay_none():
    rw = _build()
    walk = next(
        a for d in rw.last_week.days for a in d.activities if a.type == "Walk"
    )
    # A walk carries no running structure / rep shape / long-run verdict / intensity.
    assert walk.structure is None
    assert walk.shape is None
    assert walk.long_run is None
    assert walk.intensity is None


def test_long_run_marker_and_interval_shape():
    rw = _build()
    sun = next(a for d in rw.last_week.days for a in d.activities if a.type == "Run" and a.long_run)
    assert sun.long_run is True
    fri = next(
        a for d in rw.last_week.days for a in d.activities if a.shape is not None
    )
    assert fri.shape == "5x1000m"


def test_has_baseline_true_with_history():
    rw = _build()
    assert rw.has_baseline is True
    assert rw.rolling_7d.vs_your_typical is not None


def test_thin_history_abstains_from_verdict():
    # Only the two most-recent runs, no baseline history at all.
    facts = [
        FakeFact("t_tue", date(2026, 7, 14), effort_score=72.0),
        FakeFact("t_wed", AS_OF, effort_score=141.0),
    ]
    rw = build_recent_weeks(facts, {}, AS_OF)
    assert rw.has_baseline is False
    assert rw.rolling_7d.vs_your_typical is None
    assert rw.last_week.vs_your_typical is None
    # The structure lane still renders (days + totals), it just carries no verdict.
    assert rw.this_week.week_totals.all.sessions == 2


def test_notes_are_bounded():
    long_note = "x" * 500
    checkins = {"t_wed": CheckInFacts(rpe=None, pain_score=None, notes=long_note)}
    rw = _build(checkins)
    wed = rw.this_week.days[2].activities[0]
    assert wed.notes is not None and len(wed.notes) <= 200


def test_recorded_laps_source_carried_on_recent_weeks():
    """#712: a past session whose interval structure came from the runner's own recorded
    laps carries source=='recorded_laps', so a later report knows not to advise the lap
    button on it. Mirrors the subject-run `metrics.interval_structure.source` reading."""
    lap_run = FakeFact(
        "lap_run", date(2026, 7, 14), distance_m=8000, moving_time_s=2400,
        effort_score=120.0, effort="hard", structure="intervals",
        interval_structure={
            "source": "recorded_laps",
            "work_segments": [{"distance_m": 400}] * 8,
        },
    )
    facts = _baseline_history() + [lap_run]
    rw = build_recent_weeks(facts, {}, AS_OF)
    act = next(a for d in rw.this_week.days for a in d.activities if a.type == "Run")
    assert act.source == "recorded_laps"
    # It survives the section's null-strip serialization (byte present when it applies).
    from app.services.coach.signal_registry import strip_nulls_in_place
    dumped = act.model_dump()
    strip_nulls_in_place(dumped)
    assert dumped.get("source") == "recorded_laps"


def test_stream_detected_and_continuous_runs_have_no_source_marker():
    """A stream-detected interval run (interval_structure without a `source` key) and a
    continuous run (no interval_structure) both carry source is None, so it is dropped
    from serialization — byte-stable when absent. The shared fixture's l_fri/t_wed carry
    interval_structure with NO source, and the walk is a non-run."""
    from app.services.coach.signal_registry import strip_nulls_in_place
    rw = _build()
    for d in list(rw.last_week.days) + list(rw.this_week.days):
        for a in d.activities:
            assert a.source is None
            dumped = a.model_dump()
            strip_nulls_in_place(dumped)
            assert "source" not in dumped
