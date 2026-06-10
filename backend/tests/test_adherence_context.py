"""M7 adherence in the coach context pack (DB-integration).

Confirms `build_context_pack` gathers the right rows and feeds the pure
`adherence` module: the most recent prior report's next_steps are labelled
against the subsequent comparable run, the current run is in the judging window,
non-comparable/no-prior-report cases degrade to empty, and explicit chat/note
pushback on the prior report flips the implicit label to "disputed".
"""

import uuid
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V3


def _user(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()
    return user_id


def _activity(db, user_id, start_date, *, user_intent=None, name="Run"):
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        user_intent=user_intent,
        start_date=start_date,
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=50.0,
        average_speed_mps=2.78,
        raw_summary={},
    )
    db.add(a)
    db.flush()
    return a


def _metrics(db, activity, *, effort="easy", duration_class="standard",
             structure="continuous", is_race=False, confidence="high"):
    dm = DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        effort=effort,
        duration_class=duration_class,
        structure=structure,
        is_hilly=False,
        is_race=is_race,
        effort_score=3.0,
        confidence=confidence,
        confidence_reasons=[],
        flags=[],
    )
    db.add(dm)
    db.flush()
    return dm


def _report(db, activity, *, next_action, details="", why="", is_fallback=False,
            prompt_id="coach_report_v3"):
    content = {
        "headline": "Prior run",
        "thesis": "Body",
        "lead_argument": {"text": "Lead", "evidence": []},
        "key_takeaways": [{"text": "Takeaway"}],
        "next_steps": [{"action": next_action, "details": details, "why": why}],
        "risks": [],
        "questions": [],
    }
    r = CoachReport(
        id=uuid.uuid4(),
        activity_id=activity.id,
        prompt_id=prompt_id,
        schema_version="1.2",
        report=content,
        meta={},
        context_pack={},
        is_fallback=is_fallback,
    )
    db.add(r)
    db.flush()
    return r


def test_pack_labels_easy_discipline_acted_on(db):
    """Prior advice 'keep easy easy' + a subsequent easy-intended easy run that
    complied -> acted_on, judged against that subsequent run."""
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Keep your easy runs easy", details="Zone 2 only")

    current = _activity(db, user_id, base + timedelta(days=3), user_intent="Easy Run")
    _metrics(db, current, effort="easy")

    pack = build_context_pack(db, current)

    # Date is compared by prefix: SQLite drops tzinfo on a column-scalar query
    # (source date) while keeping it on identity-mapped ORM objects (candidate
    # date); Postgres keeps both tz-aware. The prefix is stable across backends.
    assert pack.adherence.prior_report_date.startswith("2026-03-01T08:00:00")
    assert len(pack.adherence.outcomes) == 1
    o = pack.adherence.outcomes[0]
    assert o.theme == "easy_discipline"
    assert o.label == "acted_on"
    assert o.overridden is False
    assert o.comparable_activity_date.startswith("2026-03-04T08:00:00")


def test_pack_labels_easy_discipline_contradicted(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Keep your easy runs easy")

    current = _activity(db, user_id, base + timedelta(days=3), user_intent="Easy Run")
    _metrics(db, current, effort="tempo")

    pack = build_context_pack(db, current)
    assert pack.adherence.outcomes[0].label == "contradicted"


def test_pack_no_prior_report_is_empty(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    current = _activity(db, user_id, base, user_intent="Easy Run")
    _metrics(db, current)

    pack = build_context_pack(db, current)
    assert pack.adherence.prior_report_date is None
    assert pack.adherence.outcomes == []


def test_pack_non_comparable_current_run_abstains(db):
    """Current run the runner MEANT as a workout is not a fair test of easy
    advice -> no outcome."""
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Keep your easy runs easy")

    current = _activity(db, user_id, base + timedelta(days=3), user_intent="Tempo")
    _metrics(db, current, effort="tempo")

    pack = build_context_pack(db, current)
    assert pack.adherence.outcomes == []


def test_pack_ignores_fallback_prior_report(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Keep your easy runs easy", is_fallback=True)

    current = _activity(db, user_id, base + timedelta(days=3), user_intent="Easy Run")
    _metrics(db, current, effort="easy")

    pack = build_context_pack(db, current)
    assert pack.adherence.outcomes == []


def test_pack_chat_pushback_overrides_to_disputed(db):
    """A user chat reply disputing the prior report flips the implicit label."""
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Keep your easy runs easy")
    db.add(CoachChatMessage(
        id=uuid.uuid4(), activity_id=a1.id, role="user",
        content="Honestly that was off, the run actually felt hard.",
    ))
    db.flush()

    current = _activity(db, user_id, base + timedelta(days=3), user_intent="Easy Run")
    _metrics(db, current, effort="tempo")  # implicitly contradicted

    pack = build_context_pack(db, current)
    o = pack.adherence.outcomes[0]
    assert o.overridden is True
    assert o.label == "disputed"


def test_pack_checkin_note_pushback_overrides(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Keep your easy runs easy")
    db.add(CheckIn(id=uuid.uuid4(), activity_id=a1.id, notes="your read was wrong"))
    db.flush()

    current = _activity(db, user_id, base + timedelta(days=3), user_intent="Easy Run")
    _metrics(db, current, effort="easy")

    pack = build_context_pack(db, current)
    assert pack.adherence.outcomes[0].overridden is True


def test_pack_unanalysed_intervening_run_is_skipped(db):
    """An intervening run without a DerivedMetric must not be treated as a
    comparable (e.g. as a missing quality session)."""
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, next_action="Add a tempo run")

    # intervening, unanalysed (no metrics)
    _activity(db, user_id, base + timedelta(days=1))

    current = _activity(db, user_id, base + timedelta(days=3))
    _metrics(db, current, effort="tempo")  # the quality session

    pack = build_context_pack(db, current)
    assert pack.adherence.outcomes[0].label == "acted_on"
    assert pack.adherence.outcomes[0].comparable_activity_date.startswith("2026-03-04T08:00:00")


class TestPromptVersioning:
    def test_v4_is_active_default(self):
        # The active default has since advanced (#168 -> v8); v4 remains
        # registered and byte-stable for its cached reports.
        assert settings.COACH_PROMPT_ID == "coach_report_v9"

    def test_v4_adds_adherence_rule(self):
        v4 = PROMPT_VERSIONS["coach_report_v4"]
        assert "ADHERENCE" in v4
        assert "18." in v4
        assert "disputed" in v4
        assert "do not nag" in v4.lower()

    def test_v3_stays_byte_stable(self):
        # v3 must not gain the M7 rule; cached v3 reports stay reproducible.
        assert PROMPT_VERSIONS["coach_report_v3"] == SYSTEM_PROMPT_V3
        assert "ADHERENCE" not in PROMPT_VERSIONS["coach_report_v3"]

    def test_v4_extends_v3(self):
        v3 = PROMPT_VERSIONS["coach_report_v3"]
        v4 = PROMPT_VERSIONS["coach_report_v4"]
        assert v4 != v3
        assert v4.startswith(v3)
