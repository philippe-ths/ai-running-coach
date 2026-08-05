"""Telegram tappable RPE/pain input (I1b, #220).

Two halves of the deployed-channel input loop:
- Outbound: the A4 opener notification carries an inline keyboard built from its
  RPE/pain options (the composer + adapter).
- Inbound: a tapped button POSTs a callback_query here; the endpoint authenticates
  it (secret header + chat_id, mirroring the Strava posture), then writes the same
  CheckIn the in-app path writes (parity) and fires the A4 fuller turn.

The inbound endpoint is BasicAuth-exempt (the /api/webhooks prefix), so the auth
checks below are the only thing standing between an untrusted POST and a DB write.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, CheckIn, User
from app.schemas.coach import (
    CoachMessageQuestion,
    CoachMessageReport,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
    TappableOption,
)
from app.services.notifications import build_coach_notification
from app.services.notifications.callback_token import (
    CallbackAction,
    decode,
    encode,
)


# --- Token codec ---------------------------------------------------------------


class TestCallbackTokenCodec:
    def test_round_trip_rpe(self):
        aid = str(uuid4())
        action = decode(encode(kind="rpe", activity_id=aid, value=7))
        assert action == CallbackAction(
            kind="rpe", activity_id=aid, value=7, field="rpe"
        )

    def test_round_trip_pain_maps_to_pain_score(self):
        aid = str(uuid4())
        action = decode(encode(kind="pain", activity_id=aid, value=3))
        assert action.field == "pain_score"
        assert action.value == 3

    def test_encode_rejects_non_tappable_kind(self):
        with pytest.raises(ValueError):
            encode(kind="reply", activity_id=str(uuid4()), value=1)

    def test_token_stays_within_telegram_64_byte_limit(self):
        # A real UUID activity id is the longest field; the token must still fit.
        token = encode(kind="pain", activity_id=str(uuid4()), value=10)
        assert len(token.encode("utf-8")) <= 64

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "garbage",
            "cb1|rpe|abc",            # wrong arity
            "cb1|rpe|abc|7|extra",    # wrong arity
            "cb0|rpe|abc|7",          # wrong prefix
            "cb1|reply|abc|7",        # non-tappable kind
            "cb1|rpe||7",             # empty activity id
            "cb1|rpe|abc|notanint",   # non-integer value
        ],
    )
    def test_decode_returns_none_for_malformed(self, token):
        assert decode(token) is None


# --- Outbound: opener keyboard composition -------------------------------------


def _opener_with_options(options) -> CoachReportRead:
    report = CoachMessageReport(
        message="",
        opener_message="Nice work! How did that feel?",
        questions=[
            CoachMessageQuestion(
                question="How hard did that feel?",
                reason="Calibrate effort against HR.",
                options=options,
            )
        ],
    )
    meta = CoachReportMeta(
        confidence="medium", model_id="claude-sonnet-4-6", prompt_id="coach_message_v2",
        schema_version="2.0", input_hash="abc", generated_at=datetime.now(),
    )
    return CoachReportRead(
        id=uuid4(), activity_id=uuid4(), report=report, meta=meta,
        debug=CoachReportDebug(context_pack={}, system_prompt="", raw_llm_response=None),
        created_at=datetime.now(),
    )


@pytest.fixture
def telegram_configured(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")


class TestOpenerKeyboardComposition:
    def test_opener_carries_rpe_and_pain_buttons(self, telegram_configured):
        read = _opener_with_options(
            [
                TappableOption(id="r6", label="Easy (6)", kind="rpe", payload=6),
                TappableOption(id="r8", label="Hard (8)", kind="rpe", payload=8),
                TappableOption(id="p0", label="No pain", kind="pain", payload=0),
            ]
        )
        notification = build_coach_notification(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="opener",
            recipient="42",  # stated, not invented (#795); these pin the keyboard
        )
        assert [a.label for a in notification.actions] == ["Easy (6)", "Hard (8)", "No pain"]
        # Each token decodes back to the right activity + kind + value.
        decoded = [decode(a.token) for a in notification.actions]
        assert decoded[0] == CallbackAction("rpe", str(read.activity_id), 6, "rpe")
        assert decoded[2] == CallbackAction("pain", str(read.activity_id), 0, "pain_score")

    def test_non_rpe_pain_options_are_skipped(self, telegram_configured):
        read = _opener_with_options(
            [
                TappableOption(id="rep", label="Reply", kind="reply", payload=None),
                TappableOption(id="dis", label="That's wrong", kind="dispute", payload=None),
                TappableOption(id="r7", label="RPE 7", kind="rpe", payload=7),
            ]
        )
        notification = build_coach_notification(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="opener",
            recipient="42",  # stated, not invented (#795); these pin the keyboard
        )
        assert [a.label for a in notification.actions] == ["RPE 7"]

    def test_option_with_non_numeric_payload_is_dropped(self, telegram_configured):
        read = _opener_with_options(
            [TappableOption(id="bad", label="?", kind="rpe", payload="not-a-number")]
        )
        notification = build_coach_notification(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="opener",
            recipient="42",  # stated, not invented (#795); these pin the keyboard
        )
        assert notification.actions == ()

    def test_fuller_stage_has_no_buttons(self, telegram_configured):
        # The fuller turn is the response to the input, so it carries no keyboard.
        read = _opener_with_options(
            [TappableOption(id="r7", label="RPE 7", kind="rpe", payload=7)]
        )
        notification = build_coach_notification(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="fuller",
            recipient="42",  # stated, not invented (#795); these pin the keyboard
        )
        assert notification.actions == ()


# --- Inbound: callback webhook -------------------------------------------------

_SECRET = "tg-secret"


def _seed_activity(db) -> Activity:
    uid = uuid4()
    db.add(User(id=uid, email=f"u-{uid}@example.com"))
    db.flush()
    a = Activity(
        id=uuid4(), user_id=uid, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(a)
    db.commit()
    return a


def _update(
    token: str, *, chat_id=42, cbq_id="cbq-1", message_id=0, reply_markup=None
) -> dict:
    message: dict = {"chat": {"id": chat_id}}
    if message_id:
        message["message_id"] = message_id
    if reply_markup is not None:
        message["reply_markup"] = reply_markup
    return {
        "update_id": 1,
        "callback_query": {
            "id": cbq_id,
            "data": token,
            "message": message,
        },
    }


def _keyboard(*buttons: tuple[str, str]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": token}] for label, token in buttons
        ]
    }


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(settings, "APP_ENV", "local")


@pytest.fixture
def isolate_side_effects():
    """Stub the re-analyze + fuller-turn enqueue + Telegram answer so the inbound
    tests focus on auth + the CheckIn write (those side effects are covered by
    their own suites)."""
    answer = MagicMock()
    notifier = MagicMock()
    notifier.answer_callback = answer
    with patch("app.services.checkins.analysis.analyze"), patch(
        "app.jobs.process_new_activity.maybe_enqueue_fuller_turn"
    ) as fuller, patch(
        "app.api.webhooks.get_notifier", return_value=notifier
    ):
        yield {"answer": answer, "fuller": fuller, "notifier": notifier}


class TestInboundAuth:
    def test_missing_secret_header_is_rejected_before_write(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        resp = client.post("/api/webhooks/telegram", json=_update(token))
        assert resp.status_code == 403
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None
        isolate_side_effects["answer"].assert_not_called()

    def test_wrong_secret_is_rejected(self, client, db, configured, isolate_side_effects):
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert resp.status_code == 403
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None

    def test_unknown_chat_is_rejected_without_writing(
        self, client, db, configured, isolate_side_effects
    ):
        # An unbound chat that is not the global owner chat is rejected, but with
        # a silent 200 + ack (not 403) so Telegram does not retry-amplify -- the
        # secret already proved the request is from Telegram (#477).
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=999),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "unauthorized_chat"
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None
        isolate_side_effects["answer"].assert_called_once()

    def test_empty_secret_in_production_fails_closed(
        self, client, db, monkeypatch, isolate_side_effects
    ):
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
        monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        monkeypatch.setattr(settings, "APP_ENV", "production")
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token),
            headers={"X-Telegram-Bot-Api-Secret-Token": ""},
        )
        assert resp.status_code == 403
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None


class TestInboundWrite:
    def _post(self, client, token, chat_id=42):
        return client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=chat_id),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )

    def test_rpe_tap_writes_checkin_and_answers_callback(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        resp = self._post(client, token)
        assert resp.status_code == 200
        row = db.query(CheckIn).filter(CheckIn.activity_id == a.id).first()
        assert row is not None and row.rpe == 7 and row.pain_score is None
        isolate_side_effects["answer"].assert_called_once()
        isolate_side_effects["fuller"].assert_called_once()

    def test_pain_tap_writes_pain_score(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="pain", activity_id=str(a.id), value=4)
        resp = self._post(client, token)
        assert resp.status_code == 200
        row = db.query(CheckIn).filter(CheckIn.activity_id == a.id).first()
        assert row.pain_score == 4 and row.rpe is None

    def test_unknown_activity_is_ignored_without_write(
        self, client, db, configured, isolate_side_effects
    ):
        token = encode(kind="rpe", activity_id=str(uuid4()), value=7)
        resp = self._post(client, token)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert db.query(CheckIn).count() == 0

    def test_bad_token_is_ignored_and_answered(
        self, client, db, configured, isolate_side_effects
    ):
        resp = self._post(client, "cb1|reply|x|1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert db.query(CheckIn).count() == 0
        isolate_side_effects["answer"].assert_called_once()

    @pytest.mark.parametrize(
        "kind,value",
        [
            ("rpe", 99),    # RPE schema bound is 1-10
            ("rpe", 0),     # below the RPE floor (ge=1)
            ("rpe", -3),    # negative
            ("pain", 99),   # pain_score schema bound is 0-10
            ("pain", -1),   # below the pain_score floor (ge=0)
        ],
    )
    def test_out_of_range_value_is_ignored_without_write_or_500(
        self, client, db, configured, isolate_side_effects, kind, value
    ):
        # #504: an authentic-but-forged callback carrying an out-of-range value must
        # not raise an uncaught ValidationError (HTTP 500) — Telegram treats a 500 as
        # failed delivery and retries, amplifying it. It is acked and ignored (200),
        # no CheckIn is written, and the bound is the CheckInCreate schema's own.
        a = _seed_activity(db)
        token = encode(kind=kind, activity_id=str(a.id), value=value)
        resp = self._post(client, token)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert resp.json()["reason"] == "bad_value"
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None
        isolate_side_effects["answer"].assert_called_once()

    def test_in_range_boundary_values_still_write(
        self, client, db, configured, isolate_side_effects
    ):
        # The guard must not over-reject: the schema's RPE 1-10 / pain 0-10 bounds
        # are inclusive, so the boundaries are the happy path, not forged input.
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=10)
        resp = self._post(client, token)
        assert resp.status_code == 200
        row = db.query(CheckIn).filter(CheckIn.activity_id == a.id).first()
        assert row is not None and row.rpe == 10

    def test_malformed_token_value_is_ignored_at_handler(
        self, client, db, configured, isolate_side_effects
    ):
        # A non-integer value token never decodes (decode returns None), so the
        # handler ignores it before any CheckIn construction — no 500, no write.
        resp = self._post(client, "cb1|rpe|abc|notanint")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert db.query(CheckIn).count() == 0
        isolate_side_effects["answer"].assert_called_once()

    def test_non_callback_update_is_ignored(
        self, client, db, configured, isolate_side_effects
    ):
        resp = client.post(
            "/api/webhooks/telegram",
            json={"update_id": 1, "message": {"text": "hello"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "not_callback"


class TestParityWithInApp:
    """A Telegram RPE tap and an in-app RPE post must produce the same CheckIn."""

    def test_telegram_and_in_app_rpe_write_identical_fields(
        self, client, db, configured, isolate_side_effects
    ):
        # One user owns both activities so the global-owner chat resolves to them
        # (single-user) and the tap authorizes — this test asserts CheckIn field
        # parity, not multi-user auth (covered by TestMultiUserInboundAuth, #608).
        owner = User(id=uuid4(), email="parity-owner@x.dev")
        db.add(owner)
        db.commit()
        a_app = _seed_activity_for(db, owner)
        a_tg = _seed_activity_for(db, owner)

        client.post(f"/api/activities/{a_app.id}/checkin", json={"rpe": 7})
        client.post(
            "/api/webhooks/telegram",
            json=_update(encode(kind="rpe", activity_id=str(a_tg.id), value=7)),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )

        in_app = db.query(CheckIn).filter(CheckIn.activity_id == a_app.id).first()
        via_tg = db.query(CheckIn).filter(CheckIn.activity_id == a_tg.id).first()
        assert in_app.rpe == via_tg.rpe == 7
        assert in_app.pain_score == via_tg.pain_score


class TestTapAcknowledgment:
    """#230: a successful tap is acknowledged persistently in-chat by check-marking
    the chosen button on the opener's inline keyboard (only the keyboard is edited;
    editing the text would strip the HTML title/link Telegram does not echo back).
    """

    def _post(self, client, token, **update_kwargs):
        return client.post(
            "/api/webhooks/telegram",
            json=_update(token, **update_kwargs),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )

    def test_tap_marks_the_chosen_button(self, client, db, configured, isolate_side_effects):
        a = _seed_activity(db)
        t5 = encode(kind="rpe", activity_id=str(a.id), value=5)
        t9 = encode(kind="rpe", activity_id=str(a.id), value=9)
        kb = _keyboard(("Cruising (RPE 5-6)", t5), ("Really pushing (RPE 9+)", t9))

        resp = self._post(client, t9, message_id=555, reply_markup=kb)

        assert resp.status_code == 200
        edit = isolate_side_effects["notifier"].edit_message_reply_markup
        edit.assert_called_once()
        kwargs = edit.call_args.kwargs
        assert kwargs["message_id"] == 555
        rows = kwargs["reply_markup"]["inline_keyboard"]
        assert rows[0][0]["text"] == "Cruising (RPE 5-6)"          # untouched
        assert rows[1][0]["text"] == "✓ Really pushing (RPE 9+)"  # marked
        assert rows[1][0]["callback_data"] == t9                   # token preserved

    def test_retap_moves_the_mark(self, client, db, configured, isolate_side_effects):
        a = _seed_activity(db)
        t5 = encode(kind="rpe", activity_id=str(a.id), value=5)
        t9 = encode(kind="rpe", activity_id=str(a.id), value=9)
        kb = _keyboard(("✓ Cruising (RPE 5-6)", t5), ("Really pushing (RPE 9+)", t9))

        resp = self._post(client, t9, message_id=555, reply_markup=kb)

        assert resp.status_code == 200
        rows = isolate_side_effects["notifier"].edit_message_reply_markup \
            .call_args.kwargs["reply_markup"]["inline_keyboard"]
        assert rows[0][0]["text"] == "Cruising (RPE 5-6)"              # mark removed
        assert rows[1][0]["text"] == "✓ Really pushing (RPE 9+)"  # mark moved

    def test_same_button_retap_skips_the_noop_edit(self, client, db, configured, isolate_side_effects):
        # Telegram rejects an edit that does not change the markup, so an
        # already-marked button must not trigger a redundant API call.
        a = _seed_activity(db)
        t9 = encode(kind="rpe", activity_id=str(a.id), value=9)
        kb = _keyboard(("✓ Really pushing (RPE 9+)", t9))

        resp = self._post(client, t9, message_id=555, reply_markup=kb)

        assert resp.status_code == 200
        isolate_side_effects["notifier"].edit_message_reply_markup.assert_not_called()

    def test_missing_keyboard_or_message_id_skips_marking(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)

        resp = self._post(client, token)  # no message_id, no reply_markup

        assert resp.status_code == 200
        row = db.query(CheckIn).filter(CheckIn.activity_id == a.id).first()
        assert row is not None and row.rpe == 7  # the write still happened
        isolate_side_effects["notifier"].edit_message_reply_markup.assert_not_called()

    def test_marking_failure_never_breaks_the_request(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        kb = _keyboard(("Comfortably hard (RPE 7-8)", token))
        isolate_side_effects["notifier"].edit_message_reply_markup.side_effect = (
            RuntimeError("telegram down")
        )

        resp = self._post(client, token, message_id=555, reply_markup=kb)

        assert resp.status_code == 200
        row = db.query(CheckIn).filter(CheckIn.activity_id == a.id).first()
        assert row is not None and row.rpe == 7


# --- Inbound: the #296 "done" tap ---------------------------------------------


class TestDoneTap:
    """A "done" tap is a control signal, not a CheckIn: it schedules the full
    report (~30 min) and writes no CheckIn. Authenticated like the RPE/pain path."""

    def test_done_tap_schedules_full_report_writes_no_checkin(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="done", activity_id=str(a.id))
        with patch("app.api.webhooks.mark_done_and_schedule") as done:
            resp = client.post(
                "/api/webhooks/telegram",
                json=_update(token),
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["action"] == "done_scheduled"
        done.assert_called_once()
        # No CheckIn for a "done" tap (it carries no rpe/pain value).
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None
        isolate_side_effects["answer"].assert_called_once()

    def test_done_tap_rejected_without_secret(
        self, client, db, configured, isolate_side_effects
    ):
        a = _seed_activity(db)
        token = encode(kind="done", activity_id=str(a.id))
        with patch("app.api.webhooks.mark_done_and_schedule") as done:
            resp = client.post("/api/webhooks/telegram", json=_update(token))
        assert resp.status_code == 403
        done.assert_not_called()  # rejected before any side effect


# --- #477: multi-user inbound auth + chat-link binding -----------------------


def _seed_user_with_chat(db, chat_id: str) -> User:
    uid = uuid4()
    user = User(id=uid, email=f"u-{uid}@example.com", telegram_chat_id=chat_id)
    db.add(user)
    db.commit()
    return user


def _seed_activity_for(db, user: User) -> Activity:
    a = Activity(
        id=uuid4(),
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0),
        type="Run",
        name="Run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10.0,
        avg_hr=140,
        raw_summary={},
    )
    db.add(a)
    db.commit()
    return a


def _start_update(text: str, *, chat_id: int) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class TestMultiUserInboundAuth:
    def test_bound_user_can_tap_their_own_activity(
        self, client, db, configured, isolate_side_effects
    ):
        user = _seed_user_with_chat(db, "555")
        a = _seed_activity_for(db, user)
        token = encode(kind="rpe", activity_id=str(a.id), value=6)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=555),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "checkin_written"
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is not None

    def test_bound_user_cannot_tap_another_users_activity(
        self, client, db, configured, isolate_side_effects
    ):
        attacker = _seed_user_with_chat(db, "555")
        victim = _seed_user_with_chat(db, "666")
        victim_activity = _seed_activity_for(db, victim)
        token = encode(kind="rpe", activity_id=str(victim_activity.id), value=6)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=555),  # attacker's chat, victim's activity
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "not_owner"
        assert (
            db.query(CheckIn).filter(CheckIn.activity_id == victim_activity.id).first()
            is None
        )

    def test_global_owner_chat_still_writes_for_unbound_owner(
        self, client, db, configured, isolate_side_effects
    ):
        # Back-compat: today's owner has a null telegram_chat_id and taps on the
        # global chat (42). With OWNER_EMAIL unset and exactly one user, that user
        # is the implicit owner, so their global-chat tap on their own activity
        # keeps working.
        a = _seed_activity(db)  # the only user; no bound chat
        token = encode(kind="rpe", activity_id=str(a.id), value=5)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=42),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is not None

    def test_global_owner_chat_cannot_tap_another_users_activity(
        self, client, db, configured, isolate_side_effects, monkeypatch
    ):
        # #608: the global owner chat is resolved to its owning User (via
        # OWNER_EMAIL) and subjected to the SAME per-activity ownership check as a
        # bound user. A tap from the owner's global chat on ANOTHER runner's
        # activity is rejected — no write.
        monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@x.dev")
        owner = User(id=uuid4(), email="owner@x.dev", telegram_chat_id=None)
        victim = _seed_user_with_chat(db, "999")
        db.add(owner)
        db.commit()
        victim_activity = _seed_activity_for(db, victim)
        token = encode(kind="rpe", activity_id=str(victim_activity.id), value=6)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=42),  # global owner chat, victim's activity
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "not_owner"
        assert (
            db.query(CheckIn).filter(CheckIn.activity_id == victim_activity.id).first()
            is None
        )

    def test_global_owner_chat_writes_for_identified_owners_own_activity(
        self, client, db, configured, isolate_side_effects, monkeypatch
    ):
        # #608: the identified owner (OWNER_EMAIL) tapping their OWN activity from
        # the global chat still writes — the ownership check passes.
        monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@x.dev")
        owner = User(id=uuid4(), email="owner@x.dev", telegram_chat_id=None)
        db.add(owner)
        db.commit()
        _seed_user_with_chat(db, "999")  # a second, unrelated runner exists
        owner_activity = _seed_activity_for(db, owner)
        token = encode(kind="rpe", activity_id=str(owner_activity.id), value=5)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=42),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "checkin_written"
        assert (
            db.query(CheckIn).filter(CheckIn.activity_id == owner_activity.id).first()
            is not None
        )

    def test_global_owner_chat_unidentifiable_owner_is_rejected(
        self, client, db, configured, isolate_side_effects
    ):
        # #608/#600: OWNER_EMAIL unset AND more than one user => the global chat's
        # owner is not identifiable, so its tap is rejected (fail closed) rather
        # than granted an unauthenticated write to any activity.
        a = _seed_activity(db)  # first user
        _seed_user_with_chat(db, "999")  # a second user => owner ambiguous
        token = encode(kind="rpe", activity_id=str(a.id), value=5)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=42),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "unauthorized_chat"
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None


class TestStartLink:
    def test_start_binds_chat_to_token_user(
        self, client, db, configured, isolate_side_effects
    ):
        user = _seed_user_with_chat(db, None)  # not yet linked
        with patch(
            "app.api.webhooks.telegram_link_token.consume", return_value=user.id
        ):
            resp = client.post(
                "/api/webhooks/telegram",
                json=_start_update("/start abc123", chat_id=777),
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["action"] == "chat_linked"
        db.refresh(user)
        assert user.telegram_chat_id == "777"

    def test_start_sends_in_chat_confirmation(
        self, client, db, configured, isolate_side_effects
    ):
        # A successful bind replies once in the chat so the runner sees it worked
        # (#533): a Notification addressed to the just-bound chat id.
        user = _seed_user_with_chat(db, None)
        with patch(
            "app.api.webhooks.telegram_link_token.consume", return_value=user.id
        ):
            client.post(
                "/api/webhooks/telegram",
                json=_start_update("/start abc123", chat_id=777),
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            )
        send = isolate_side_effects["notifier"].send
        send.assert_called_once()
        assert send.call_args.args[0].to == "777"

    def test_start_confirmation_failure_does_not_break_bind(
        self, client, db, configured, isolate_side_effects
    ):
        # The confirmation send is best-effort: a transport failure must leave the
        # committed bind intact and the webhook still 200 (#533).
        user = _seed_user_with_chat(db, None)
        isolate_side_effects["notifier"].send.side_effect = RuntimeError("telegram down")
        with patch(
            "app.api.webhooks.telegram_link_token.consume", return_value=user.id
        ):
            resp = client.post(
                "/api/webhooks/telegram",
                json=_start_update("/start abc123", chat_id=777),
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["action"] == "chat_linked"
        db.refresh(user)
        assert user.telegram_chat_id == "777"

    def test_start_with_bad_token_is_noop(
        self, client, db, configured, isolate_side_effects
    ):
        user = _seed_user_with_chat(db, None)
        with patch("app.api.webhooks.telegram_link_token.consume", return_value=None):
            resp = client.post(
                "/api/webhooks/telegram",
                json=_start_update("/start nope", chat_id=777),
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "bad_link_token"
        db.refresh(user)
        assert user.telegram_chat_id is None
        # No bind happened, so no confirmation is sent (#533: no spam on a
        # repeat/expired /start).
        isolate_side_effects["notifier"].send.assert_not_called()

    def test_start_rebind_steals_chat_from_prior_owner(
        self, client, db, configured, isolate_side_effects
    ):
        prior = _seed_user_with_chat(db, "777")  # already holds chat 777
        claimant = _seed_user_with_chat(db, None)
        with patch(
            "app.api.webhooks.telegram_link_token.consume", return_value=claimant.id
        ):
            resp = client.post(
                "/api/webhooks/telegram",
                json=_start_update("/start tok", chat_id=777),
                headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
            )
        assert resp.status_code == 200
        db.refresh(prior)
        db.refresh(claimant)
        # chat->user stays a function: the prior owner's claim is cleared.
        assert prior.telegram_chat_id is None
        assert claimant.telegram_chat_id == "777"

    def test_start_rejected_without_secret(self, client, db, configured):
        user = _seed_user_with_chat(db, None)
        with patch(
            "app.api.webhooks.telegram_link_token.consume", return_value=user.id
        ) as consume:
            resp = client.post(
                "/api/webhooks/telegram",
                json=_start_update("/start abc", chat_id=777),
            )
        assert resp.status_code == 403
        consume.assert_not_called()  # secret gate runs before binding
