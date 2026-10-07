"""#1032: `this_run.notable` — why this activity stands out for this runner.

The named guard this section owes: `this_run.notable` is a grouped-lineage-only
additive feature, so the structural pack guards built under
`fullest_message_prompt_id` do not cover it.

The load-bearing claims:

**Never a fabricated previous best.** Our store holds best efforts only for activities
ingested in detail, so an older, faster effort can exist that we cannot see. A
previous best and a margin are stated only when every earlier run long enough to
contain the distance is covered; otherwise the section says the margin is unknown.

**Records neither spam nor vanish.** "More than 10% past the most in the past year,
among at least 10 of that kind of activity" was calibrated on the owner's real
history (see `coach/notable.py`). A build's steady progression must not fire it.

**Byte-stability.** The section reaches only a notable-aware prompt.

All row data is synthetic test setup (trust level 5), in the real shapes.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.core.config import settings
from app.models import Activity, User
from app.models.goal_race import GoalRace
from app.services.best_efforts import Effort
from app.services.coach import notable

NOTABLE_PROMPT = "coach_message_lean_grouped_v12"
PRIOR_PROMPT = "coach_message_lean_grouped_v11"

DAY = date(2026, 9, 27)


def _effort(name, distance, seconds, rank=None):
    return Effort(name=name, distance_m=distance, elapsed_time_s=seconds, pr_rank=rank)


def _run(on, distance, seconds=3000, has_efforts=True):
    return notable.PriorRun(on=on, distance_m=distance, moving_time_s=seconds, has_efforts=has_efforts)


# --- best efforts ------------------------------------------------------------


def test_a_previous_best_and_margin_are_given_when_our_records_are_complete():
    found = [_effort("10K", 10000, 2809, rank=1)]
    earlier_day = DAY - timedelta(days=30)
    prior = [notable.PriorEffort(_effort("10K", 10000, 2900), earlier_day)]
    out = notable.build_best_efforts(found, prior, [_run(earlier_day, 10500)])

    assert out[0].strava_rank == "fastest ever"
    assert out[0].best_we_hold_before == "48:20 (28 Aug 2026)"
    assert out[0].margin == "1:31 faster than that"
    assert out[0].note is None


def test_an_uncovered_earlier_run_makes_the_margin_unknown_never_invented():
    """The owner's case: their previous half predates our best-effort records, so the
    stored 'fastest earlier half' would be a fabrication. The note says so and offers
    the whole earlier run, labelled as a whole-run time."""
    found = [_effort("Half-Marathon", 21097, 6130, rank=1)]
    covered = DAY - timedelta(days=40)
    uncovered = DAY - timedelta(days=200)
    prior_runs = [_run(covered, 12000), _run(uncovered, 21201, seconds=6493, has_efforts=False)]
    out = notable.build_best_efforts(found, [], prior_runs)

    assert out[0].best_we_hold_before is None and out[0].margin is None
    note = out[0].note
    assert note.startswith(notable.MARGIN_UNKNOWN), note
    assert "21.2 km in 1:48:13" in note and "whole-run moving time" in note, note


def test_no_earlier_effort_held_is_never_called_a_first_time():
    """Strava's rank can prove earlier efforts exist that predate our records, so an
    empty store says only that we hold none."""
    out = notable.build_best_efforts(
        [_effort("Half-Marathon", 21097, 6130, rank=1)], [], [_run(DAY - timedelta(days=9), 15000)]
    )
    assert out[0].note == notable.NONE_HELD
    assert "first" not in out[0].note.lower()


def test_a_race_lists_its_own_distance_first_even_unranked():
    found = [
        _effort("1 mile", 1609, 440, rank=1),
        _effort("10K", 10000, 2809, rank=1),
        _effort("Half-Marathon", 21097, 6500),  # not a PB, but it is the race
    ]
    out = notable.build_best_efforts(found, [], [], race_distance_m=21097.5)
    assert [e.distance for e in out] == ["Half-Marathon", "10K", "1 mile"]
    assert out[0].strava_rank is None


def test_only_a_fastest_ever_at_a_raced_distance_is_notable_on_its_own():
    """Strava ranks segments down to 400 m and ranks 2nd and 3rd too; counting those
    fired on 28% of a real runner's build. A short segment or a 2nd-fastest is not news."""
    found = [
        _effort("400m", 400, 90, rank=1),
        _effort("1K", 1000, 250, rank=1),
        _effort("5K", 5000, 1400, rank=2),
        _effort("10K", 10000, 2809, rank=1),
    ]
    out = notable.build_best_efforts(found, [], [])
    assert [e.distance for e in out] == ["10K"]


def test_a_run_with_no_ranked_effort_and_no_race_has_no_best_efforts():
    assert notable.build_best_efforts([_effort("5K", 5000, 1600)], [], []) is None


# --- records -----------------------------------------------------------------


def _fact(days_ago, distance=5000, seconds=3600, climb=100.0):
    return SimpleNamespace(
        local_date=DAY - timedelta(days=days_ago),
        distance_m=distance,
        moving_time_s=seconds,
        elev_gain_m=climb,
    )


def _history(n=12, **kw):
    return [_fact(10 + i * 10, **kw) for i in range(n)]


