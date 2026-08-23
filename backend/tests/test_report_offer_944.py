"""#944 — the report can offer the change it just argued for.

The report's judgment is the sharpest the coach ever has, and until now it was
the one surface where the runner could do nothing with it. The offer-and-confirm
mechanism already existed; it was reachable only from a chat thread.

This file is the security file for that change: it puts a write-triggering
surface on a path that has never had one. The properties it holds are ownership,
confirmation, single-use, whitelist, and pack-grounding (#943 — the report may
not offer a change to a session it could not see). Every one of them has a
negative-path test here; the happy path alone would prove nothing.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.coach_report import CoachReport
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.schemas.coach import CoachMessageReport, CoachReportOffer
from app.services.coach import proposed_actions
from app.services.coach import report_offer as ro
from app.services.coach.context import build_context_pack
from app.services.coach.output_contract import (
    RECORD_COACH_TAIL_TOOL,
    ParsedBlocks,
    merge_opener,
    merge_report,
)

V9 = "coach_message_lean_grouped_v9"

# The Monday of the week every fixture sits in, so the planned session and the
# activity land in one week and the schedule section actually populates.
MON = date(2026, 6, 1)
TUE = MON + timedelta(days=1)
THU = MON + timedelta(days=3)


class _FakeRedis:
    """The token store, in memory. `getdel` is the atomic single-use primitive
    the real path relies on, so the fake implements exactly that and nothing
    else — a fake with a plain `get` would let a replay test pass by accident."""

    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


# --- fixtures -----------------------------------------------------------------


def _user(db) -> User:
    user = User(email=f"offer-{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
            upcoming_races=[],
        )
    )
    db.flush()
    return user


def _activity(db, user: User, *, day: date = TUE) -> Activity:
    activity = Activity(
        id=uuid.uuid4(),
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Morning run",
        type="Run",
        start_date=datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=10.0,
        average_speed_mps=2.78,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort="easy",
            duration_class="standard",
            structure="continuous",
            is_hilly=False,
            is_race=False,
            effort_score=30.0,
            confidence="high",
            confidence_reasons=[],
            flags=[],
        )
    )
    db.flush()
    return activity


def _plan_with_sessions(db, user: User):
    """An active plan with the run's own session (Tue) and one still to come (Thu)."""
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=[],
        horizon_end=MON + timedelta(days=60),
    )
    db.add(plan)
    db.flush()
    today = PlannedSession(
        plan_id=plan.id,
        user_id=user.id,
        window_start=TUE,
        window_end=TUE,
        intent="easy",
        discipline="run",
        commitment="committed",
        title="Easy 10k",
        target_distance_m=10000,
    )
    upcoming = PlannedSession(
        plan_id=plan.id,
        user_id=user.id,
        window_start=THU,
        window_end=THU,
        intent="quality",
        discipline="run",
        commitment="committed",
        title="Thursday intervals",
        target_distance_m=12000,
    )
    db.add_all([today, upcoming])
    db.flush()
    return plan, today, upcoming


def _pack(db, activity):
    return build_context_pack(db, activity, prompt_id=V9)


def _report_with_offer(**offer_fields) -> CoachMessageReport:
    return CoachMessageReport(
        message="You went well. The Thursday session is too long for this week.",
        headline="Solid run",
        offer=CoachReportOffer(**offer_fields),
    )


# --- the whitelist ------------------------------------------------------------


