"""M2 — the messy-user replay corpus (eval-validated, REAL LLM).

The judgment-quality tier (G7): these run the REAL Haiku writer over deliberately
messy synthetic histories and assert the design's behaviour — no fixated verdict,
a buried safety fact held, a switched goal superseded. They are `integration`-marked
so they are EXCLUDED from `make backend-test` (real-LLM judgment is a tracked
scorecard, never a flaky merge gate); run them with `-m integration` and a live
`ANTHROPIC_API_KEY`, and re-run on real data in M5.

The structural halves of these guarantees (a single signal never hardens, anti-echo
input construction) are pinned deterministically in test_memory_update.py and
test_memory_writer_graduation.py and DO gate CI.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models import Activity, CheckIn, CoachChatMessage, User
from app.schemas.coach_memory import RunnerMemoryProfile
from app.services.coach.memory_update import update_memory

pytestmark = pytest.mark.integration

_VERDICT_WORDS = ("ignore", "doesn't follow", "does not follow", "fails to", "fixate", "won't listen", "refuses")


def _skip_if_no_key():
    if not settings.ANTHROPIC_API_KEY:
        pytest.skip("no ANTHROPIC_API_KEY — real-LLM replay corpus not runnable")


def _require_profile(row):
    """The writer degrades to None on any LLM failure (no key, no credits, transport
    error). When that happens there is nothing to evaluate, so skip rather than fail
    — these judgment cases assert behaviour, not availability."""
    if row is None:
        pytest.skip("writer produced no profile (LLM unavailable / no credits) — cannot evaluate")
    return row


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"replay_{uid}@example.com"))
    db.flush()
    return uid


def _run_with_note(db, uid, when, note):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=when,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    if note:
        db.add(CheckIn(id=uuid.uuid4(), activity_id=a.id, notes=note))
    db.flush()
    return a


def _all_lines(profile: RunnerMemoryProfile) -> str:
    return " ".join(
        " ".join(getattr(profile, f)) for f in
        ("who_you_are", "limits_and_constraints", "goals_and_plans", "what_works_for_you", "lately")
    ).lower()


def test_incident_no_behavioral_verdict_from_neutral_history(db):
    """The original incident: a normal history must not produce an 'ignores easy
    guidance'-style fixation. Memory holds no behavioral verdicts (G1)."""
    _skip_if_no_key()
    uid = _user(db)
    base = datetime(2026, 3, 1, 7, 0, tzinfo=timezone.utc)
    for i, note in enumerate([
        "Felt good today, comfortable pace.",
        "Legs a little tired but fine.",
        "Easy one, kept it relaxed.",
        "Solid run, nothing notable.",
    ]):
        _run_with_note(db, uid, base + timedelta(days=i * 3), note)

    row = _require_profile(asyncio.run(update_memory(db, uid)))
    text = _all_lines(RunnerMemoryProfile.model_validate(row.profile))
    for word in _VERDICT_WORDS:
        assert word not in text, f"profile contains a behavioral verdict: {word!r}"


def test_safety_niggle_mentioned_once_is_held(db):
    """A safety-relevant fact buried in noise is HELD (limits), not lost — but not
    hardened to a firm diagnosis."""
    _skip_if_no_key()
    uid = _user(db)
    base = datetime(2026, 4, 1, 7, 0, tzinfo=timezone.utc)
    _run_with_note(db, uid, base, "Beautiful morning, felt strong, my left knee was a little sore near the end though.")
    _run_with_note(db, uid, base + timedelta(days=3), "Good session, weather was great, legs felt springy.")

    row = _require_profile(asyncio.run(update_memory(db, uid)))
    profile = RunnerMemoryProfile.model_validate(row.profile)
    limits = " ".join(profile.limits_and_constraints).lower()
    assert "knee" in limits, f"the once-mentioned knee niggle was lost: {profile.limits_and_constraints}"


