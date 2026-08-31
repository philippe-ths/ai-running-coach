"""Whether an amendment is in flight, and how the last one ended (#1003).

A draft is watchable because it has a row: `training_plans` carries `drafting`,
then `active` or `failed`, and `useDraftStatus` polls it. An amendment has no row
by design - it either replaces the window or leaves the plan exactly as it was,
so there is nothing half-written to record. That made it unwatchable, and a
runner who confirmed one saw nothing change until they reloaded on a hunch.

So the state lives here instead: per runner, in Redis, for minutes rather than
for ever. Redis is the right lifetime. This is not a fact about the plan, it is a
fact about a job that is running right now, and a `training_plans`-shaped row
would outlive its usefulness and need cleaning up after. It already backs the
proposed-action tokens on the same reasoning.

Losing it is survivable, which is why Redis is allowed to be the only copy. If
the key expires or Redis restarts mid-amendment, the runner stops being told what
is happening; the amendment itself still lands, because the job holds the work.
The plan is never what is at stake here, only the watching.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.queue import redis_conn

logger = logging.getLogger(__name__)

# Long enough to outlast a generation that has gone badly - the worker's retries
# plus its own ceiling - and short enough that a stale answer cannot be waiting
# for a runner who comes back tomorrow.
_TTL_SECONDS = 900

WORKING = "working"
DONE = "done"
FAILED = "failed"


def _key(user_id: Any) -> str:
    return f"schedule:amendment:{user_id}"


def _write(user_id: Any, payload: Dict[str, Any]) -> None:
    """Never raises. A watch that cannot be recorded is a worse screen, not a
    worse plan, and the caller is always in the middle of doing the real work."""
    try:
        payload["at"] = datetime.now(timezone.utc).isoformat()
        redis_conn.set(_key(user_id), json.dumps(payload, default=str), ex=_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.exception("amendment watch: could not record state for %s", user_id)


def mark_started(user_id: Any, start: Optional[date], end: Optional[date]) -> None:
    """An amendment has been handed to the worker for this window."""
    _write(user_id, {"status": WORKING, "start": start, "end": end})


def mark_done(
    user_id: Any,
    start: Optional[date],
    end: Optional[date],
    changes: Optional[List[str]] = None,
) -> None:
    """The window has been rewritten. `changes` is what it actually did."""
    _write(
        user_id,
        {"status": DONE, "start": start, "end": end, "changes": list(changes or [])},
    )


def mark_failed(
    user_id: Any, start: Optional[date], end: Optional[date], detail: str = ""
) -> None:
    """Nothing was written, and the plan is as it was."""
    _write(user_id, {"status": FAILED, "start": start, "end": end, "detail": detail})


def current(user_id: Any) -> Optional[Dict[str, Any]]:
    """The runner's in-flight or just-finished amendment, or None.

    None is the honest answer for "nothing to say", and it is what a runner who
    has never amended anything gets. A caller must not read it as "no amendment
    is running": the key expires, and expiry means the watching stopped rather
    than that the work did.
    """
    try:
        raw = redis_conn.get(_key(user_id))
    except Exception:  # noqa: BLE001
        logger.exception("amendment watch: could not read state for %s", user_id)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def clear(user_id: Any) -> None:
    """Stop reporting the last outcome, once a surface has shown it."""
    try:
        redis_conn.delete(_key(user_id))
    except Exception:  # noqa: BLE001
        logger.exception("amendment watch: could not clear state for %s", user_id)