class TestWhitelist:
    def test_only_the_two_single_session_corrections_are_offerable(self):
        assert ro.REPORT_OFFER_KINDS == {"adjust_session", "complete_session"}
        # The tool's enum is derived from the set, so the model is never even
        # shown a kind the gate would refuse.
        assert RECORD_COACH_TAIL_TOOL["input_schema"]["properties"]["offer"][
            "properties"
        ]["action_type"]["enum"] == ["adjust_session", "complete_session"]

    @pytest.mark.parametrize(
        "kind", ["check_in", "intent", "split_block", "merge_blocks"]
    )
    def test_the_runners_own_account_of_events_is_not_offerable(self, db, kind):
        # A report proposing a check-in or an intent would be the report telling
        # the runner what they did. Those four stay in the conversation, where
        # the runner is the one talking. The model is not offered the kind, and
        # the store-time gate refuses it even if it writes one anyway.
        assert kind not in ro.REPORT_OFFER_KINDS
        assert kind not in RECORD_COACH_TAIL_TOOL["input_schema"]["properties"][
            "offer"
        ]["properties"]["action_type"]["enum"]

        user = _user(db)
        activity = _activity(db, user)
        pack = _pack(db, activity)
        rogue = ro.coerce_offer({"action_type": kind, "planned_session_id": None})
        report = CoachMessageReport(message="m").model_copy(update={"offer": rogue})
        assert ro.ground_offer(report, pack).offer is None

    def test_a_stored_kind_off_the_whitelist_is_never_minted(self, client, db):
        """The mint-time whitelist, driven through the REAL read path.

        The rogue kind is `draft_plan`, and the choice is what gives this test
        its teeth. It is the ONE action that takes no arguments, so it is the one
        kind that survives being stored, parses back whole, and would mint a real,
        tappable card if the whitelist stopped looking — every other excluded kind
        needs an argument `CoachReportOffer` does not carry, and would be refused
        by the thread contract regardless. It is also the realistic row: reports
        written while `draft_plan` was briefly on this whitelist, read by this
        build. An earlier version of this test used a hand-built object the read
        path could never produce, which left the guard unreachable behind a green
        assertion.
        """
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        _plan_with_sessions(db, user)
        db.commit()
        _store_report(db, activity, {"action_type": "draft_plan"})

        # Reachability, part one: the row PARSES with its rogue kind intact, so
        # the parse is not what refuses it.
        stored_row = (
            db.query(CoachReport)
            .filter(CoachReport.activity_id == activity.id)
            .first()
        )
        assert (
            CoachMessageReport.model_validate(stored_row.report).offer.action_type
            == "draft_plan"
        )

        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
            assert res.status_code == 200
            assert res.json()["offer"] is None
            assert fake._store == {}

            # Reachability, part two: the SAME offer mints a real card through
            # the thread's own path, so the whitelist is demonstrably the only
            # thing that refused it here.
            _r, frame = proposed_actions.mint_proposed_action(
                db, user.id, {"action_type": "draft_plan"}
            )
            assert frame is not None

    @pytest.mark.parametrize(
        "stored_offer",
        [
            # A kind a later build added and this one does not know.
            {"action_type": "reschedule_week", "planned_session_id": None},
            # A FIELD a later build added. The stored offer is a durable JSON
            # shape read by the build before the one that wrote it, so a rollback
            # must degrade to no card rather than to an unreadable report.
            {
                "action_type": "adjust_session",
                "planned_session_id": "11111111-1111-1111-1111-111111111111",
                "target_pace_s_per_km": 300,
            },
            # Outright junk.
            {"action_type": "", "planned_session_id": 12345},
        ],
    )
    def test_an_unreadable_offer_never_costs_the_runner_the_report(
        self, client, db, stored_offer
    ):
        """The #944 defect this guard exists for: `CoachReportOffer` was strict,
        so a row like these raised inside `service._to_read` and answered the
        report GET with a 500 — the report permanently unreadable over a card.
        A bad offer costs the offer and nothing else."""
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        db.commit()
        _store_report(db, activity, stored_offer)
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
        assert res.status_code == 200
        assert res.json()["offer"] is None
        assert res.json()["report"]["message"].startswith("You went well")

    def test_the_tool_offers_the_field_without_demanding_it(self):
        # Most reports do not conclude the plan should change. A required field
        # would invite an offer on every one of them.
        schema = RECORD_COACH_TAIL_TOOL["input_schema"]
        assert "offer" in schema["properties"]
        assert "offer" not in schema["required"]
        assert schema["properties"]["offer"]["additionalProperties"] is False

    def test_every_stored_offer_field_is_one_the_thread_contract_accepts(self):
        # The report must not open a looser channel into the same writers than the
        # conversation has. If CoachReportOffer grows a field ProposedActionRequest
        # does not know, this fails rather than silently dropping it at mint.
        allowed = set(proposed_actions.ProposedActionRequest.model_fields)
        assert set(CoachReportOffer.model_fields) <= allowed


# --- store-time grounding (#943) ----------------------------------------------


