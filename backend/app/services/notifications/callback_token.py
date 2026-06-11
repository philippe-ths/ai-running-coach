"""Opaque token codec for Telegram inline-keyboard taps (I1b, #220).

A tap echoes back a self-contained token (Telegram's `callback_data`, capped at
64 bytes), so the inbound webhook can resolve the action with no server-side
state. The token carries the three facts a tap needs to become a CheckIn: which
question kind was answered (`rpe`/`pain`), which activity, and the chosen value.

One module owns both ends — `encode` (outbound composer) and `decode` (inbound
webhook) — so the wire format cannot drift between them.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

# Versioned prefix so a future format change is detectable rather than silently
# misparsed. Pipe is a safe separator: it appears in neither a UUID nor an int.
_PREFIX = "cb1"
_SEP = "|"

# Only the I1a kinds are tappable-to-CheckIn (matches the in-app path); reply/
# dispute/custom stay non-interactive (deferred to I2).
_FIELD_BY_KIND = {"rpe": "rpe", "pain": "pain_score"}

# Telegram hard-caps callback_data at 64 bytes.
_MAX_BYTES = 64


class CallbackAction(NamedTuple):
    kind: str          # "rpe" | "pain"
    activity_id: str   # activity UUID as string
    value: int         # the chosen RPE/pain value
    field: str         # the CheckIn column the value writes to


def encode(*, kind: str, activity_id: str, value: int) -> str:
    """Build a callback token for an RPE/pain option tap.

    Raises ValueError for an unsupported kind or a token that would exceed
    Telegram's 64-byte limit, so an over-long token fails at build time (a bug)
    rather than being silently truncated on the wire (a security/correctness
    hazard at decode).
    """
    if kind not in _FIELD_BY_KIND:
        raise ValueError(f"non-tappable callback kind: {kind!r}")
    token = _SEP.join((_PREFIX, kind, activity_id, str(int(value))))
    if len(token.encode("utf-8")) > _MAX_BYTES:
        raise ValueError(f"callback token exceeds {_MAX_BYTES} bytes: {token!r}")
    return token


def decode(token: str) -> Optional[CallbackAction]:
    """Parse a callback token back into an action, or None if it is malformed.

    Returns None (never raises) for any unrecognised, wrong-arity, wrong-prefix,
    unknown-kind, or non-integer-value token, so the inbound webhook treats a
    garbage callback as a clean no-op rather than a 500."""
    if not token:
        return None
    parts = token.split(_SEP)
    if len(parts) != 4:
        return None
    prefix, kind, activity_id, raw_value = parts
    if prefix != _PREFIX:
        return None
    field = _FIELD_BY_KIND.get(kind)
    if field is None or not activity_id:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return CallbackAction(kind=kind, activity_id=activity_id, value=value, field=field)
