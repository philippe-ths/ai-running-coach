"""Activity analysis pipeline.

Exposes a single external interface: `analyze` (synchronous) for already-stored
activities, and `analyze_with_streams` (async) for the case where streams must
be fetched from Strava before analysis runs. Bulk callers pass
`skip_baseline=True` and call `recompute_baselines_after_batch` once.

Everything else in this package is internal.
"""

from app.services.analysis._orchestrator import (
    analyze,
    analyze_with_streams,
    recompute_baselines_after_batch,
)

__all__ = ["analyze", "analyze_with_streams", "recompute_baselines_after_batch"]
