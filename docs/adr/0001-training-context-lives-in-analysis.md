# Training context lives in the analysis module and persists on DerivedMetric

The "training context" signal (last-7-day intensity distribution, days since last hard session, hard count this week) was previously computed in `app/services/coach/context.py` as a private helper, yet consumed by `app/services/processing/engine.py` for risk scoring via a cross-module import of a private function. That made the analysis pipeline depend on a private helper inside the coach pipeline — a real leak, not a hypothetical one.

We treat training context as a derived signal about a run. It moves into the analysis module, its output is persisted on `DerivedMetric` (new `training_context` column), and the coach context builder reads it from there rather than recomputing.

## Consequences

- Requires an alembic migration adding `training_context` to `derived_metric`.
- Re-hashes the coach context pack (the field appears under `metrics.training_context` rather than as a top-level `training_context` key), invalidating cached `CoachReport` rows for prior activities. Reports regenerate on next access.
- Future architecture reviews should not re-suggest splitting this back out into a shared module: the persistence is the point.
