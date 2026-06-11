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
        )
        assert [a.label for a in notification.actions] == ["RPE 7"]

    def test_option_with_non_numeric_payload_is_dropped(self, telegram_configured):
        read = _opener_with_options(
            [TappableOption(id="bad", label="?", kind="rpe", payload="not-a-number")]
        )
        notification = build_coach_notification(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="opener",
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


def _update(token: str, *, chat_id=42, cbq_id="cbq-1") -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": cbq_id,
            "data": token,
            "message": {"chat": {"id": chat_id}},
        },
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
        yield {"answer": answer, "fuller": fuller}


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

    def test_wrong_chat_id_is_rejected(self, client, db, configured, isolate_side_effects):
        a = _seed_activity(db)
        token = encode(kind="rpe", activity_id=str(a.id), value=7)
        resp = client.post(
            "/api/webhooks/telegram",
            json=_update(token, chat_id=999),
            headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET},
        )
        assert resp.status_code == 403
        assert db.query(CheckIn).filter(CheckIn.activity_id == a.id).first() is None

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
        a_app = _seed_activity(db)
        a_tg = _seed_activity(db)

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
