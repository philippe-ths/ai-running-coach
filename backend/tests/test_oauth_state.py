"""Unit tests for the Strava OAuth signed-state codec (#469)."""

import uuid

from app.core import oauth_state


def test_round_trip_returns_user_id():
    user_id = uuid.uuid4()
    token = oauth_state.encode_state(user_id)
    assert oauth_state.decode_state(token) == user_id


def test_accepts_string_user_id():
    user_id = uuid.uuid4()
    token = oauth_state.encode_state(str(user_id))
    assert oauth_state.decode_state(token) == user_id


def test_token_is_url_safe():
    token = oauth_state.encode_state(uuid.uuid4())
    # No characters that the un-encoded Strava query builder would mangle.
    for bad in ("+", "/", "=", "&", " "):
        assert bad not in token


def test_tampered_signature_returns_none():
    token = oauth_state.encode_state(uuid.uuid4())
    payload, _sig = token.split(".", 1)
    forged = f"{payload}.{'A' * len(_sig)}"
    assert oauth_state.decode_state(forged) is None


def test_tampered_payload_returns_none():
    other = uuid.uuid4()
    token = oauth_state.encode_state(uuid.uuid4())
    _payload, sig = token.split(".", 1)
    # Swap in a different (validly-encoded) payload but keep the old signature.
    forged_payload = oauth_state._b64url_encode(f"{other}.99999999999".encode())
    assert oauth_state.decode_state(f"{forged_payload}.{sig}") is None


def test_expired_token_returns_none():
    token = oauth_state.encode_state(uuid.uuid4(), ttl_seconds=-1)
    assert oauth_state.decode_state(token) is None


def test_malformed_tokens_return_none():
    for bad in ("", "no-dot", "only.", ".only", "a.b.c.d", "@@@.@@@"):
        assert oauth_state.decode_state(bad) is None


def test_non_uuid_payload_returns_none():
    payload = oauth_state._b64url_encode(b"not-a-uuid.99999999999")
    import hashlib
    import hmac

    sig = hmac.new(
        oauth_state._signing_secret(),
        b"not-a-uuid.99999999999",
        hashlib.sha256,
    ).digest()
    token = f"{payload}.{oauth_state._b64url_encode(sig)}"
    assert oauth_state.decode_state(token) is None
