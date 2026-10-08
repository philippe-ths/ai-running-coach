"""#1064: week frames, what the season asks of each week as arithmetic.

Pure functions over a season, the goals, the runner's own facts and today's date,
so every test builds its inputs and reads the frame; no model, no database.

All data is synthetic test setup (exercises code paths; represents no real runner).
"""

from datetime import date, timedelta

import pytest

from app.schemas.season import SeasonPlan
from app.services.schedule.frames import (
    build_frames,
    describe_frame,
    share_of_week_left,
    walking_floor_m,
)
from tests._week_fixtures_1064 import (
    SHARES, THIS_WEEK, TODAY, challenge_season, fact, goal_row, owner_facts,
)

WEEK_2 = THIS_WEEK + timedelta(weeks=1)


def frames_for(season, goals, facts=None, weeks=12, today=TODAY):
    return build_frames(
        season=season, goals=goals, facts=owner_facts() if facts is None else facts,
        starts_on=0, today=today, horizon_weeks=weeks,
    )


def season_with(phases, goals=()):
    return SeasonPlan.model_validate({"summary": "s", "goals": list(goals), "phases": phases})


# --- interpolation -------------------------------------------------------------


def test_a_phases_targets_are_where_it_ends_and_each_week_steps_towards_them():
    # The runner's usual week is 9.2 h; the phase ends at 13.2 h on the Sunday of
    # the fourth week, so each week climbs a quarter of the 4 h from the Sunday
    # before the phase began.
    season = season_with([
        {"kind": "build", "start": "2026-10-05", "end": "2026-11-01", "weekly_hours": 13.2},
        {"kind": "base", "start": "2026-11-02", "end": "2026-11-29"},
    ])

    frames = frames_for(season, [])

    hours = [f.targets.weekly_hours_s / 3600 for f in frames[:5]]
    # Anchor: Sunday 4 Oct at 9.2 h. Week 1 is evaluated at its Sunday (11 Oct),
    # 7 of 28 days along the line to 13.2 h.
    assert hours[0] == pytest.approx(9.2 + 4.0 * 7 / 28, abs=0.01)
    assert hours[1] == pytest.approx(9.2 + 4.0 * 14 / 28, abs=0.01)
    assert hours[3] == pytest.approx(13.2, abs=0.01)
    # Past the last knot the value holds.
    assert hours[4] == pytest.approx(13.2, abs=0.01)


def test_the_first_phase_starts_from_the_runners_current_level_not_from_zero():
    season = season_with([
        {"kind": "build", "start": "2026-10-05", "end": "2026-10-25", "run_km": 40},
    ])

    first = frames_for(season, [])[0]

    # Their usual running is 28 km; the first week is a step towards 40, never 0.
    assert 28_000 < first.targets.run_m < 40_000


def test_a_target_no_phase_states_is_unknown_not_the_current_level_held_flat():
    season = season_with([
        {"kind": "build", "start": "2026-10-05", "end": "2026-10-25", "run_km": 40},
    ])

    frame = frames_for(season, [])[0]

    assert frame.targets.long_run_m is None
    assert frame.targets.weekly_hours_s is None


def test_a_later_phase_climbs_from_where_the_earlier_one_ended():
    season = season_with([
        {"kind": "build", "start": "2026-10-05", "end": "2026-10-18", "weekly_hours": 12},
        {"kind": "taper", "start": "2026-10-19", "end": "2026-11-01", "weekly_hours": 8},
    ])

    frames = frames_for(season, [])

    assert frames[1].targets.weekly_hours_s / 3600 == pytest.approx(12.0, abs=0.01)
    assert frames[2].targets.weekly_hours_s / 3600 == pytest.approx(10.0, abs=0.01)
    assert frames[3].targets.weekly_hours_s / 3600 == pytest.approx(8.0, abs=0.01)


