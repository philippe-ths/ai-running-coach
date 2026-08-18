"""Seed the local database from a production snapshot.

Why this exists
---------------
There is no Strava OAuth link for local/dev, so the usual "connect Strava ->
sync" path cannot bootstrap data outside production. But Strava is only a port:
once ``Activity`` / ``ActivityStream`` rows exist locally, the whole
analyze -> coach -> frontend chain runs with zero Strava dependency. This script
copies a bounded, real snapshot from a source (production) database into the
local one so that local testing exercises real data.

Safety
------
- Strava OAuth tokens are REDACTED by default (``--with-live-tokens`` opts in).
  This keeps live secrets out of the local working tree and, crucially, stops a
  local environment from refreshing -- and thereby rotating/invalidating -- the
  production refresh token.
- The script only ever READS from the source and only WRITES to the target. The
  target schema is reset to this branch's migration head before loading so the
  copy is deterministic regardless of the local DB's prior state.

Usage
-----
    SEED_SOURCE_URL=postgresql://... python scripts/seed_from_prod.py
    python scripts/seed_from_prod.py --source-url postgresql://... --activities 20
    python scripts/seed_from_prod.py --with-live-tokens   # only when testing real sync

The target defaults to ``settings.DATABASE_URL`` (the local app DB). Prefer the
``make seed-local`` wrapper, which injects the source URL from Railway.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import bindparam, create_engine, insert, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine

# Make the backend package importable when run as a bare script.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Activity,
    ActivityStream,
    Block,
    CheckIn,
    CoachChatMessage,
    CoachReport,
    CoachingRelationship,
    DerivedMetric,
    Exchange,
    GoalRace,
    PlannedSession,
    RunnerBaseline,
    RunnerMemory,
    StravaAccount,
    Thread,
    TrainingPlan,
    User,
    UserProfile,
)

# Models in foreign-key dependency order. Load top-to-bottom; wipe bottom-to-top.
USER_MODELS = [
    User,
    UserProfile,
    StravaAccount,
    RunnerBaseline,
    RunnerMemory,
    CoachingRelationship,
    # The schedule's user-scoped half (#830). A goal race belongs to the runner
    # alone; a plan points at one optionally, so the race must land first.
    GoalRace,
    TrainingPlan,
]
# activities.block_id <-> blocks.primary_activity_id is circular (A1), so
# activities are inserted with block_id nulled, blocks/exchanges follow, and
# block_id is backfilled last (see seed()).
# PlannedSession sits here rather than with its plan because it can point at an
# activity (the run that completed it), so it must land after activities do.
ACTIVITY_MODELS = [Activity, Block, Exchange, ActivityStream, DerivedMetric, CoachReport, Thread, CoachChatMessage, CheckIn, PlannedSession]
ORDERED_MODELS = USER_MODELS + ACTIVITY_MODELS

# Tables keyed off activity_id, filtered when --activities limits the snapshot.
# Thread and CoachChatMessage are filtered separately (#765): a thread's
# activity anchor is optional, and a thread-only message carries no activity_id.
ACTIVITY_CHILD_MODELS = [ActivityStream, DerivedMetric, CoachReport, CheckIn]

REDACTED_TOKEN = "dev-redacted-not-a-real-token"
INSERT_CHUNK = 500


def _normalise_driver(url: str) -> str:
    """Mirror Settings._ensure_psycopg_driver so raw provider URLs work here."""
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def _read_rows(engine: Engine, model) -> list[dict]:
    """Read all rows, selecting only the columns the SOURCE actually has.

    A branch that adds a model column would otherwise break seeding until prod
    has run the matching migration. Columns missing on the source are simply
    absent from the copied dicts and take their local defaults (they are new,
    so they must be nullable/defaulted for prod's own deploy anyway).
    """
    with engine.connect() as conn:
        inspector = sa_inspect(conn)
        if not inspector.has_table(model.__tablename__):
            # A branch that adds a whole table (e.g. coach_threads, #765) seeds
            # cleanly from a prod that has not run the migration yet: the table
            # simply arrives empty and the local upgrade path backfills it.
            return []
        source_cols = {c["name"] for c in inspector.get_columns(model.__tablename__)}
        cols = [c for c in model.__table__.columns if c.name in source_cols]
        return [dict(r) for r in conn.execute(select(*cols)).mappings()]


def _reset_target_schema(target_url: str) -> None:
    """Drop and rebuild the local schema at this branch's migration head.

    A plain ``alembic upgrade head`` can fail when the local DB is stamped at a
    revision that does not exist on the current branch (e.g. after switching off
    a feature branch). Dropping the schema removes the stale alembic_version, so
    the subsequent upgrade builds everything cleanly.
    """
    print("Resetting target schema (DROP SCHEMA public CASCADE; CREATE SCHEMA public)...")
    engine = create_engine(target_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    print("Running 'alembic upgrade head' against the target...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit("alembic upgrade head failed; aborting before any data load.")


def _select_activity_ids(rows: list[dict], limit: int) -> set:
    """Most-recent ``limit`` activity ids by start_date (0 = keep all)."""
    if limit <= 0:
        return {r["id"] for r in rows}
    ordered = sorted(rows, key=lambda r: r["start_date"], reverse=True)
    return {r["id"] for r in ordered[:limit]}


def _insert_rows(engine: Engine, model, rows: list[dict]) -> int:
    if not rows:
        return 0
    with engine.begin() as conn:
        for start in range(0, len(rows), INSERT_CHUNK):
            conn.execute(insert(model.__table__), rows[start : start + INSERT_CHUNK])
    return len(rows)


def seed(source_url: str, target_url: str, activities_limit: int, with_live_tokens: bool) -> None:
    source = create_engine(_normalise_driver(source_url))
    target = create_engine(_normalise_driver(target_url))

    # Pull everything up front so we can compute the activity-id filter before
    # touching the target.
    snapshot: dict = {model: _read_rows(source, model) for model in ORDERED_MODELS}

    keep_activity_ids = _select_activity_ids(snapshot[Activity], activities_limit)
    snapshot[Activity] = [r for r in snapshot[Activity] if r["id"] in keep_activity_ids]
    for model in ACTIVITY_CHILD_MODELS:
        snapshot[model] = [r for r in snapshot[model] if r["activity_id"] in keep_activity_ids]

    # #765: a thread survives when unanchored or anchored to a kept activity; a
    # message survives via its kept activity, or (thread-only rows, no
    # activity_id) via its kept thread.
    snapshot[Thread] = [
        r
        for r in snapshot[Thread]
        if r.get("activity_id") is None or r["activity_id"] in keep_activity_ids
    ]
    keep_thread_ids = {r["id"] for r in snapshot[Thread]}
    snapshot[CoachChatMessage] = [
        r
        for r in snapshot[CoachChatMessage]
        if r.get("activity_id") in keep_activity_ids
        or (r.get("activity_id") is None and r.get("thread_id") in keep_thread_ids)
    ]

    # #830: a planned session is NOT a child of an activity -- it exists whether
    # or not a run ever completed it -- so it is never dropped by the window.
    # What can dangle is the completion pointer, which is nullable. Two ways to
    # get that wrong, both taken deliberately here:
    #   - Filtering the row out (the ACTIVITY_CHILD_MODELS treatment) would
    #     delete every uncompleted session, since those carry no activity_id at
    #     all, and would tear a completed one out of its plan's schedule.
    #   - Clearing the whole completion would say the runner never did it.
    # So the row and its completion survive and only the LINK is dropped, the
    # same call the Thread anchor above makes: the session was completed, by a
    # run that fell outside the window this snapshot asked for.
    for row in snapshot[PlannedSession]:
        if row.get("completed_activity_id") not in keep_activity_ids:
            row["completed_activity_id"] = None

    # Blocks survive only when their primary activity does; exchanges follow
    # their block. Activities are inserted with block_id nulled (circular FK
    # with blocks.primary_activity_id) and backfilled after blocks land.
    snapshot[Block] = [
        r for r in snapshot[Block] if r["primary_activity_id"] in keep_activity_ids
    ]
    keep_block_ids = {r["id"] for r in snapshot[Block]}
    snapshot[Exchange] = [r for r in snapshot[Exchange] if r["block_id"] in keep_block_ids]

    block_assignments = {
        r["id"]: r["block_id"]
        for r in snapshot[Activity]
        if r.get("block_id") in keep_block_ids
    }
    for row in snapshot[Activity]:
        row["block_id"] = None

    if not with_live_tokens:
        for row in snapshot[StravaAccount]:
            row["access_token"] = REDACTED_TOKEN
            row["refresh_token"] = REDACTED_TOKEN

    print(f"\nLoading into target (tokens {'LIVE' if with_live_tokens else 'redacted'}):")
    for model in ORDERED_MODELS:
        count = _insert_rows(target, model, snapshot[model])
        print(f"  {model.__tablename__:<22} {count}")

    if block_assignments:
        stmt = (
            Activity.__table__.update()
            .where(Activity.__table__.c.id == bindparam("aid"))
            .values(block_id=bindparam("bid"))
        )
        with target.begin() as conn:
            conn.execute(
                stmt,
                [{"aid": aid, "bid": bid} for aid, bid in block_assignments.items()],
            )
        print(f"  activities.block_id    {len(block_assignments)} backfilled")

    source.dispose()
    target.dispose()
    print("\nDone. Local DB seeded from snapshot.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-url",
        default=os.environ.get("SEED_SOURCE_URL"),
        help="Source (production) DB URL. Defaults to $SEED_SOURCE_URL.",
    )
    parser.add_argument(
        "--target-url",
        default=settings.DATABASE_URL,
        help="Target (local) DB URL. Defaults to settings.DATABASE_URL.",
    )
    parser.add_argument(
        "--activities",
        type=int,
        default=0,
        help="Keep only the N most recent activities (0 = all).",
    )
    parser.add_argument(
        "--with-live-tokens",
        action="store_true",
        help="Copy real Strava tokens instead of redacting them. Use only to test real sync; "
        "this can rotate and invalidate the production refresh token.",
    )
    parser.add_argument(
        "--no-reset-schema",
        action="store_true",
        help="Skip the schema drop/rebuild and load into the existing target schema.",
    )
    args = parser.parse_args()

    if not args.source_url:
        raise SystemExit("No source URL. Set $SEED_SOURCE_URL or pass --source-url.")

    target_url = _normalise_driver(args.target_url)
    if "localhost" not in target_url and "127.0.0.1" not in target_url:
        raise SystemExit(
            f"Refusing to seed a non-local target ({target_url!r}). "
            "This script wipes the target schema and is for local dev only."
        )

    if not args.no_reset_schema:
        _reset_target_schema(target_url)

    seed(args.source_url, target_url, args.activities, args.with_live_tokens)


if __name__ == "__main__":
    main()