class TestGrounding:
    def test_an_offer_naming_a_session_the_pack_showed_survives(self, db):
        user = _user(db)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        pack = _pack(db, activity)
        # Ground truth for this test: the id really is in the pack the coach read.
        assert str(upcoming.id) in ro._session_ids_in_pack(pack)

        report = _report_with_offer(
            action_type="adjust_session",
            planned_session_id=str(upcoming.id),
            target_distance_m=8000,
        )
        assert ro.ground_offer(report, pack).offer is not None

    def test_an_offer_naming_a_session_the_pack_never_showed_is_dropped(self, db):
        # #943: the report may not offer a change to a session it could not see.
        user = _user(db)
        activity = _activity(db, user)
        _plan_with_sessions(db, user)
        pack = _pack(db, activity)

        report = _report_with_offer(
            action_type="complete_session", planned_session_id=str(uuid.uuid4())
        )
        assert ro.ground_offer(report, pack).offer is None

    def test_an_offer_naming_another_runners_session_is_dropped(self, db):
        # The cross-tenant shape of the same rule. The other runner's session is
        # a real row, owned by someone else, and it was never in this pack.
        user = _user(db)
        activity = _activity(db, user)
        _plan_with_sessions(db, user)
        stranger = _user(db)
        _plan, _theirs, their_upcoming = _plan_with_sessions(db, stranger)
        pack = _pack(db, activity)

        report = _report_with_offer(
            action_type="complete_session", planned_session_id=str(their_upcoming.id)
        )
        assert ro.ground_offer(report, pack).offer is None

    def test_a_report_with_no_schedule_in_its_pack_can_offer_no_session(self, db):
        # A runner with no plan gets no schedule section at all, so every session
        # id is ungrounded by construction.
        user = _user(db)
        activity = _activity(db, user)
        pack = _pack(db, activity)
        assert pack.schedule is None

        report = _report_with_offer(
            action_type="complete_session", planned_session_id=str(uuid.uuid4())
        )
        assert ro.ground_offer(report, pack).offer is None

    def test_a_report_may_not_offer_to_redraft_the_block(self, db):
        """`draft_plan` is excluded on purpose (see REPORT_OFFER_KINDS).

        It is the one action here that is not idempotent — a second confirm
        drafts again and supersedes the plan the first one wrote — and a report
        about ONE run is the wrong size of surface to replace the block from.
        The thread still offers it, which is where that conversation happens.
        """
        user = _user(db)
        activity = _activity(db, user)
        pack = _pack(db, activity)
        report = _report_with_offer(action_type="draft_plan")

        assert ro.ground_offer(report, pack).offer is None
        # And it is still offerable in the conversation, so this is a boundary on
        # the REPORT rather than the action being withdrawn from the product.
        assert "draft_plan" in proposed_actions.ProposedActionRequest.model_fields[
            "action_type"
        ].annotation.__args__

    @pytest.mark.parametrize(
        "fields",
        [
            # adjust_session with both targets: two prescriptions for one session.
            {"action_type": "adjust_session", "target_distance_m": 8000,
             "target_duration_s": 2400},
            # adjust_session with neither: "change this session" is not something
            # a runner can agree to.
            {"action_type": "adjust_session"},
            # complete_session with nothing to complete.
            {"action_type": "complete_session"},
            # A correction riding on an action that does not take one. Uses a
            # WHITELISTED kind deliberately: with `draft_plan` here the whitelist
            # would refuse it first and this case would prove nothing about the
            # cross-field rule it is written for.
            {"action_type": "complete_session", "target_distance_m": 8000},
        ],
    )
    def test_an_off_contract_offer_is_dropped(self, db, fields):
        user = _user(db)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        pack = _pack(db, activity)
        fields = dict(fields)
        # Every case is isolated to the ONE rule it is written for. The cases
        # about a TARGET get a real, shown session id so nothing else can be what
        # fails them; the case about the MISSING id is the only one left bare.
        bare = fields["action_type"] == "complete_session" and not fields.get(
            "target_distance_m"
        )
        if not bare:
            fields["planned_session_id"] = str(upcoming.id)
        report = _report_with_offer(**fields)
        assert ro.ground_offer(report, pack).offer is None

    @pytest.mark.parametrize(
        "session_id",
        [
            "",
            "not-a-uuid",
            "../../etc/passwd",
            "1 OR 1=1",
            "\x00",
            "<script>alert(1)</script>",
            "x" * 64,
        ],
    )
    def test_a_session_id_that_is_not_an_id_is_dropped(self, db, session_id):
        # The id is stored as a string (it is dumped into a JSON column), so the
        # corpus matters: it is parsed as a UUID where it is used as one, and
        # anything that is not one never reaches a query.
        user = _user(db)
        activity = _activity(db, user)
        _plan_with_sessions(db, user)
        pack = _pack(db, activity)
        report = _report_with_offer(
            action_type="complete_session", planned_session_id=session_id
        )
        assert ro.ground_offer(report, pack).offer is None

    def test_an_oversized_session_id_never_becomes_a_stored_offer(self):
        # The bound is on the stored shape itself, so an unbounded string cannot
        # be written into the report row even before anything tries to parse it.
        assert ro.coerce_offer(
            {"action_type": "complete_session", "planned_session_id": "x" * 5000}
        ) is None

    def test_the_report_survives_every_drop(self, db):
        # Degrade-not-withhold: a bad offer costs the offer, never the coaching.
        user = _user(db)
        activity = _activity(db, user)
        pack = _pack(db, activity)
        report = _report_with_offer(
            action_type="complete_session", planned_session_id=str(uuid.uuid4())
        )
        grounded = ro.ground_offer(report, pack)
        assert grounded.offer is None
        assert grounded.message == report.message
        assert grounded.headline == report.headline

    def test_an_opener_carries_no_offer(self, db):
        # Stage one is a brief reaction written before the coach has said anything
        # to hang an offer on, and one evolving row cannot hold two.
        user = _user(db)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        pack = _pack(db, activity)
        report = _report_with_offer(
            action_type="complete_session", planned_session_id=str(upcoming.id)
        )
        assert ro.ground_offer(report, pack, is_opener=True).offer is None


