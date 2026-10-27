"""Re-running a captured conversation's tool calls and policy floor, for the chat
diagram generator (generate_chat_flow_data.py).

Split out of generate_chat_flow_data.py, whose docstring describes the capture;
it imports these back. Needs the backend importable, which that file arranges.
"""
from __future__ import annotations

from app.services.coach import query_tools

# --- the real-turn capture (needs a seeded local DB) ----------------------------

# The trace's short window label back to the window enum, so the tools can be
# re-executed over the SAME windows the captured turn actually fetched.
_WINDOW_BY_TRACE_LABEL = {v: k for k, v in query_tools.WINDOW_TRACE_LABELS.items()}


def _pick_turn(db):
    """The richest REAL assistant turn to trace through the diagram.

    Prefers a turn whose `tools_used` is the modern trace shape (a list of
    {tool,label,detail,count} dicts, #664) over the older bare-name list, then an
    anchored thread, then a substantial reply — so the captured turn exercises as
    much of the graph as the seeded data allows.
    """
    from app.models.coach_chat_message import CoachChatMessage
    from app.models.thread import Thread

    best, best_score = None, -1
    for row in db.query(CoachChatMessage).filter(CoachChatMessage.role == "assistant").all():
        tools = row.tools_used if isinstance(row.tools_used, list) else []
        skills = row.skills_used if isinstance(row.skills_used, list) else []
        thread = db.query(Thread).filter(Thread.id == row.thread_id).first()
        if thread is None:
            continue
        modern = sum(1 for t in tools if isinstance(t, dict))
        score = (
            modern * 6                       # a real, renderable tool trace
            + len(skills) * 8                # a loaded procedure is rarer still
            + (4 if thread.activity_id else 0)
            + min(len(row.content or "") // 300, 4)
        )
        if score > best_score:
            best, best_score = (row, thread), score
    return best


def _turn_facts(db, row, thread):
    """The stored turn, as it really is: the question, the reply, the trace."""
    from app.models.coach_chat_message import CoachChatMessage

    question = (
        db.query(CoachChatMessage)
        .filter(
            CoachChatMessage.thread_id == thread.id,
            CoachChatMessage.role == "user",
            CoachChatMessage.created_at <= row.created_at,
        )
        .order_by(CoachChatMessage.created_at.desc())
        .first()
    )
    return {
        "thread_id": str(thread.id),
        "thread_title": thread.title,
        "anchored": thread.activity_id is not None,
        "asked_from": row.asked_from,
        "asked_at": str(row.created_at),
        "question": question.content if question else None,
        "reply": row.content,
        "reply_chars": len(row.content or ""),
        "tools_used": row.tools_used,
        "skills_used": row.skills_used,
    }


def _sessions_on(db, user_id, day: str):
    """The runner's activities whose LOCAL day is `day`, the same definition
    get_session_detail stamps into the trace (`local_start.date()`).

    `local_start` is a Python property (start_date_local, falling back to the UTC
    start_date), not a column, and the model is explicit that it must never drive
    ordering or windowing. So the query bounds a small UTC window and the local-day
    match happens in Python, which is where that definition lives.
    """
    from datetime import date as _date, datetime, timedelta, timezone

    from app.models.activity import Activity

    try:
        parsed = _date.fromisoformat(day)
    except (TypeError, ValueError):
        return []
    lo = datetime.combine(parsed - timedelta(days=1), datetime.min.time(), timezone.utc)
    hi = datetime.combine(parsed + timedelta(days=2), datetime.min.time(), timezone.utc)
    rows = (
        db.query(Activity)
        .filter(
            Activity.user_id == user_id,
            Activity.is_deleted.is_(False),
            Activity.start_date >= lo,
            Activity.start_date < hi,
        )
        .order_by(Activity.start_date.asc())
        .all()
    )
    return [a for a in rows if a.local_start.date() == parsed]


def _fill_violations(conv, chat_mod_ref) -> None:
    """Re-run the REAL policy floor over each stored reply, in place.

    Only rule 5's outcome is inferable from a stored row (its redirect is
    byte-identical to the canned message). Rules 2 and 4 are TOLERATED — logged
    and let through — so they leave no trace on the row and have to be recomputed
    to be shown at all. Sessions in play are rebuilt from this turn's own re-run
    session-detail results plus the screen view, the same two sources
    thread_turn.py feeds the validator. Runs after the re-runs exist, because it
    needs their results.
    """
    runs = conv.get("tool_runs") or {}
    floor = conv.get("floor") or {}
    view_sessions = floor.get("view_sessions") or []
    for turn in conv.get("turns") or []:
        if turn.get("role") != "assistant":
            turn["violations"] = []
            continue
        sessions = list(view_sessions)
        for call in turn.get("tools_used") or []:
            if not isinstance(call, dict) or call.get("tool") != "get_session_detail":
                continue
            res = (runs.get(f"get_session_detail|{call.get('detail')}") or {}).get("result") or {}
            if not res.get("error"):
                sessions += chat_mod_ref.sessions_in_play_from_tool_results(
                    [("get_session_detail", res)]
                )
        turn["violations"] = [
            v.rule
            for v in chat_mod_ref._validate_conversational_text(
                turn.pop("_full", "") or turn.get("content") or "",
                zones_calibrated=bool(floor.get("zones_calibrated")),
                sessions_in_play=sessions,
            )
        ]
        turn["sessions_in_play"] = len(sessions)
    for turn in conv.get("turns") or []:
        turn.pop("_full", None)


def _run_recorded_calls(db, user_id, thread, turns) -> dict:
    """Re-execute every DISTINCT call this conversation recorded, keyed so each
    turn's node shows the result of ITS OWN call.

    The two window tools key on the window they resolved. `get_session_detail`
    does not: its trace `detail` is the session's local DATE (query_tools
    .summarize_tool_call), so it keys on that and resolves the date back to the
    activity. Keying it by window instead collapsed every session-detail call in
    a conversation into one bucket and re-ran it against the THREAD ANCHOR, which
    showed one session's data under every call — an anchor the coach may never
    have fetched. A date the record cannot resolve to exactly one session says
    so rather than substituting a neighbour.
    """
    from datetime import date

    runs: dict = {}
    seen = set()
    for turn in turns:
        for call in turn.get("tools_used") or []:
            if not isinstance(call, dict):
                continue
            tool = call.get("tool")
            detail = call.get("detail")
            if tool == "get_session_detail":
                key = f"{tool}|{detail}"
            else:
                key = f"{tool}|" + (
                    _WINDOW_BY_TRACE_LABEL.get(detail) or "last_30_days"
                )
            if key in seen:
                continue
            seen.add(key)
            win = key.split("|", 1)[1]
            try:
                if tool == "list_activities_in_range":
                    res = query_tools.list_activities_in_range(
                        db, user_id, window=win, type_filter=None, today=date.today())
                elif tool == "get_training_summary":
                    res = query_tools.get_training_summary(
                        db, user_id, window=win, type_filter=None, today=date.today())
                elif tool == "get_session_detail":
                    matches = _sessions_on(db, user_id, detail)
                    if len(matches) == 1:
                        res = query_tools.get_session_detail(
                            db, user_id, activity_id=str(matches[0].id),
                            today=date.today())
                    elif not matches:
                        res = {"error": "no session on that date in the record"}
                    else:
                        res = {
                            "error": "the trace records only the date, and this "
                                     "runner logged more than one session that day",
                            "candidates": [
                                {"activity_id": str(a.id), "name": a.name,
                                 "type": a.type, "local_start": str(a.local_start)}
                                for a in matches
                            ],
                        }
                else:
                    continue
            except Exception as exc:  # noqa: BLE001
                res = {"error": str(exc)}
            runs[key] = {"tool": tool, "window": win, "result": res,
                         "rederived_at": str(date.today())}
    return runs


def _run_tools(db, user_id, thread, trace) -> dict:
    """Execute the three data tools FOR REAL, over the windows the captured turn
    used where the trace records them. They are deterministic owner-scoped reads,
    so re-running them now reproduces what the coach saw — no model call, no cost.
    """
    from datetime import date

    windows = [
        _WINDOW_BY_TRACE_LABEL.get((t or {}).get("detail"))
        for t in (trace or [])
        if isinstance(t, dict)
    ]
    windows = [w for w in windows if w] or ["last_30_days"]
    win = windows[0]

    # What the captured turn's trace RECORDED at the time, so the diagram can show
    # the re-derived result honestly beside it. A named window resolves relative to
    # TODAY, so re-running it later legitimately returns a different set — that is
    # the windows working as designed, not a mismatch to paper over.
    out = {
        "window_used": win,
        "window_from_trace": win in windows,
        # EVERY recorded call, not a per-tool collapse: one conversation can call
        # the same tool twice over the same window with different type filters (the
        # trace records the window but NOT the filter), and the counts then differ
        # for a reason that has nothing to do with the window.
        "trace_calls": [
            {"tool": (t or {}).get("tool"), "detail": (t or {}).get("detail"),
             "count": (t or {}).get("count")}
            for t in (trace or [])
            if isinstance(t, dict)
        ],
        "rederived_at": str(date.today()),
    }
    try:
        out["list_activities_in_range"] = query_tools.list_activities_in_range(
            db, user_id, window=win, type_filter=None, today=date.today()
        )
    except Exception as exc:  # noqa: BLE001
        out["list_activities_in_range"] = {"error": str(exc)}
    try:
        out["get_training_summary"] = query_tools.get_training_summary(
            db, user_id, window=win, type_filter=None, today=date.today()
        )
    except Exception as exc:  # noqa: BLE001
        out["get_training_summary"] = {"error": str(exc)}
    # get_session_detail needs one activity: the thread's anchor, else the newest
    # session the list call just returned.
    target = thread.activity_id
    if target is None:
        listed = (out.get("list_activities_in_range") or {}).get("activities") or []
        target = listed[0]["activity_id"] if listed else None
    if target is not None:
        try:
            out["get_session_detail"] = query_tools.get_session_detail(
                db, user_id, activity_id=str(target), today=date.today()
            )
        except Exception as exc:  # noqa: BLE001
            out["get_session_detail"] = {"error": str(exc)}
    return out
