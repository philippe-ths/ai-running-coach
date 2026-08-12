"""The schedule: what the runner is doing next (#830).

The product has been entirely retrospective — every surface answers "what did I
do and what did it mean". This package is the forward-facing half.

Module map:

- `disciplines.py` — the one mapping from a logged activity's type to the
  schedule's discipline vocabulary.
- `placement.py`   — placement, effective window and status, all DERIVED from the
  stored `[window_start, window_end]`. Nothing here is a column.
- `rules.py`       — the closed spacing-rule vocabulary, one pure predicate each,
  and the checker that makes a violation detectable.
- `store.py`       — the owner-scoped reads. Every query takes a required
  `user_id`; there is no unscoped read of a plan or a session.
- `week.py`        — the week read, planned and free.
- `horizon.py`     — the multi-week shape read.

Nothing in this package computes a training total of its own. Actuals, windows
and norms all come from `activity_facts`, `weeks` and `coach/volume.py`, because
the schedule must not become a second opinion about a week the Trends page has
already answered.
"""