# --- the tail merge -----------------------------------------------------------


class TestTailMerge:
    def _blocks(self, tail):
        return ParsedBlocks(message="Good session.", tail=tail)

    def test_an_offer_on_the_tail_reaches_the_report(self):
        sid = str(uuid.uuid4())
        report = merge_report(
            self._blocks(
                {
                    "headline": "h",
                    "next_steps": [],
                    "risks": [],
                    "questions": [],
                    "offer": {
                        "action_type": "complete_session",
                        "planned_session_id": sid,
                    },
                }
            )
        )
        assert report.offer.action_type == "complete_session"
        assert report.offer.planned_session_id == sid
        assert report.tail_degraded is False

    def test_a_malformed_offer_costs_the_offer_and_not_the_tail(self):
        # The reason the offer is lifted out before the tail is constructed: a
        # typo in an optional affordance must not discard the runner's next_steps.
        # The tail survives whatever the offer turns out to be — and an offer
        # naming a kind nobody offers is refused by the gate a moment later
        # (TestWhitelist), not by silently corrupting the tail here.
        report = merge_report(
            self._blocks(
                {
                    "headline": "h",
                    "next_steps": [
                        {"action": "Easy week", "details": "Keep it light", "why": "load"}
                    ],
                    "risks": [],
                    "questions": [],
                    "offer": {"action_type": "become_my_coach", "nonsense": 1},
                }
            )
        )
        assert report.tail_degraded is False
        assert len(report.next_steps) == 1

    def test_an_offer_that_is_not_even_an_object_is_dropped(self):
        report = merge_report(
            self._blocks(
                {
                    "headline": "h",
                    "next_steps": [],
                    "risks": [],
                    "questions": [],
                    "offer": "just change thursday",
                }
            )
        )
        assert report.offer is None
        assert report.tail_degraded is False

    def test_the_opener_merge_carries_no_offer(self):
        report = merge_opener(
            self._blocks(
                {
                    "headline": "h",
                    "questions": [],
                    "offer": {"action_type": "complete_session"},
                }
            )
        )
        assert report.offer is None

    def test_a_report_stored_before_944_still_validates(self):
        # Optional and defaulted; every stored report predates this field.
        legacy = {
            "message": "old report",
            "headline": "h",
            "next_steps": [],
            "risks": [],
            "questions": [],
        }
        assert CoachMessageReport.model_validate(legacy).offer is None


