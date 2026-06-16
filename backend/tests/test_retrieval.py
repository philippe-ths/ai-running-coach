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
from app.models.user_material import UserMaterial
from app.schemas.coach_context import PriorReportDigest
from app.services.coach.digest import build_report_digest
from app.services.coach.retrieval import (
    _MAX_USER_MATERIALS,
    fetch_corpus,
    fetch_prior_commitments,
    fetch_prior_digests,
    fetch_stream_view,
)
from app.services.coach import corpus as corpus_lib


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


# --- A4: opener-only rows are incomplete exchanges, excluded from prior reads ---


def _opener_only_body() -> dict:
    """A stored opener-state report: prose in opener_message, message empty, no
    next_steps (the fuller turn never landed)."""
    return {
        "message": "",
        "opener_message": "Quick reaction — full breakdown to follow.",
        "schedule_fuller_turn": True,
        "next_steps": [],
        "questions": [],
    }


def test_fetch_prior_digests_skips_opener_only_rows(db):
    """A prior opener-only row (incomplete exchange) must not leak a thin digest
    into the longitudinal memory, and must not shadow an earlier complete exchange."""
    user_id = _user(db)
    current = _activity(db, user_id, day=20)
    complete = _activity(db, user_id, day=5)
    opener_only = _activity(db, user_id, day=12)  # more recent, but incomplete
    _add_report(db, complete, body=_report_body("Real exchange"), store_digest=True)
    _add_report(db, opener_only, body=_opener_only_body(), store_digest=False,
                prompt_id="coach_message_v2")

    digests = fetch_prior_digests(db, current)
    headlines = [d.headline for d in digests]
    assert "Real exchange" in headlines
    # the opener-only row contributed no digest
    assert all(d.activity_date[:10] != opener_only.start_date.date().isoformat()
               for d in digests)


def test_fetch_prior_commitments_skips_opener_only_rows(db):
    """An opener-only row carries no commitments and must not shadow the earlier
    complete exchange's next_steps that M7 judges against."""
    user_id = _user(db)
    current = _activity(db, user_id, day=20)
    complete = _activity(db, user_id, day=5)
    opener_only = _activity(db, user_id, day=12)
    _add_report(db, complete, body=_report_body("Real exchange"), store_digest=True)
    _add_report(db, opener_only, body=_opener_only_body(), store_digest=False,
                prompt_id="coach_message_v2")

    out = fetch_prior_commitments(db, current)
    assert out is not None
    assert out.source_activity_id == complete.id
    assert len(out.next_steps) == 1


# --- P1.2 / P4: fetch_corpus (keyed school lookup + the user-materials read) --
# The seam resolves the keyed school from the code-resident corpus.py (degrading to
# house-core-only) AND, since #286 (ADR 0017), reads the runner's ACTIVE distilled
# UserMaterial rows into Corpus.user_materials. School resolution is unchanged; the
# materials read degrades cleanly — a null db/user_id, no active materials, or a
# malformed distilled row never raises (containment: a bad record degrades the pack,
# it never breaks the exchange).

_DISTILLED = {
    "stance": "Run on feel; chase the joy of the trail.",
    "principles": ["Time on feet over pace.", "Hills are strength work."],
    "method_framing": "Frame every run by how sustainable and enjoyable it is.",
    "emphasis_hints": ["enjoyment", "time on feet"],
}


def _material(db, user_id, *, day: int, status: str = "active", distilled=None,
              title: str = "My plan", hash_suffix: str = "") -> UserMaterial:
    m = UserMaterial(
        id=uuid.uuid4(),
        user_id=user_id,
        kind="philosophy",
        title=title,
        filename=f"{title}.md",
        raw_text="(untrusted source — never read into the pack)",
        distilled=dict(_DISTILLED) if distilled is None else distilled,
        status=status,
        content_hash=f"hash-{user_id}-{day}-{hash_suffix}",
        created_at=datetime(2026, 5, day, 8, 0, tzinfo=timezone.utc),
    )
    db.add(m)
    db.flush()
    return m


def test_fetch_corpus_resolves_the_named_school(db):
    """Every known school id returns the house core plus that exact school
    (school resolution is unchanged by the P4 materials read)."""
    uid = _user(db)
    for school_id in ("aerobic-base", "polarized", "enjoyment-and-consistency"):
        result = fetch_corpus(db, uid, school_id)
        assert result.house_core is corpus_lib.HOUSE_CORE
        assert result.school is corpus_lib.SCHOOLS[school_id]


def test_fetch_corpus_house_core_is_always_present(db):
    """Every lookup carries the always-present house core, school or not."""
    uid = _user(db)
    for school_id in ("aerobic-base", "polarized", "enjoyment-and-consistency"):
        assert fetch_corpus(db, uid, school_id).house_core is corpus_lib.HOUSE_CORE


