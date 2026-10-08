"""#918: the chat diagram generator refuses a database behind its migrations.

A local database seeded before a migration made the schedule read raise
`UndefinedColumn`, which aborted the Postgres transaction, and every later query
in the capture failed. The per-thread guard swallowed each failure and the run
wrote a capture with most turns' screen context empty, which the drift guard
passed because a uniformly degraded capture is internally consistent.

The generator now checks the database is at the migration head before
capturing, and reports how many screen-asked turns resolved a view, so a
collapse shows in the console rather than only in the blob. Both decisions are
pure functions, testable with no database.
"""

import sys
from pathlib import Path

_DIAGRAMS = Path(__file__).resolve().parents[2] / "docs" / "diagrams"
if str(_DIAGRAMS) not in sys.path:
    sys.path.insert(0, str(_DIAGRAMS))

import generate_chat_flow_data as gen  # noqa: E402


# --- the migration-head check -------------------------------------------------


def test_a_database_at_head_is_captured():
    assert gen.migration_gap({"a4c8"}, {"a4c8"}) is None


def test_a_database_behind_head_is_refused_with_the_command_to_run():
    reason = gen.migration_gap({"old1"}, {"a4c8"})

    assert reason is not None
    assert "old1" in reason and "a4c8" in reason
    assert "alembic upgrade head" in reason


def test_an_unmigrated_database_is_refused():
    assert gen.migration_gap(set(), {"a4c8"}) is not None


# --- the screen-resolution summary --------------------------------------------


def _conv(*turns):
    return {"turns": [{"role": r, "asked_from": a, "screen_view": v} for r, a, v in turns]}


def test_resolution_counts_only_runner_turns_asked_from_a_screen():
    convs = [
        _conv(
            ("user", "schedule", {"label": "Schedule", "view": None}),
            # The assistant row carries no asked_from of its own.
            ("assistant", None, {"label": "Schedule", "view": None}),
            # Asked from no screen at all: legitimately nothing to resolve.
            ("user", None, None),
        ),
        _conv(("user", "activity", None)),
    ]

    res = gen.screen_resolution(convs)

    assert res == {"asked": 2, "resolved": 1, "unresolved_by_screen": {"activity": 1}}


def test_a_wholesale_collapse_is_visible_in_the_counts():
    """The #918 shape: every screen-asked turn came back with no view."""
    convs = [_conv(*[("user", "schedule", None)] * 5)]

    res = gen.screen_resolution(convs)

    assert res["asked"] == 5 and res["resolved"] == 0
    assert res["unresolved_by_screen"] == {"schedule": 5}