# --- read-time minting --------------------------------------------------------


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


def _store_report(db, activity, offer: dict | None):
    body = {
        "message": "You went well. Thursday is too long for this week.",
        "headline": "Solid run",
        "next_steps": [],
        "risks": [],
        "questions": [],
    }
    if offer is not None:
        body["offer"] = offer
    row = CoachReport(
        activity_id=activity.id,
        report=body,
        meta={
            "confidence": "medium",
            "model_id": "test-model-do-not-use-in-prod",
            "prompt_id": V9,
            "schema_version": "2.0",
            "input_hash": "x",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "policy_violations": [],
        },
        context_pack={},
        prompt_id=V9,
        schema_version="2.0",
        is_fallback=False,
    )
    db.add(row)
    db.commit()
    return row


class TestReadTimeMint:
    """The key decision: the token is minted when the OWNER READS the report.

    A report is written by a background worker and read hours later over
    Telegram; the action token lives thirty minutes. A token minted at generation
    is always dead on arrival, so the stored offer carries none.
    """

    def test_the_owner_reading_the_report_gets_a_card_and_a_token(self, client, db):
        # A decoy runner exists FIRST, so "the reader" and "whoever happens to be
        # first in the users table" are different ids. Without this the key
        # assertion below holds for any user the endpoint might have picked.
        _decoy = _user(db)
        user = _user(db)
        db.commit()
        assert _decoy.id != user.id
        _act_as(user)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()
        _store_report(
            db,
            activity,
            {"action_type": "complete_session", "planned_session_id": str(upcoming.id)},
        )
        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            res = client.get(f"/api/activities/{activity.id}/coach-report")

        assert res.status_code == 200
        card = res.json()["offer"]
        assert card["action_type"] == "complete_session"
        assert card["token"]
        assert "Thursday intervals" in card["description"]
        assert card["confirm_label"] and card["dismiss_label"]
        # The token is written under the READER's own key, never a shared one.
        assert list(fake._store) == [f"coach-action:{user.id}:{card['token']}"]

    def test_the_stored_report_carries_no_token(self, client, db):
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()
        row = _store_report(
            db,
            activity,
            {"action_type": "complete_session", "planned_session_id": str(upcoming.id)},
        )
        assert "token" not in row.report["offer"]
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            body = client.get(f"/api/activities/{activity.id}/coach-report").json()
        # The durable half stays server-side entirely: the client reads the
        # minted card and nothing else, so no resource id rides the response.
        assert body["report"]["offer"] is None
        assert body["offer"]["token"]

    def test_re_reading_mints_a_fresh_token(self, client, db):
        # A report is read many times over the life of an offer; each read is what
        # makes the card tappable, so each read mints its own.
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()
        _store_report(
            db,
            activity,
            {"action_type": "complete_session", "planned_session_id": str(upcoming.id)},
        )
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            first = client.get(f"/api/activities/{activity.id}/coach-report").json()
            second = client.get(f"/api/activities/{activity.id}/coach-report").json()
        assert first["offer"]["token"] != second["offer"]["token"]

    def test_a_report_with_no_offer_gets_no_card(self, client, db):
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        _store_report(db, activity, None)
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
        assert res.status_code == 200
        assert res.json()["offer"] is None

    def test_a_stranger_cannot_read_the_report_and_nothing_is_minted(self, client, db):
        # Cross-tenant is indistinguishable from missing: one 404 covers both, and
        # the mint is never reached.
        owner = _user(db)
        stranger = _user(db)
        db.commit()
        activity = _activity(db, owner)
        _plan, _today, upcoming = _plan_with_sessions(db, owner)
        db.commit()
        _store_report(
            db,
            activity,
            {"action_type": "complete_session", "planned_session_id": str(upcoming.id)},
        )
        _act_as(stranger)
        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
        assert res.status_code == 404
        assert fake._store == {}

    def test_an_offer_naming_a_session_the_reader_does_not_own_mints_nothing(
        self, client, db
    ):
        # Defence in depth for a stored row that should never exist: grounding
        # rejects it at store time, and the mint re-resolves ownership anyway.
        owner = _user(db)
        stranger = _user(db)
        db.commit()
        activity = _activity(db, owner)
        _plan, _theirs, their_session = _plan_with_sessions(db, stranger)
        db.commit()
        _store_report(
            db,
            activity,
            {
                "action_type": "complete_session",
                "planned_session_id": str(their_session.id),
            },
        )
        _act_as(owner)
        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
        assert res.status_code == 200
        assert res.json()["offer"] is None
        assert fake._store == {}

    def test_the_surfaces_kill_switch_silences_the_card(self, client, db, monkeypatch):
        # COACH_THREADS_ENABLED gates the confirm endpoint, so a card minted with
        # it off would be a button that 503s.
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()
        _store_report(
            db,
            activity,
            {"action_type": "complete_session", "planned_session_id": str(upcoming.id)},
        )
        monkeypatch.setattr(settings, "COACH_THREADS_ENABLED", False)
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
        assert res.status_code == 200
        assert res.json()["offer"] is None


