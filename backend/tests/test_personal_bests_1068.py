"""#1068: the coach can look up the runner's personal bests.

Unit tests pin the trust rule in `app.services.personal_bests`; the rest exercise the
`get_personal_bests` tool against the DB and the profile API that holds stated PBs.

Effort payloads follow Strava's DetailedActivity `best_efforts` shape, as the #1032
tests do. The times are test setup (trust level 5), except the half marathon at
6130 s, which is the owner's real 2026-09-27 PB from #1032.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

from app.models import Activity, User, UserProfile
from app.services import personal_bests as pbs
from app.services.best_efforts import Effort
from app.services.coach import query_tools as qt


def _run(on, distance_m, *efforts):
    return pbs.HeldRun(on=on, distance_m=distance_m, efforts=list(efforts))


def _effort(name, seconds, rank=None):
    return Effort(name=name, distance_m=pbs.DISTANCES[name], elapsed_time_s=seconds, pr_rank=rank)


def _only(result, distance):
    found = [p for p in result if p["distance"] == distance]
    assert len(found) == 1, f"expected one {distance} entry, got {result}"
    return found[0]


# --- the trust rule ------------------------------------------------------------


def test_a_rank_one_effort_with_every_later_long_run_covered_is_confirmed():
    runs = [
        _run(date(2026, 9, 27), 21200, _effort("Half-Marathon", 6130, rank=1)),
        _run(date(2026, 10, 4), 22000, _effort("Half-Marathon", 6400)),
    ]
    pb = _only(pbs.personal_bests(runs, []), "Half-Marathon")
    assert (pb["status"], pb["time"], pb["set_on"]) == (pbs.CONFIRMED, "1:42:10", "2026-09-27")


def test_a_later_long_run_held_without_efforts_leaves_the_pb_open():
    runs = [
        _run(date(2026, 9, 27), 21200, _effort("Half-Marathon", 6130, rank=1)),
        _run(date(2026, 10, 4), 22000),
    ]
    pb = _only(pbs.personal_bests(runs, []), "Half-Marathon")
    assert pb["status"] == pbs.MAY_BE_BEATEN
    assert pb["later_runs_without_efforts"] == 1


def test_a_later_run_too_short_for_the_distance_cannot_leave_it_open():
    runs = [
        _run(date(2026, 9, 27), 21200, _effort("Half-Marathon", 6130, rank=1)),
        _run(date(2026, 10, 4), 8000),
    ]
    assert _only(pbs.personal_bests(runs, []), "Half-Marathon")["status"] == pbs.CONFIRMED


def test_a_fastest_held_effort_strava_did_not_rank_first_is_not_the_pb():
    runs = [_run(date(2026, 6, 1), 10100, _effort("10K", 2900, rank=2))]
    pb = _only(pbs.personal_bests(runs, []), "10K")
    assert pb["status"] == pbs.FASTER_EXISTS
    assert pb["reading"].startswith("NOT their PB")


def test_a_stated_pb_faster_than_any_held_effort_is_the_one_shown():
    runs = [_run(date(2026, 6, 1), 10100, _effort("10K", 2900, rank=2))]
    stated = [pbs.StatedPB("10K", 2750, date(2023, 4, 2))]
    pb = _only(pbs.personal_bests(runs, stated), "10K")
    assert (pb["status"], pb["time"], pb["set_on"]) == (pbs.STATED, "45:50", "2023-04-02")


def test_a_derived_pb_faster_than_the_stated_one_replaces_it():
    runs = [_run(date(2026, 9, 27), 21200, _effort("Half-Marathon", 6130, rank=1))]
    stated = [pbs.StatedPB("Half-Marathon", 6400)]
    pb = _only(pbs.personal_bests(runs, stated), "Half-Marathon")
    assert (pb["status"], pb["time"]) == (pbs.CONFIRMED, "1:42:10")


def test_distances_with_nothing_held_or_stated_are_left_out():
    runs = [_run(date(2026, 6, 1), 5100, _effort("5K", 1400, rank=1))]
    assert [p["distance"] for p in pbs.personal_bests(runs, [])] == ["5K"]


# --- the tool, against the DB --------------------------------------------------


def _user(db) -> User:
    u = User(email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.commit()
    return u


def _activity(db, user, *, on, distance_m, best_efforts=None, type="Run"):
    raw = {"best_efforts": best_efforts} if best_efforts is not None else {}
    a = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, datetime.min.time()) + timedelta(hours=9),
        type=type, name="r", distance_m=distance_m, moving_time_s=3000,
        elapsed_time_s=3000, elev_gain_m=10.0, raw_summary=raw,
    )
    db.add(a)
    db.commit()
    return a


HALF_PB = [
    {"name": "10K", "distance": 10000, "elapsed_time": 2809, "pr_rank": 1},
    {"name": "Half-Marathon", "distance": 21097, "elapsed_time": 6130, "pr_rank": 1},
]


def test_the_tool_reads_the_runners_own_efforts_and_stated_pbs(db):
    u = _user(db)
    _activity(db, u, on=date(2026, 9, 27), distance_m=21200, best_efforts=HALF_PB)
    db.add(UserProfile(
        user_id=u.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4,
        stated_pbs=[{"distance": "Marathon", "time_s": 14400, "on": "2019-04-28"}],
    ))
    db.commit()

    out = qt.execute_chat_tool(db, u.id, "get_personal_bests", {})

    assert [(p["distance"], p["status"]) for p in out["personal_bests"]] == [
        ("10K", pbs.CONFIRMED),
        ("Half-Marathon", pbs.CONFIRMED),
        ("Marathon", pbs.STATED),
    ]
    assert out["no_record_at"] == ["1 mile", "5K"]
    assert out["strava_best_efforts_on_record_from"] == "2026-09-27"


def test_the_tool_never_reads_another_runners_efforts(db):
    me, other = _user(db), _user(db)
    _activity(db, other, on=date(2026, 9, 27), distance_m=21200, best_efforts=HALF_PB)

    out = qt.execute_chat_tool(db, me.id, "get_personal_bests", {})

    assert out["personal_bests"] == []


def test_the_trace_names_the_distances_the_coach_saw():
    entry = qt.summarize_tool_call(
        "get_personal_bests", {},
        {"personal_bests": [{"distance": "5K"}, {"distance": "Half-Marathon"}]},
    )
    assert entry == {
        "tool": "get_personal_bests",
        "label": "Looked up your personal bests",
        "detail": "5K, half marathon",
        "count": None,
    }


# --- stated PBs on the profile -------------------------------------------------

_PROFILE = {"goal_type": "general", "experience_level": "intermediate", "weekly_days_available": 4}


def test_stated_pbs_round_trip_with_their_dates(client, db):
    pbs_in = [{"distance": "Marathon", "time_s": 14400, "on": "2019-04-28"}]
    assert client.put("/api/profile", json={**_PROFILE, "stated_pbs": pbs_in}).status_code == 200
    assert client.get("/api/profile").json()["stated_pbs"] == pbs_in


def test_a_stated_time_outside_the_envelope_is_rejected(client, db):
    # 3:30 typed as minutes:seconds for a marathon, a unit slip not a fact.
    body = {**_PROFILE, "stated_pbs": [{"distance": "Marathon", "time_s": 210}]}
    assert client.put("/api/profile", json=body).status_code == 422


def test_two_stated_pbs_at_one_distance_are_rejected(client, db):
    body = {**_PROFILE, "stated_pbs": [
        {"distance": "5K", "time_s": 1500}, {"distance": "5K", "time_s": 1600},
    ]}
    assert client.put("/api/profile", json=body).status_code == 422


def test_a_stated_pb_at_a_distance_we_do_not_track_is_rejected(client, db):
    body = {**_PROFILE, "stated_pbs": [{"distance": "15K", "time_s": 4000}]}
    assert client.put("/api/profile", json=body).status_code == 422
