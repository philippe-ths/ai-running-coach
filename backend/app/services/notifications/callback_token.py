"""Opaque token codec for Telegram inline-keyboard taps (I1b, #220; #296 done).

A tap echoes back a self-contained token (Telegram's `callback_data`, capped at
64 bytes), so the inbound webhook can resolve the action with no server-side
state. Two token shapes share one versioned prefix:

- an RPE/pain tap -> `cb1|<kind>|<activity_id>|<value>`: the facts a tap needs to
  become a CheckIn (which question kind, which activity, the chosen value);
- a "done" tap (#296 receipt) -> `cb1|done|<activity_id>`: a control signal with
  no CheckIn field/value, telling the coach the session is finished.

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

# The #296 "done" tap: a value-less control signal (the runner is finished), not
# a CheckIn. Carries only the activity id so the webhook can find the block.
_DONE_KIND = "done"

# Telegram hard-caps callback_data at 64 bytes.
_MAX_BYTES = 64


class CallbackAction(NamedTuple):
    kind: str                 # "rpe" | "pain" | "done"
    activity_id: str          # activity UUID as string
    value: Optional[int]      # the chosen RPE/pain value; None for "done"
    field: Optional[str]      # the CheckIn column the value writes to; None for "done"


def encode(*, kind: str, activity_id: str, value: Optional[int] = None) -> str:
    """Build a callback token for an RPE/pain tap, or a value-less "done" tap.

    Raises ValueError for an unsupported kind or a token that would exceed
    Telegram's 64-byte limit, so an over-long token fails at build time (a bug)
    rather than being silently truncated on the wire (a security/correctness
    hazard at decode).
    """
    if kind == _DONE_KIND:
        token = _SEP.join((_PREFIX, kind, activity_id))
    elif kind in _FIELD_BY_KIND:
        token = _SEP.join((_PREFIX, kind, activity_id, str(int(value))))
    else:
        raise ValueError(f"non-tappable callback kind: {kind!r}")
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
    if len(parts) < 3:
        return None
    if parts[0] != _PREFIX:
        return None
    kind, activity_id = parts[1], parts[2]
    if not activity_id:
        return None
    if kind == _DONE_KIND:
        if len(parts) != 3:
            return None
        return CallbackAction(kind=kind, activity_id=activity_id, value=None, field=None)
    field = _FIELD_BY_KIND.get(kind)
    if field is None or len(parts) != 4:
        return None
    try:
        value = int(parts[3])
    except ValueError:
        return None
    return CallbackAction(kind=kind, activity_id=activity_id, value=value, field=field)
