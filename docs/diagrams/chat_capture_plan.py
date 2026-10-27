"""Whether a chat diagram capture may be written, for the chat diagram generator
(generate_chat_flow_data.py).

Split out of generate_chat_flow_data.py, which imports these back. Pure apart
from _db_migration_gap, so the refusal logic is testable without a database.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
TARGET = Path(__file__).parent / "coach-chat-nodes.js"


def _migration_gap(current: set[str], heads: set[str]) -> str | None:
    """Why a database is not at alembic head, or None if it is. Pure (#918).

    A database behind head does not fail the capture outright: the first query
    touching a missing column aborts the Postgres transaction, every later query
    in it fails, the per-thread catch swallows each failure, and the capture is
    written with most turns resolving no screen. That artifact looks plausible
    and the drift guard passes it, so the check has to happen before capturing.
    """
    if current == heads:
        return None
    return (
        f"the local database is not at this branch's migration head "
        f"(at {sorted(current) or ['nothing']}, head is {sorted(heads)}).\n"
        f"  A capture against it degrades silently: screens resolve to nothing\n"
        f"  while every check stays green.\n"
        f"  Migrate it first: cd backend && alembic upgrade head\n"
        f"  (if it is ahead, from a newer branch, downgrade to the head above)"
    )


def _db_migration_gap(db) -> str | None:
    """Compare the session's database revision against the repository's head."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    current = set(MigrationContext.configure(db.connection()).get_current_heads())
    return _migration_gap(current, heads)

# The two keys that ARE the capture: what one real turn was served, and the
# configuration it was served under. Everything else in the blob is a
# declaration read straight out of the code, which needs no database at all.
# They travel together — a fresh `meta` over a preserved `assembled` would
# describe the capture with a config it was not taken under, which is a subtler
# version of the dishonesty this is fixing.
_CAPTURE_KEYS = ("assembled", "meta")


def _committed_capture(target_text: str) -> dict | None:
    """The capture already in the file, if it holds a real one.

    Read straight off the committed `const CHAT = ...` line rather than through
    the drift guard, so the generator does not import the thing that checks it.
    """
    match = re.search(r"(?m)^const CHAT = (.*);$", target_text)
    if not match:
        return None
    try:
        blob = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not (blob.get("assembled") or {}).get("ok"):
        return None
    return {key: blob[key] for key in _CAPTURE_KEYS if key in blob}


def _screen_resolution_summary(conversations: list[dict]) -> str:
    """How many turns asked from a screen got a view back, as one console line.

    #918: a capture whose screen resolution collapsed is internally consistent,
    so no check can call it wrong. This puts the count where the person who ran
    the generator reads, and in the diff of the summary they paste.
    """
    asked = [
        t for conv in conversations for t in conv.get("turns") or []
        if t.get("role") == "user" and t.get("asked_from")
    ]
    resolved = sum(1 for t in asked if t.get("screen_view"))
    line = f"{resolved} of {len(asked)} runner turns asked from a screen"
    if asked and not resolved:
        line += "  <-- NONE resolved: is the database migrated and seeded?"
    return line


class CaptureRefused(Exception):
    """Raised rather than writing a diagram that shows less than the one it replaces."""


def plan_capture(assembled: dict | None, target_text: str) -> dict:
    """What to do about a run whose capture came back template-only (#870).

    Returns the capture keys to splice into the fresh data — empty when this
    run captured a real turn and nothing needs keeping — and raises
    CaptureRefused when there is neither a fresh capture nor a committed one to
    fall back on.

    Pure, so its own behaviour can be tested without a database, which is the
    condition the whole check is about.
    """
    if (assembled or {}).get("ok"):
        return {}
    reason = (assembled or {}).get("reason")
    if (assembled or {}).get("behind_migrations"):
        # Not the #870 no-database case: the developer has a database and means
        # to capture from it. Keeping the committed capture would print a NOTE
        # and exit 0, which reads as a successful regenerate. Refuse instead.
        raise CaptureRefused(f"refusing to capture: {reason}")
    preserved = _committed_capture(target_text)
    if preserved is None:
        raise CaptureRefused(
            f"refusing to write a template-only capture to {TARGET.name}.\n"
            f"  no turn could be captured: {reason}\n"
            f"  and the committed file holds no real capture to keep.\n"
            f"  Every node would render 'no capture' instead of what the coach was\n"
            f"  really served, which is the diagram's whole promise.\n"
            f"  Seed a local database first: make seed-local"
        )
    print(
        f"NOTE: no turn could be captured ({reason}).\n"
        f"  The declarations below were refreshed; the committed capture and the\n"
        f"  config it was taken under were kept as they were, so nothing the\n"
        f"  diagram shows has been downgraded.\n"
        f"  Run against a seeded local DB (make seed-local) to refresh the capture\n"
        f"  itself."
    )
    return preserved