def test_a_week_belongs_to_the_phase_covering_most_of_it():
    # Taper takes six days of the week of 2 Nov, the race phase one.
    race = goal_row("Chatham 10k", race_date=date(2026, 11, 8), distance_m=10_000)
    season = season_with(
        [
            {"kind": "taper", "start": "2026-10-05", "end": "2026-11-07"},
            {"kind": "race", "start": "2026-11-08", "end": "2026-11-08",
             "goal_id": str(race.id)},
            {"kind": "recover", "start": "2026-11-09", "end": "2026-11-22"},
        ],
        goals=[{"goal_id": str(race.id), "kind": "race", "success": "PB",
                "date": "2026-11-08", "approach": "go"}],
    )

    frames = {f.week_start: f for f in frames_for(season, [race])}

    assert frames[date(2026, 11, 2)].phase.kind == "taper"
    assert frames[date(2026, 11, 9)].phase.kind == "recover"


def test_a_week_past_the_end_of_the_timeline_has_no_phase_and_no_targets():
    season = season_with([
        {"kind": "build", "start": "2026-10-05", "end": "2026-10-18", "weekly_hours": 12},
    ])

    late = frames_for(season, [])[5]

    assert late.phase is None and late.targets.weekly_hours_s is None


# --- the goals a week holds -------------------------------------------------------


def test_a_dated_goal_lands_in_its_own_week_with_the_runners_facts_about_it():
    race = goal_row("Chatham 10k", race_date=date(2026, 11, 8), distance_m=10_000,
                    booked=True)
    someday = goal_row("Backyard ultra")
    season = season_with(
        [{"kind": "build", "start": "2026-10-05", "end": "2026-12-27"}],
        goals=[
            {"goal_id": str(race.id), "kind": "race", "success": "PB",
             "date": "2026-11-08", "approach": "go"},
            {"goal_id": str(someday.id), "kind": "someday", "success": "one day",
             "approach": "later"},
        ],
    )

    frames = {f.week_start: f for f in frames_for(season, [race, someday])}

    holding = frames[date(2026, 11, 2)]
    assert [(g.name, g.day, g.distance_m, g.booked) for g in holding.dated] == [
        ("Chatham 10k", date(2026, 11, 8), 10_000, True)
    ]
    assert all(not f.dated for w, f in frames.items() if w != date(2026, 11, 2))


def test_a_recommended_date_counts_as_a_day_the_week_holds():
    unbooked = goal_row("First marathon", race_date=None)
    season = season_with(
        [{"kind": "build", "start": "2026-10-05", "end": "2026-12-27"}],
        goals=[{"goal_id": str(unbooked.id), "kind": "finish", "success": "run well",
                "date": "2026-12-06", "approach": "build"}],
    )

    frames = {f.week_start: f for f in frames_for(season, [unbooked])}

    (goal,) = frames[date(2026, 11, 30)].dated
    assert (goal.day, goal.booked) == (date(2026, 12, 6), False)


def test_a_challenge_is_week_n_of_N_in_every_week_it_covers_and_nowhere_else():
    g = goal_row("10h a week")
    frames = frames_for(challenge_season(g, start=date(2026, 10, 12), weeks=4), [g])

    covering = [(f.week_start, f.challenges[0].index) for f in frames if f.challenges]

    assert covering == [(date(2026, 10, 12) + timedelta(weeks=n), n + 1) for n in range(4)]
    assert frames[1].challenges[0].weeks == 4
    assert frames[1].challenges[0].threshold == 36_000
    assert not frames[0].challenges and not frames[5].challenges


# --- what the runner does, and the floors and ceilings ------------------------------


def test_the_frame_carries_the_usual_week_zone_shares_and_ceilings():
    g = goal_row("10h a week")
    frame = frames_for(challenge_season(g), [g])[0]

    assert frame.usual_time_s("walk") / 3600 == pytest.approx(4.9, abs=0.01)
    assert frame.usual_distance_m("walk") == pytest.approx(30_000, abs=1)
    for discipline, share in SHARES.items():
        assert frame.share(2, discipline) == pytest.approx(share, abs=0.001)
    assert frame.hours_ceiling_s / 3600 == pytest.approx(2 * 9.2, abs=0.05)
    assert frame.run_ceiling_m == pytest.approx(2 * 28_000, abs=100)


