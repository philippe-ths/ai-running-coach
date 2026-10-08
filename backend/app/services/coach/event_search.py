"""The coach's web search for races and events (#1051).

Anthropic's server-side `web_search` tool, offered in a thread turn only. The API
runs the search and hands the model the results; nothing here fetches a page.

Search results are UNTRUSTED input, the first live web content the coach reads.
The containment is the one the runner's uploaded materials get (ADR 0017): the
model may cite what it found, but the only way a result becomes a write is a
structured `add_goal` offer, validated against the races API's own envelope and
confirmed by the runner. Page text never reaches a code path that acts on it.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.config import settings

# Per API request. Searches are billed per query, so this is the cost ceiling of
# one model round; a turn has few rounds and most searches happen in one.
MAX_SEARCHES_PER_ROUND = 3

# The basic, direct-call version. The newer ones route through code execution for
# dynamic filtering, which a handful of event listings does not need.
WEB_SEARCH_TOOL: Dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": MAX_SEARCHES_PER_ROUND,
}

# Tools the API runs itself: the tool loop never dispatches these.
SERVER_TOOL_NAMES = frozenset({"web_search"})


def event_search_tools() -> List[Dict[str, Any]]:
    """The search tool when the kill switch allows it, else nothing."""
    return [dict(WEB_SEARCH_TOOL)] if settings.COACH_EVENT_SEARCH_ENABLED else []
