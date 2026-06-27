"""Unit tests for the single-use Telegram chat-link token (#477)."""

import uuid
from unittest.mock import MagicMock, patch

from app.services.notifications import telegram_link_token


def test_mint_token_fits_telegram_start_constraints():
    fake = MagicMock()
    with patch.object(telegram_link_token, "redis_conn", fake):
        token = telegram_link_token.mint(uuid.uuid4())
    # Telegram's `start` param: <=64 chars, only [A-Za-z0-9_-].
    assert 0 < len(token) <= 64
    assert all(c.isalnum() or c in "_-" for c in token)
    fake.set.assert_called_once()
    # Stored with a TTL so a never-tapped link expires.
    assert fake.set.call_args.kwargs.get("ex")


def test_consume_returns_user_id_and_is_single_use():
    user_id = uuid.uuid4()
    fake = MagicMock()
    # First read returns the stored id (as bytes, like real redis); GETDEL then
    # removes it, so a second read returns None.
    fake.getdel.side_effect = [str(user_id).encode(), None]
    with patch.object(telegram_link_token, "redis_conn", fake):
        assert telegram_link_token.consume("tok") == user_id
        assert telegram_link_token.consume("tok") is None


def test_consume_handles_missing_malformed_and_errors():
    fake = MagicMock()
    with patch.object(telegram_link_token, "redis_conn", fake):
        # Empty token never hits redis.
        assert telegram_link_token.consume("") is None
        # Missing key.
        fake.getdel.return_value = None
        assert telegram_link_token.consume("x") is None
        # Non-UUID payload.
        fake.getdel.return_value = b"not-a-uuid"
        assert telegram_link_token.consume("x") is None
        # Redis error -> None, never raises.
        fake.getdel.side_effect = RuntimeError("redis down")
        assert telegram_link_token.consume("x") is None
