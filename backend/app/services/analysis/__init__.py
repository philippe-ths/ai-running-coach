"""Activity analysis pipeline.

Exposes a single external interface: `analyze` (synchronous) for already-stored
activities, and `analyze_with_streams` (async) for the case where streams must
be fetched from Strava before analysis runs.

Everything else in this package is internal.
"""

from app.services.analysis._orchestrator import analyze, analyze_with_streams

__all__ = ["analyze", "analyze_with_streams"]