# --- confirmation: the write --------------------------------------------------


class TestConfirm:
    """Nothing is written until the runner confirms, and then exactly once."""

    def _setup(self, client, db):
        user = _user(db)
        db.commit()
        _act_as(user)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()
        _store_report(
            db,
            activity,
            {"action_type": "complete_session", "planned_session_id": str(upcoming.id)},
        )
        return user, activity, upcoming

    def test_reading_the_report_writes_nothing(self, client, db):
        user, activity, upcoming = self._setup(client, db)
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            res = client.get(f"/api/activities/{activity.id}/coach-report")
        assert res.json()["offer"]["token"]
        db.refresh(upcoming)
        assert upcoming.completed_at is None

    def test_a_confirmed_offer_writes_once_and_the_token_cannot_be_replayed(
        self, client, db
    ):
        user, activity, upcoming = self._setup(client, db)
        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            token = client.get(
                f"/api/activities/{activity.id}/coach-report"
            ).json()["offer"]["token"]
            ok = client.post(
                "/api/coach/threads/actions/confirm", json={"token": token}
            )
            assert ok.status_code == 200
            assert ok.json()["action_type"] == "complete_session"
            db.refresh(upcoming)
            assert upcoming.completed_at is not None
            completed_at = upcoming.completed_at

            # Single-use: the same token again changes nothing.
            replay = client.post(
                "/api/coach/threads/actions/confirm", json={"token": token}
            )
        assert replay.status_code == 404
        db.refresh(upcoming)
        assert upcoming.completed_at == completed_at

    def test_a_stranger_cannot_spend_the_owners_token(self, client, db):
        user, activity, upcoming = self._setup(client, db)
        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            token = client.get(
                f"/api/activities/{activity.id}/coach-report"
            ).json()["offer"]["token"]
            stranger = _user(db)
            db.commit()
            _act_as(stranger)
            res = client.post(
                "/api/coach/threads/actions/confirm", json={"token": token}
            )
        assert res.status_code == 404
        db.refresh(upcoming)
        assert upcoming.completed_at is None
        # And the owner's token is not burnt by the attempt.
        assert list(fake._store) == [f"coach-action:{user.id}:{token}"]

    def test_an_expired_token_writes_nothing(self, client, db):
        user, activity, upcoming = self._setup(client, db)
        fake = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake):
            token = client.get(
                f"/api/activities/{activity.id}/coach-report"
            ).json()["offer"]["token"]
            # Expiry, as Redis performs it: the key is simply gone.
            fake._store.clear()
            res = client.post(
                "/api/coach/threads/actions/confirm", json={"token": token}
            )
        assert res.status_code == 404
        db.refresh(upcoming)
        assert upcoming.completed_at is None

    @pytest.mark.parametrize(
        "token",
        ["", "x" * 200, "../../etc/passwd", "\x00", "not-a-token"],
    )
    def test_a_junk_token_is_refused(self, client, db, token):
        user, activity, upcoming = self._setup(client, db)
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            res = client.post(
                "/api/coach/threads/actions/confirm", json={"token": token}
            )
        assert res.status_code in (404, 422)
        db.refresh(upcoming)
        assert upcoming.completed_at is None


# --- the generation path (the wiring) -----------------------------------------


