"""Screen-context resolution (#767, ADR 0028): pointer in, screen view out.

The client names which screen and which selections; this module resolves that
into a screen view using the SAME builders that produced the screen — the
"one builder, used two ways" rule — so there is no parallel per-screen
assembly path. Resolution is owner-scoped like every other read: a pointer
naming another runner's data resolves to nothing.

A screen earns a bespoke view only when it shows something the relationship
baseline does not: parameterised data (Trends) or item-specific data (an
activity page). Load, Home, Activities, and Profile contribute identity only —
the baseline already carries their content, and a second copy under a second
name is the duplication the one-fact-one-place fold exists to prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.schemas.thread import ScreenPointer

logger = logging.getLogger(__name__)

_SCREEN_TITLES = {
    "home": "Home",
    "activities": "Activities",
    "activity": "a run's page",
    "load": "Load",
    "trends": "Trends",
    "profile": "Profile",
    # #830. Identity-only, like Home/Load/Profile: the thread turn's baseline
    # already carries the runner's plan-shaped facts, so resolving the screen
    # would place a second copy of them in front of the coach.
    "schedule": "Schedule",
}


@dataclass
class ScreenView:
    """One resolved screen: the human label plus the (optional) server-derived
    view. `view` is None for identity-only screens; a pointer that fails to
    resolve (cross-user id, unknown row) yields no ScreenView at all."""

    label: str
    view: Optional[dict]


def resolve_screen_view(
    db: Session, owner_user_id, pointer: ScreenPointer
) -> Optional[ScreenView]:
    """Resolve the runner's pointer into a screen view, owner-scoped.

    Selections are inputs; numbers are facts: everything in the returned view
    is recomputed here, never echoed from the client.
    """
    label = _SCREEN_TITLES[pointer.screen]

    if pointer.screen == "activity":
        if pointer.activity_id is None:
            return None
        from app.services.coach.query_tools import get_session_detail

        detail = get_session_detail(
            db, owner_user_id, activity_id=str(pointer.activity_id), today=date.today()
        )
        if detail.get("error"):
            # Cross-user or unknown id: an unresolvable claim gets no voice.
            return None
        return ScreenView(label=label, view=detail)

    if pointer.screen == "trends":
        from app.services.trends import get_volume_report

        range_key = pointer.range or "30D"
        try:
            report = get_volume_report(
                db, owner_user_id, range_key, types=pointer.types
            )
        except Exception:  # noqa: BLE001 — a resolution hiccup never breaks a turn
            logger.exception("trends screen resolution failed")
            return ScreenView(label=f"{label} ({range_key})", view=None)
        view = report.model_dump() if hasattr(report, "model_dump") else dict(report)
        selection = range_key + (f", {', '.join(pointer.types)}" if pointer.types else "")
        return ScreenView(label=f"{label} ({selection})", view=view)

    # Identity-only screens: the coach learns WHERE the runner is, nothing more.
    return ScreenView(label=label, view=None)


def sessions_in_play_from_view(screen_view: Optional[ScreenView]) -> list[dict]:
    """The interval-claim facts (#767 policy re-sourcing) carried by a resolved
    activity view, in the `workout_match` shape rule 4 gates on. A session with
    no detected structure gates as low-confidence: a specific execution claim
    about it cannot be grounded."""
    if screen_view is None or screen_view.view is None:
        return []
    if "activity_id" not in screen_view.view:
        return []
    interval = screen_view.view.get("interval")
    if isinstance(interval, dict):
        return [
            {
                "detection_confidence": interval.get("detection_confidence") or "low",
                "source": interval.get("source"),
            }
        ]
    return [{"detection_confidence": "low"}]