def test_the_walking_floor_is_ninety_percent_of_usual_when_walking_is_material():
    assert walking_floor_m(30_000) == pytest.approx(27_000)
    assert walking_floor_m(4_999) is None
    assert walking_floor_m(None) is None


def test_a_runner_who_barely_walks_has_no_walking_floor():
    facts = [f for f in owner_facts() if f.activity_type != "Walk"]
    facts += [fact(THIS_WEEK - timedelta(weeks=n), kind="Walk", seconds=1800,
                   distance_m=2000) for n in range(1, 13)]

    assert frames_for(None, [], facts=facts)[0].walking_floor_m is None


def test_the_walking_floor_is_prorated_for_the_part_of_this_week_that_is_left():
    # Thursday of a Monday week: Thu..Sun is four of seven days.
    assert share_of_week_left(THIS_WEEK, TODAY) == pytest.approx(4 / 7)
    assert share_of_week_left(WEEK_2, TODAY) == 1.0
    assert share_of_week_left(THIS_WEEK, THIS_WEEK) == 1.0


def test_the_current_week_carries_what_was_measured_before_today_and_only_that():
    g = goal_row("10h a week")
    facts = owner_facts() + [
        fact(THIS_WEEK, kind="Run", seconds=3600, distance_m=10_000,
             zones={"Z1": 0, "Z2": 3600, "Z3": 0, "Z4": 0, "Z5": 0}),
        # Today's run is neither done nor planned as far as the frame knows: it
        # is left to the plan, so a session today is not counted twice.
        fact(TODAY, kind="Run", seconds=3600, distance_m=10_000,
             zones={"Z1": 0, "Z2": 3600, "Z3": 0, "Z4": 0, "Z5": 0}),
        # A run with no heart-rate data adds time but no zone time: measured only.
        fact(THIS_WEEK + timedelta(days=1), kind="Run", seconds=1800, distance_m=5_000),
    ]

    frames = frames_for(challenge_season(g), [g], facts=facts)

    done = frames[0].done
    assert done.time_s["run"] == 5400
    assert done.zone_time_s(2) == 3600
    assert frames[1].done is None


# --- said in words ------------------------------------------------------------------


def test_a_frame_is_stated_plainly_for_the_prompt():
    g = goal_row("10h a week")
    race = goal_row("Chatham 10k", race_date=date(2026, 11, 8), distance_m=10_000,
                    booked=True)
    season = challenge_season(g, start=date(2026, 11, 2), weeks=3, race=race)

    frames = {f.week_start: f for f in frames_for(season, [g, race])}
    text = "\n".join(describe_frame(frames[date(2026, 11, 2)]))

    assert 'Challenge "10h a week", week 1 of 3' in text
    assert "10.0 h in zone 2 or above" in text
    assert "run 98%" in text and "walk 27%" in text
    assert "Chatham 10k is on Sun 2026-11-08, 10 km (booked)" in text
    assert "race week" in text
    assert "at least 27.0 km of walking" in text
    this_week = "\n".join(describe_frame(frames_for(season, [g, race])[0]))
    assert "4 of 7 days left" in this_week


def test_the_walking_floor_is_stated_rounded_up_so_the_stated_figure_always_passes():
    g = goal_row("10h a week")
    frame = frames_for(challenge_season(g), [g])[1]
    frame.walking_floor_m = 26_140.0  # a floor that rounds DOWN to 26.1 but needs 26.2 to pass

    text = "\n".join(describe_frame(frame))

    # "26.1 km" would fail the check at 26.1 km against a floor of 26.14.
    assert "at least 26.2 km of walking" in text
