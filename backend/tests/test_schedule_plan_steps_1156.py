"""#1156: the day card shows a session's plan, worded by the server.

The structures below are the owner's real sessions for the week of 26 Oct 2026,
read from production: an interval session and a tempo.
"""

from app.schemas.schedule import PlannedSessionRead
from app.services.schedule.planned_distance import plan_steps, structured_distance_m

INTERVALS = {
    "reps_planned": 6,
    "rep_distance_m": 1000.0,
    "rest_s": 90.0,
    "warmup_distance_m": 1500.0,
    "cooldown_distance_m": 1500.0,
}
TEMPO = {"warmup_distance_m": 1500.0, "cooldown_distance_m": 1500.0}


class _Session:
    def __init__(self, structure):
        self.structure = structure


def test_an_interval_session_reads_warm_up_reps_and_cool_down_in_order():
    assert plan_steps(_Session(INTERVALS)) == [
        "Warm-up 1.5 km",
        "6 × 1 km · 90 s recovery",
        "Cool-down 1.5 km",
    ]


def test_a_tempo_with_no_reps_shows_only_its_edges():
    assert plan_steps(_Session(TEMPO)) == ["Warm-up 1.5 km", "Cool-down 1.5 km"]


def test_short_reps_and_whole_minute_rests_read_naturally():
    s = _Session({"reps_planned": 8, "rep_distance_m": 400.0, "rest_s": 120.0})
    assert plan_steps(s) == ["8 × 400 m · 2 min recovery"]


def test_reps_with_no_distance_say_only_the_count():
    assert plan_steps(_Session({"reps_planned": 6})) == ["6 reps"]


def test_a_session_with_no_structure_has_no_plan():
    assert plan_steps(_Session(None)) == []
    assert plan_steps(_Session({})) == []


def test_a_stored_float_or_text_rep_count_reads_as_a_whole_number():
    assert plan_steps(_Session({"reps_planned": 6.0, "rep_distance_m": 1000.0})) == ["6 × 1 km"]
    assert plan_steps(_Session({"reps_planned": "0", "rep_distance_m": 1000.0})) == []


def test_the_distances_shown_are_the_ones_the_total_adds_up():
    """The card's parts and the summed structure are one set of numbers (#887):
    read back out of the words, they add to `structured_distance_m`."""
    import re

    shown = " ".join(plan_steps(_Session(INTERVALS)))
    km = [float(x) for x in re.findall(r"([\d.]+) km", shown)]
    reps = int(re.search(r"(\d+) ×", shown).group(1))
    warmup, rep, cooldown = km
    assert (warmup + reps * rep + cooldown) * 1000 == structured_distance_m(_Session(INTERVALS))


def test_the_week_read_carries_the_plan_to_the_client():
    from datetime import date
    from uuid import uuid4

    read = PlannedSessionRead(
        id=uuid4(),
        window_start=date(2026, 10, 27),
        window_end=date(2026, 10, 27),
        placement="pinned",
        intent="quality",
        discipline="run",
        commitment="committed",
        status="upcoming",
        title="Intervals 6 x 1 km",
        structure=INTERVALS,
    )
    assert read.model_dump()["plan_steps"][1] == "6 × 1 km · 90 s recovery"
