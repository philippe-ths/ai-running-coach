"""A2b: the retrieval seam (services/coach/retrieval.py).

The seam pulls A2a's stored processed artifacts on demand — the consolidated
stream view (deferred DerivedMetric.stream_view) and prior-exchange digests
(CoachReport.digest) — never the raw store. These tests pin:
  - fetch_stream_view returns the stored view, degrades to None cleanly;
  - fetch_prior_digests prefers the stored A2a digest, falls back to
    re-projecting the report body, and the two are byte-equal (the A2a
    invariant, relocated here from test_report_digest.py and strengthened to
    cover the stored path);
  - the prior-digest selection semantics (exclude self/fallback, newest-first,
    one per activity, capped) match the behaviour the longitudinal builder had.
"""

import uuid
from datetime import datetime, timezone, timedelta

from app.models import Activity, DerivedMetric, User
from app.models.coach_report import CoachReport
from app.schemas.coach_context import PriorReportDigest
from app.services.coach.digest import build_report_digest
from app.services.coach.retrieval import (
    fetch_prior_commitments,
    fetch_prior_digests,
    fetch_stream_view,
)


def _user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"retr_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id, *, day: int, name: str = "Run") -> Activity:
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        start_date=datetime(2026, 5, day, 8, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3000,
        elapsed_time_s=3050,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=40.0,
        average_speed_mps=3.3,
        raw_summary={},
    )
    db.add(a)
    db.flush()
    return a


def _report_body(headline: str = "Solid aerobic run") -> dict:
    return {
        "headline": headline,
        "lead_argument": {"text": "Aerobic durability is holding."},
        "next_steps": [
            {
                "action": "Add a tempo segment",
                "details": "15 min at threshold",
                "why": "Builds the half-marathon engine.",
            }
        ],
        "key_takeaways": [{"text": "Good easy control."}],
    }


def _add_report(
    db, activity, *, body=None, store_digest: bool, is_fallback=False,
    prompt_id="coach_report_v9",
):
    body = body if body is not None else _report_body()
    digest = (
        build_report_digest(body, activity.start_date).model_dump()
        if store_digest and not is_fallback
        else None
    )
    db.add(
        CoachReport(
            id=uuid.uuid4(),
            activity_id=activity.id,
            prompt_id=prompt_id,
            schema_version="1.2",
            report=body,
            meta={},
            context_pack={},
            is_fallback=is_fallback,
            digest=digest,
        )
    )
    db.flush()


# --- fetch_stream_view -----------------------------------------------------


def test_fetch_stream_view_returns_stored_view(db):
    user_id = _user(db)
    activity = _activity(db, user_id, day=1)
    view = {"n_points": 2, "source_n": 4, "time_s": [0, 2], "hr": [150, 152],
            "pace_s_per_km": [303, 300], "grade_pct": [1.0, 1.1], "cadence_spm": [170, 171]}
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=activity.id, effort_score=5.0,
        confidence="high", flags=[], confidence_reasons=[], stream_view=view,
    ))
    db.flush()

    assert fetch_stream_view(db, activity.id) == view


def test_fetch_stream_view_none_when_no_metrics(db):
    user_id = _user(db)
    activity = _activity(db, user_id, day=1)
    assert fetch_stream_view(db, activity.id) is None


def test_fetch_stream_view_none_when_view_absent(db):
    user_id = _user(db)
    activity = _activity(db, user_id, day=1)
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=activity.id, effort_score=5.0,
        confidence="high", flags=[], confidence_reasons=[], stream_view=None,
    ))
    db.flush()
    assert fetch_stream_view(db, activity.id) is None


# --- fetch_prior_digests ---------------------------------------------------


def test_fetch_prior_digests_prefers_stored_digest(db):
    user_id = _user(db)
    prior = _activity(db, user_id, day=1)
    current = _activity(db, user_id, day=5)
    body = _report_body("Stored-path run")
    _add_report(db, prior, body=body, store_digest=True)

    out = fetch_prior_digests(db, current)
    assert out == [build_report_digest(body, prior.start_date)]


def test_fetch_prior_digests_falls_back_to_reprojection(db):
    user_id = _user(db)
    prior = _activity(db, user_id, day=1)
    current = _activity(db, user_id, day=5)
    body = _report_body("Fallback-path run")
    _add_report(db, prior, body=body, store_digest=False)  # digest column NULL

    out = fetch_prior_digests(db, current)
    assert len(out) == 1
    # The body is projected through the shared digest. (activity_date is asserted
    # by prefix, not exact value: SQLite returns the joined start_date naive while
    # the in-memory object is tz-aware; Postgres returns it aware. The digest
    # projection is tz-agnostic — a plain .isoformat() passthrough.)
    expected = build_report_digest(body, prior.start_date)
    assert out[0].headline == expected.headline
    assert out[0].lead_argument == expected.lead_argument
    assert out[0].next_steps == expected.next_steps
    assert out[0].activity_date.startswith("2026-05-01T08:00:00")


