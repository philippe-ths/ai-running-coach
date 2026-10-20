"""Status labels, trace labels and the tool-call trace summary for the chat tools.

Split out of `query_tools.py` (#1102); `query_tools` re-imports every name here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.coach import training_metrics


# The status label shown in chat while each tool runs (the ephemeral "fetching"
# affordance, #648). Coach-framed, so it reads as a competent coach checking the
# record rather than a spinner.
TOOL_STATUS_LABELS = {
    "list_activities_in_range": "Checking your training history…",
    "get_session_detail": "Pulling up that session…",
    "get_training_summary": "Tallying your recent training…",
    "get_training_plan": "Reading your training plan…",
    "get_personal_bests": "Looking up your personal bests…",
    "get_training_metric": "Measuring your training…",
}


# The past-tense verb shown on the PERSISTENT trace of a finished turn (#664, the
# #663 follow-up). Owned here alongside the present-tense spinner labels so the two
# tenses live in ONE module instead of being duplicated across the backend spinner
# and the frontend trace (the #663 friendly-label duplication). An unknown key falls
# back to a generic phrasing so a new tool never renders a raw identifier.
TOOL_TRACE_LABELS = {
    "list_activities_in_range": "Looked up your training history",
    "get_session_detail": "Pulled up a past session",
    "get_training_summary": "Tallied your recent training",
    "get_training_plan": "Read your training plan",
    "get_personal_bests": "Looked up your personal bests",
    "get_training_metric": "Measured your training",
    # The server-side web search (#1051). Its detail is a result count, server-derived.
    "web_search": "Searched the web",
}
_DEFAULT_TRACE_LABEL = "Looked up your training data"


# Compact, human-readable renderings of each named window, for the trace's `detail`
# (#664). These are SHORT ("last 30 days") rather than the tool result's verbose
# `window.label` ("the last 30 days (2026-06-21 to 2026-07-21)"), which is meant for
# the coach to reason over. Keyed by the window enum value the model supplied, so the
# label is server-derived from a server-known token, never from model prose.
WINDOW_TRACE_LABELS = {
    "last_7_days": "last 7 days",
    "last_14_days": "last 14 days",
    "last_30_days": "last 30 days",
    "last_90_days": "last 90 days",
    "last_180_days": "last 180 days",
    "last_365_days": "last year",
    "this_week": "this week",
    "last_week": "last week",
    "this_month": "this month",
    "last_month": "last month",
    "this_year": "this year",
    "all_time": "all time",
    "last_4_weeks": "last 4 weeks",
    "last_8_weeks": "last 8 weeks",
    "last_12_weeks": "last 12 weeks",
}


# How each modality filter reads in a trace (#886). Keyed by the same tokens
# `_TYPE_FILTER` accepts, so the chip is server-derived from a server-known
# value: a `type_filter` the tool did not recognise renders as nothing rather
# than putting the model's own word in front of the runner.
TYPE_FILTER_TRACE_LABELS = {
    "run": "runs",
    "ride": "rides",
    "strength": "strength sessions",
    "swim": "swims",
    "walk": "walks",
}


def trace_label(name: Optional[str]) -> str:
    """The past-tense trace verb for a tool name (or the generic fallback)."""
    return TOOL_TRACE_LABELS.get(name or "", _DEFAULT_TRACE_LABEL)


def _window_detail(tool_input: Dict[str, Any]) -> Optional[str]:
    """What was looked at: the window, and WHICH sessions in it (#886).

    The filter used to be dropped, so two lookups over the same window returned
    contradictory counts under identical chips — "last 30 days (17 sessions)"
    and "last 30 days (55 sessions)" on the same day, one runs-only and one
    everything. Both were right. The chip exists so the runner can sanity-check
    what the coach read, and two different questions that look like one question
    do the opposite of that.
    """
    window = WINDOW_TRACE_LABELS.get(tool_input.get("window"))
    if window is None:
        return None
    modality = TYPE_FILTER_TRACE_LABELS.get(tool_input.get("type_filter"))
    return f"{modality}, {window}" if modality else window


def summarize_tool_call(
    name: Optional[str], tool_input: Optional[Dict[str, Any]], result: Optional[dict]
) -> dict:
    """Derive one compact, safe trace record from a completed tool call (#664).

    Produces `{tool, label, detail, count}` describing WHAT was fetched — the resolved
    window and a result count — so the runner can sanity-check the data the coach
    reasoned over instead of seeing only that a tool ran. Every field is SERVER-derived
    (the past-tense verb, the window enum humanised, counts computed by the tool); no
    model-authored prose enters the trace, so it needs no policy gate (it is not the
    coach's reply). One record per tool CALL, so a genuine multi-window turn no longer
    collapses to a single chip. Never raises: a malformed or error result degrades to a
    label-only record."""
    tool_input = tool_input or {}
    result = result or {}
    entry: dict = {"tool": name or "", "label": trace_label(name), "detail": None, "count": None}

    def _int(value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if name == "get_training_metric":
        detail = _window_detail(tool_input)
        what = training_metrics.BY_KEY.get(tool_input.get("metric"))
        if what is not None and what.key == "zone_time_h":
            noun = f"zone {result.get('min_zone', 2)}+ time"
        else:
            noun = what.description if what is not None else None
        entry["detail"] = f"{noun}, {detail}" if noun and detail else detail
        entry["count"] = _int((result.get("whole_window") or {}).get("sessions"))
    elif name in ("list_activities_in_range", "get_training_summary"):
        entry["detail"] = _window_detail(tool_input)
        if name == "list_activities_in_range":
            entry["count"] = _int(result.get("count"))
        else:  # get_training_summary: the session total is the natural count
            entry["count"] = _int((result.get("totals") or {}).get("sessions"))
    elif name == "get_session_detail":
        # A single named session: its local date is the useful "what", no count.
        d = result.get("date")
        entry["detail"] = d if isinstance(d, str) else None
    elif name == "get_training_plan":
        # The block, not a window: what the runner can check is how far ahead the
        # coach actually read, and how many of those weeks hold real sessions.
        # The unit here is WEEKS, and the client renders a bare `count` as
        # "sessions", so the number goes in the detail where it can carry its
        # own noun. A chip reading "7 sessions" for a seven-WEEK block is the
        # kind of small false number this trace exists to prevent.
        through = result.get("plan_covers_through_week_starting")
        weeks = _int(result.get("week_count"))
        parts = []
        if weeks:
            parts.append(f"{weeks} week{'s' if weeks != 1 else ''}")
        if isinstance(through, str):
            parts.append(f"to {through}")
        entry["detail"] = ", ".join(parts) or None
    elif name == "get_personal_bests":
        # The distances the coach actually saw, in the detail for the same reason
        # as the plan: a bare count renders as "sessions".
        from app.services.personal_bests import DISPLAY_NAMES

        found = [
            DISPLAY_NAMES.get(p.get("distance"), p.get("distance"))
            for p in (result.get("personal_bests") or [])
            if isinstance(p, dict) and isinstance(p.get("distance"), str)
        ]
        entry["detail"] = ", ".join(found) or "none on record"
    return entry
