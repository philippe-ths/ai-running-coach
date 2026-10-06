"""Strava best efforts: the per-run fastest segments, and Strava's PB rank on each (#1032).

Strava's DETAILED activity payload carries ``best_efforts``: one entry per standard
distance the run covered (400m up to Marathon), each with its ``elapsed_time`` and a
``pr_rank`` of 1, 2 or 3 when that effort is the runner's fastest, second or third
fastest across their WHOLE Strava history. The rank is the trustworthy signal: our
own store only holds best efforts for activities ingested in detail, so it cannot
see every earlier effort Strava ranked this one against.

Pure: no I/O. The coach's notable-activity read owns the query for earlier efforts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Effort:
    """One best effort on one activity."""

    name: str  # Strava's label: "5K", "Half-Marathon", "1 mile", ...
    distance_m: float
    elapsed_time_s: int
    pr_rank: Optional[int]  # 1/2/3 = fastest/2nd/3rd in the runner's Strava history


def efforts(raw_summary: Optional[dict]) -> List[Effort]:
    """The activity's best efforts, shortest first. Empty for a summary payload.

    `raw_summary` is untyped Strava JSON, so a malformed entry is skipped rather
    than raised on.
    """
    out: List[Effort] = []
    for entry in (raw_summary or {}).get("best_efforts") or ():
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        distance = entry.get("distance")
        elapsed = entry.get("elapsed_time")
        if not name or not isinstance(distance, (int, float)) or not isinstance(elapsed, (int, float)):
            continue
        rank = entry.get("pr_rank")
        out.append(
            Effort(
                name=str(name),
                distance_m=float(distance),
                elapsed_time_s=int(elapsed),
                pr_rank=rank if rank in (1, 2, 3) else None,
            )
        )
    return sorted(out, key=lambda e: e.distance_m)


def merge_preserved_best_efforts(
    existing_raw_summary: dict | None, incoming_raw: dict
) -> dict:
    """Preserve stored best efforts across a summary-only re-sync (#1032).

    The detail endpoint (webhook, self-heal) returns ``best_efforts``; the summary
    list endpoint (manual "Sync Now", the import) does not. Without this a routine
    sync overwrites them, and the coach loses the run's personal bests. A fresh
    payload that carries its own best efforts still wins. Same rule as
    `analysis.intervals.merge_preserved_laps`.
    """
    if (
        not incoming_raw.get("best_efforts")
        and isinstance(existing_raw_summary, dict)
        and existing_raw_summary.get("best_efforts")
    ):
        return {**incoming_raw, "best_efforts": existing_raw_summary["best_efforts"]}
    return incoming_raw