def test_a_climb_far_beyond_the_past_year_is_a_record():
    this = _fact(0, climb=1271.0, seconds=15840)
    out = notable.build_records("walk", this, DAY, _history(climb=270.0, seconds=5340))
    climb = next(r for r in out if r.measure == "climb")
    assert climb.value == "1271 m"
    assert climb.times_previous == 4.7
    assert climb.beats_all_we_hold is True
    assert "walks and hikes over the past 12 months" in climb.compared_with


def test_a_builds_steady_progression_is_not_a_record():
    """+5% on last week's long run is the plan working, not news."""
    this = _fact(0, distance=21000, seconds=7000, climb=100.0)
    history = _history(distance=20000, seconds=6800, climb=100.0)
    assert notable.build_records("run", this, DAY, history) is None


def test_too_little_history_sets_no_record():
    this = _fact(0, climb=2000.0)
    assert notable.build_records("walk", this, DAY, _history(n=9, climb=100.0)) is None


def test_history_older_than_a_year_does_not_count_toward_the_window():
    this = _fact(0, climb=2000.0)
    old = [_fact(400 + i, climb=100.0) for i in range(12)]
    assert notable.build_records("walk", this, DAY, old) is None


def test_an_activity_kind_without_meaningful_records_sets_none():
    """A padel match's GPS 'distance' is not a record anyone set."""
    assert notable.build_records("other", _fact(0, distance=99999), DAY, _history()) is None


def test_nothing_notable_means_no_section():
    assert notable.build_notable(race=None, best_efforts=None, records=None) is None


# --- through the store -------------------------------------------------------


def _seed_race_day(db):
    user = User(email=f"notable-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(GoalRace(user_id=user.id, name="Autumn Half", race_date=DAY,
                    distance_m=21097.5, priority="A"))
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc),
        start_date_local=datetime(2026, 9, 27, 9, 0),
        type="Run",
        name="Morning Run",
        distance_m=21340,
        moving_time_s=6274,
        elapsed_time_s=6309,
        elev_gain_m=148.0,
        raw_summary={"best_efforts": [
            {"name": "10K", "distance": 10000, "elapsed_time": 2809, "pr_rank": 1},
            {"name": "Half-Marathon", "distance": 21097, "elapsed_time": 6130, "pr_rank": 1},
        ]},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def test_the_goal_race_reaches_the_coach_as_a_race_with_its_best_efforts(db):
    from app.services.coach import context as ctx

    section = ctx._build_notable_context(db, _seed_race_day(db), None)

    assert section.race.source == "goal_race"
    assert section.race.name == "Autumn Half" and section.race.priority == "A"
    assert "not warning signs" in section.race.reading
    assert [e.distance for e in section.best_efforts] == ["Half-Marathon", "10K"]
    assert section.best_efforts[0].note == notable.NONE_HELD
    assert section.best_efforts_recorded_since == "27 Sep 2026"


def test_the_section_reaches_only_a_notable_aware_prompt():
    from app.services.coach import context as ctx
    from app.services.coach.read_time_signals import will_run

    assert will_run(ctx._NOTABLE_SIGNAL, NOTABLE_PROMPT) is True
    assert will_run(ctx._NOTABLE_SIGNAL, PRIOR_PROMPT) is False


def test_the_kill_switch_drops_the_section(monkeypatch):
    from app.services.coach import context as ctx
    from app.services.coach.read_time_signals import will_run

    monkeypatch.setattr(settings, "COACH_NOTABLE_ENABLED", False)
    assert will_run(ctx._NOTABLE_SIGNAL, NOTABLE_PROMPT) is False


def test_the_pack_under_the_prior_prompt_carries_no_notable_key(db):
    from app.services.coach.context import build_context_pack

    activity = _seed_race_day(db)
    prior = build_context_pack(db, activity, prompt_id=PRIOR_PROMPT).to_grouped_dict()
    current = build_context_pack(db, activity, prompt_id=NOTABLE_PROMPT).to_grouped_dict()

    assert "notable" not in prior["this_run"]
    assert current["this_run"]["notable"]["race"]["source"] == "goal_race"


def test_a_fault_never_costs_the_runner_their_report(db, monkeypatch):
    from app.services.coach import context as ctx

    def _boom(*args, **kwargs):
        raise RuntimeError("store is down")

    monkeypatch.setattr(ctx, "_gather_notable", _boom)
    assert ctx._build_notable_context(db, _seed_race_day(db), None) is None


def test_records_compare_like_with_like_in_the_store(db):
    """Through the store: a big hike is judged against walks and hikes only, so rides
    with far more climb do not hide it, and the past-year window is applied."""
    from app.services.coach import context as ctx

    user = User(email=f"records-{uuid4()}@example.com")
    db.add(user)
    db.commit()

    def add(kind, days_ago, climb, seconds=5400, distance=8000):
        start = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)
        a = Activity(
            user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
            start_date=start, type=kind, name=kind, distance_m=distance,
            moving_time_s=seconds, elapsed_time_s=seconds, elev_gain_m=climb, raw_summary={},
        )
        db.add(a)
        return a

    for i in range(12):
        add("Walk", 10 + i * 20, climb=250.0)
    for i in range(3):
        add("Ride", 5 + i, climb=3000.0, seconds=20000, distance=90000)
    hike = add("Walk", 0, climb=1271.0, seconds=15840, distance=12800)
    db.commit()

    section = ctx._build_notable_context(db, hike, None)
    climb = next(r for r in section.records if r.measure == "climb")
    assert climb.times_previous == 5.1, climb
    assert "walks and hikes" in climb.compared_with and "(12 of them)" in climb.compared_with