class TestGenerationPath:
    """The store-time gate is only worth having if the generator actually runs it.

    These drive the real `_generate_message` against a stubbed Anthropic client,
    so removing the grounding call from the service turns them red — the unit
    tests above would stay green.
    """

    def _blocks(self, offer):
        return [
            {"type": "text", "text": "Good steady run. Thursday looks too long."},
            {
                "type": "tool_use",
                "name": "record_coach_tail",
                "input": {
                    "headline": "Solid run",
                    "next_steps": [],
                    "risks": [],
                    "questions": [],
                    "offer": offer,
                },
            },
        ]

    async def _generate(self, db, activity, offer, monkeypatch):
        from unittest.mock import AsyncMock

        from app.services.coach.llm import MessageResult
        from app.services.coach.service import get_or_generate_coach_report

        monkeypatch.setattr(settings, "COACH_PROMPT_ID", V9)
        fake = AsyncMock()
        fake.generate_coach_message = AsyncMock(
            return_value=MessageResult(
                content_blocks=self._blocks(offer), stop_reason="end_turn"
            )
        )
        with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
            return await get_or_generate_coach_report(db, str(activity.id))

    @pytest.mark.asyncio
    async def test_a_grounded_offer_is_stored_on_the_report(self, db, monkeypatch):
        user = _user(db)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()

        read = await self._generate(
            db,
            activity,
            {
                "action_type": "adjust_session",
                "planned_session_id": str(upcoming.id),
                "target_distance_m": 8000,
            },
            monkeypatch,
        )

        assert read.report.offer.action_type == "adjust_session"
        stored = (
            db.query(CoachReport)
            .filter(CoachReport.activity_id == activity.id)
            .first()
        )
        assert stored.report["offer"]["planned_session_id"] == str(upcoming.id)
        # And still no token in the durable row — that is minted on read.
        assert "token" not in stored.report["offer"]

    @pytest.mark.asyncio
    async def test_an_ungrounded_offer_never_reaches_the_stored_report(
        self, db, monkeypatch
    ):
        # #943 at the seam that matters: an id the pack never showed is dropped by
        # the generator, so it can never be minted into a card later.
        user = _user(db)
        activity = _activity(db, user)
        _plan_with_sessions(db, user)
        db.commit()

        read = await self._generate(
            db,
            activity,
            {
                "action_type": "complete_session",
                "planned_session_id": str(uuid.uuid4()),
            },
            monkeypatch,
        )

        assert read.report.offer is None
        assert read.report.message.startswith("Good steady run")
        stored = (
            db.query(CoachReport)
            .filter(CoachReport.activity_id == activity.id)
            .first()
        )
        assert stored.report.get("offer") is None
        assert stored.is_fallback is False


class TestOfferSurvivesTheVoiceRewrite:
    """The last stage rewrites PROSE. It must not take the offer with it.

    The voice pass replaces the attempt's report with a copy carrying the voiced
    text, and a copy that dropped the offer would silently cost every runner with
    a declared voice their card — a defect invisible to any test that only
    exercises the default-voice path (which returns before the rewrite runs).
    """

    @pytest.mark.asyncio
    async def test_a_voiced_report_keeps_its_offer(self, db):
        from unittest.mock import AsyncMock

        from app.services.coach import service as coach_service
        from app.services.coach.voice import resolve_voice
        from app.services.coach.voice_rewrite import RewriteOutcome

        user = _user(db)
        activity = _activity(db, user)
        _plan, _today, upcoming = _plan_with_sessions(db, user)
        db.commit()
        pack = _pack(db, activity)

        report = _report_with_offer(
            action_type="complete_session", planned_session_id=str(upcoming.id)
        )
        attempt = coach_service._MsgAttempt(
            report=report, raw_response="", truncated=False, violations=[]
        )

        with patch.object(
            coach_service,
            "revoice_report",
            AsyncMock(
                return_value=RewriteOutcome(
                    text="Said again, in their voice.", reason="applied"
                )
            ),
        ):
            voiced, reason = await coach_service._apply_voice(
                attempt, pack, voice=resolve_voice(None), user_id=user.id, is_opener=False
            )

        assert reason == "applied"
        assert voiced.report.voiced_message == "Said again, in their voice."
        assert voiced.report.offer is not None
        assert voiced.report.offer.planned_session_id == str(upcoming.id)