def test_fetch_prior_digests_stored_matches_shared_projection(db):
    """A2a invariant: a stored digest equals what the single shared projection
    (digest.build_report_digest) produces from the same body, so reading the
    stored artifact is byte-equal to re-projecting it. This is the invariant
    test_report_digest.py pinned on the old read-time helper, relocated to the
    seam that now owns the read."""
    user_id = _user(db)
    prior = _activity(db, user_id, day=1)
    body = _report_body("Equivalence run")
    _add_report(db, prior, body=body, store_digest=True)
    current = _activity(db, user_id, day=5)

    stored = fetch_prior_digests(db, current)[0]
    assert stored == build_report_digest(body, prior.start_date)
    assert isinstance(stored, PriorReportDigest)


def test_fetch_prior_digests_excludes_self_and_fallback(db):
    user_id = _user(db)
    prior = _activity(db, user_id, day=1)
    current = _activity(db, user_id, day=5)
    _add_report(db, prior, store_digest=True)
    _add_report(db, current, store_digest=True)            # self -> excluded
    fallback_act = _activity(db, user_id, day=3)
    _add_report(db, fallback_act, store_digest=False, is_fallback=True)  # fallback -> excluded

    out = fetch_prior_digests(db, current)
    assert len(out) == 1
    assert out[0].activity_date == prior.start_date.isoformat()


def test_fetch_prior_digests_newest_first_one_per_activity_capped(db):
    user_id = _user(db)
    current = _activity(db, user_id, day=20)
    acts = [_activity(db, user_id, day=d) for d in (2, 6, 10)]
    for a in acts:
        _add_report(db, a, store_digest=True)
    # A second (different-version) report on the most recent prior activity:
    # dedupe keeps one digest per activity. A distinct prompt_id is required to
    # coexist under the (activity_id, prompt_id, schema_version) unique key.
    _add_report(
        db, acts[2], body=_report_body("Other version"), store_digest=True,
        prompt_id="coach_report_v8",
    )

    out = fetch_prior_digests(db, current)
    assert len(out) == 2  # capped at the last two exchanges
    # Newest-first: day 10 then day 6.
    assert out[0].activity_date == acts[2].start_date.isoformat()
    assert out[1].activity_date == acts[1].start_date.isoformat()


def test_fetch_prior_digests_empty_when_no_prior(db):
    user_id = _user(db)
    current = _activity(db, user_id, day=5)
    assert fetch_prior_digests(db, current) == []


# --- fetch_prior_commitments (A2d M7 read) ---------------------------------


def test_fetch_prior_commitments_returns_structured_next_steps(db):
    """The M7 read pulls the prior exchange's commitments as STRUCTURED dicts
    (action/details/why), not the flattened display strings the digest carries,
    because the adherence theme classifier reads action+details."""
    user_id = _user(db)
    prior = _activity(db, user_id, day=1)
    current = _activity(db, user_id, day=5)
    body = _report_body("Prior advice run")
    _add_report(db, prior, body=body, store_digest=True)

    out = fetch_prior_commitments(db, current)
    assert out is not None
    assert out.source_activity_id == prior.id
    assert out.source_start_date.isoformat().startswith("2026-05-01T08:00:00")
    assert out.next_steps == body["next_steps"]  # structured dicts, unflattened
    assert out.next_steps[0]["action"] == "Add a tempo segment"


def test_fetch_prior_commitments_none_when_no_prior(db):
    user_id = _user(db)
    current = _activity(db, user_id, day=5)
    assert fetch_prior_commitments(db, current) is None


def test_fetch_prior_commitments_excludes_self_and_fallback(db):
    user_id = _user(db)
    prior = _activity(db, user_id, day=1)
    current = _activity(db, user_id, day=5)
    _add_report(db, prior, body=_report_body("Prior"), store_digest=True)
    _add_report(db, current, store_digest=True)  # self -> excluded
    fallback_act = _activity(db, user_id, day=3)
    _add_report(db, fallback_act, store_digest=False, is_fallback=True)  # excluded

    out = fetch_prior_commitments(db, current)
    assert out is not None
    assert out.source_activity_id == prior.id  # not self, not the fallback


def test_fetch_prior_commitments_picks_most_recent_prior(db):
    """When several prior reports exist, the M7 read judges against the LATEST
    exchange's commitments (newest-first, the single most recent)."""
    user_id = _user(db)
    current = _activity(db, user_id, day=20)
    older = _activity(db, user_id, day=2)
    newer = _activity(db, user_id, day=10)
    _add_report(db, older, body=_report_body("Older"), store_digest=True)
    _add_report(db, newer, body=_report_body("Newer"), store_digest=True)

    out = fetch_prior_commitments(db, current)
    assert out is not None
    assert out.source_activity_id == newer.id