def test_fetch_corpus_unknown_school_degrades_to_house_core_only(db):
    """An unknown school id degrades to house-core-only (school is None), never raising."""
    uid = _user(db)
    result = fetch_corpus(db, uid, "no-such-school")
    assert result.house_core is corpus_lib.HOUSE_CORE
    assert result.school is None


def test_fetch_corpus_none_school_degrades_to_house_core_only(db):
    """A null school id (the no-selection case) also degrades to house-core-only."""
    uid = _user(db)
    result = fetch_corpus(db, uid, None)
    assert result.house_core is corpus_lib.HOUSE_CORE
    assert result.school is None


# --- P4 (#286): the user-materials read --------------------------------------


def test_fetch_corpus_reads_active_user_materials(db):
    """Active distilled materials map into Corpus.user_materials as the typed
    DistilledMaterial shape (stance/principles/method_framing/emphasis_hints)."""
    uid = _user(db)
    _material(db, uid, day=10)
    result = fetch_corpus(db, uid, "aerobic-base")
    assert len(result.user_materials) == 1
    m = result.user_materials[0]
    assert m.stance == _DISTILLED["stance"]
    assert list(m.principles) == _DISTILLED["principles"]
    assert m.method_framing == _DISTILLED["method_framing"]
    assert list(m.emphasis_hints) == _DISTILLED["emphasis_hints"]


def test_fetch_corpus_only_active_materials(db):
    """processing / failed / archived rows are excluded; only active reaches the corpus."""
    uid = _user(db)
    _material(db, uid, day=10, status="active", title="active", hash_suffix="a")
    _material(db, uid, day=11, status="processing", title="processing", hash_suffix="p")
    _material(db, uid, day=12, status="failed", title="failed", hash_suffix="f")
    _material(db, uid, day=13, status="archived", title="archived", hash_suffix="ar")
    result = fetch_corpus(db, uid, "aerobic-base")
    assert len(result.user_materials) == 1


def test_fetch_corpus_materials_are_user_scoped(db):
    """A different runner's materials never bleed into this runner's corpus."""
    uid = _user(db)
    other = _user(db)
    _material(db, other, day=10, hash_suffix="other")
    result = fetch_corpus(db, uid, "aerobic-base")
    assert result.user_materials == ()


def test_fetch_corpus_materials_newest_first(db):
    """Materials are ordered newest-first so the most recent reference leads."""
    uid = _user(db)
    _material(db, uid, day=10, distilled={**_DISTILLED, "stance": "older"}, hash_suffix="old")
    _material(db, uid, day=20, distilled={**_DISTILLED, "stance": "newer"}, hash_suffix="new")
    result = fetch_corpus(db, uid, "aerobic-base")
    assert [m.stance for m in result.user_materials] == ["newer", "older"]


def test_fetch_corpus_soft_caps_materials(db):
    """The seam soft-caps the active materials it carries (defence on pack size)."""
    uid = _user(db)
    for i in range(_MAX_USER_MATERIALS + 3):
        _material(db, uid, day=1 + i, hash_suffix=str(i))
    result = fetch_corpus(db, uid, "aerobic-base")
    assert len(result.user_materials) == _MAX_USER_MATERIALS


def test_fetch_corpus_skips_malformed_distilled(db):
    """An off-shape distilled row is skipped, never raising — containment means a
    bad record degrades the pack, it does not break the exchange."""
    uid = _user(db)
    _material(db, uid, day=10, distilled={"unexpected": "shape"}, title="bad", hash_suffix="bad")
    _material(db, uid, day=11, title="good", hash_suffix="good")
    result = fetch_corpus(db, uid, "aerobic-base")
    assert len(result.user_materials) == 1
    assert result.user_materials[0].stance == _DISTILLED["stance"]


def test_fetch_corpus_no_materials_is_empty_tuple(db):
    """A runner with no active materials gets an empty tuple, school intact."""
    uid = _user(db)
    result = fetch_corpus(db, uid, "aerobic-base")
    assert result.user_materials == ()
    assert result.school is corpus_lib.SCHOOLS["aerobic-base"]


def test_fetch_corpus_degrades_without_db_or_user(db):
    """A null db or user_id yields no materials (the seam's clean-degradation idiom),
    while the school still resolves from the code-resident corpus."""
    assert fetch_corpus(None, None, "aerobic-base").user_materials == ()
    assert fetch_corpus(None, None, "aerobic-base").school is corpus_lib.SCHOOLS["aerobic-base"]
    uid = _user(db)
    assert fetch_corpus(db, None, "aerobic-base").user_materials == ()