def test_switched_goal_supersedes_the_old_one(db):
    """Goal switching: the old goal retired, the new active — only the new graduates
    to goals_and_plans (D3 supersede-on-newer)."""
    _skip_if_no_key()
    uid = _user(db)
    base = datetime(2026, 2, 1, 7, 0, tzinfo=timezone.utc)
    _run_with_note(db, uid, base, "Building toward the Berlin marathon in September.")
    _run_with_note(db, uid, base + timedelta(days=5), "Marathon training going well, Berlin is the focus.")
    _run_with_note(db, uid, base + timedelta(days=40), "Actually I've changed my mind — dropping the marathon, focusing on a fast 5k now.")
    _run_with_note(db, uid, base + timedelta(days=45), "The 5k is the goal now, doing more speed work.")

    row = _require_profile(asyncio.run(update_memory(db, uid)))
    goals = " ".join(RunnerMemoryProfile.model_validate(row.profile).goals_and_plans).lower()
    assert "5k" in goals or "5 k" in goals, f"new goal missing: {goals!r}"
    assert "marathon" not in goals and "berlin" not in goals, f"superseded goal survived: {goals!r}"


# --------------------------------------------------------------------------- #
# #657 — the coach+runner dialogue as context: a coach suggestion the runner only
# questioned must not become a durable plan; a committed plan (incl. an elliptical
# "yeah, do that") must be captured. Replayed from the 2026-07-09 review incident.
# --------------------------------------------------------------------------- #
def _thread(db, uid, when, turns):
    """One activity with a coach+runner chat thread. `turns` is [(role, content)];
    created_at is spaced so the dialogue interleaves in order."""
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=when,
        distance_m=8000, moving_time_s=2700, elapsed_time_s=2800,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    for i, (role, content) in enumerate(turns):
        db.add(CoachChatMessage(
            id=uuid.uuid4(), activity_id=a.id, role=role, content=content,
            created_at=when + timedelta(minutes=i),
        ))
    db.flush()
    return a


def test_657_coach_suggestion_the_runner_questioned_does_not_become_a_plan(db):
    """The phantom: the coach proposed 1k reps, the runner only questioned it and
    then committed to a 7x400m session. Durable plans must carry the 7x400 the runner
    committed to, and must NOT assert a '1k rep' plan the runner never made."""
    _skip_if_no_key()
    uid = _user(db)
    base = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    _thread(db, uid, base, [
        ("user", "I'm gonna do an interval session next week, what reps would you recommend for a half marathon?"),
        ("assistant", "A classic starting point is 6 x 1km at 10k effort with 90s recovery."),
        ("user", "6k total is too much volume, I'm targeting 20km for the week."),
        ("assistant", "Then 4 x 1km fits neatly — about 6-7km total with warm-up and cool-down."),
        ("user", "Why should the reps be 1k?"),
        ("assistant", "It's not a hard rule — 4 x 800m could actually be a smarter entry point."),
    ])
    _thread(db, uid, base + timedelta(days=8), [
        ("user", "I plan to do 7x 400m reps with 90sec rest in between. 1km warm up, .5k cool down."),
        ("assistant", "Love this plan — well structured. Target around 3:50-4:00/km on the reps."),
    ])

    profile = RunnerMemoryProfile.model_validate(_require_profile(asyncio.run(update_memory(db, uid))).profile)
    goals = " ".join(profile.goals_and_plans).lower()
    # The committed session is captured (single commitment graduates at the bar-1 plan bar).
    assert "400" in goals, f"the committed 7x400 session was not captured: {profile.goals_and_plans}"
    # The coach's 1k idea, which the runner only questioned, is NOT a durable REP plan.
    # (Guard against the phantom's rep structure, not an incidental "1km warm-up".)
    phantom = ("1k rep", "1km rep", "1000m rep", "1 k rep", "1k interval", "1km interval")
    assert not any(p in goals for p in phantom), f"the phantom 1k-rep plan came back: {profile.goals_and_plans}"


def test_657_elliptical_assent_becomes_a_committed_plan(db):
    """A plan-by-assent: the content lives in the coach's proposal and the runner just
    says 'yeah'. With the dialogue in view the writer should capture it as a settled
    plan (in goals_and_plans or as a settled lately thread), not lose it."""
    _skip_if_no_key()
    uid = _user(db)
    base = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    _thread(db, uid, base, [
        ("assistant", "Want to do 4x1km at threshold on Tuesday, with an easy day either side?"),
        ("user", "yeah lets do that"),
    ])

    profile = RunnerMemoryProfile.model_validate(_require_profile(asyncio.run(update_memory(db, uid))).profile)
    settled = (" ".join(profile.goals_and_plans) + " " + " ".join(profile.lately)).lower()
    assert "1km" in settled or "4x1" in settled or "tuesday" in settled, (
        f"the elliptical commitment was not captured anywhere: goals={profile.goals_and_plans} lately={profile.lately}"
    )
