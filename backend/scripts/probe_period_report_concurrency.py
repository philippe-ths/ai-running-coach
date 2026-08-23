"""Live concurrency probe for period-report creation (#946 adversarial review).

Not part of the pytest suite: the standard `db`/`client` fixtures share ONE
SQLite connection/session per test (see `tests/conftest.py`), which cannot
exercise real concurrent DB access at all — no real row locking, one shared
transaction. The race the review found (two concurrent identical POSTs both
passing `report_in_flight -> find_ready -> create_generating_report` and both
creating a `generating` row) can only be reproduced and disproven against a
real Postgres with real concurrent connections and a real Redis, which is what
this script does — the same method the review itself used.

Run against a THROWAWAY database only:

    docker exec running-coach-postgres psql -U coach -d coach \\
        -c "CREATE DATABASE period_report_concurrency_probe;"
    DATABASE_URL="postgresql+psycopg://coach:coach@localhost:5433/period_report_concurrency_probe" \\
        .venv/bin/python -m alembic upgrade head
    DATABASE_URL="postgresql+psycopg://coach:coach@localhost:5433/period_report_concurrency_probe" \\
        .venv/bin/python scripts/probe_period_report_concurrency.py

Prints a PASS/FAIL verdict for both halves: the UNGUARDED sequence (the
pre-fix shape, called directly with no claim) reliably produces two rows, and
the GUARDED sequence (`store.claim_identity` in front, what the API route
actually does) reliably produces one.
"""

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.period_report import PeriodReport  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.coach import period_report_store as store  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL or "period_report_concurrency_probe" not in DATABASE_URL:
    print(
        "Refusing to run: DATABASE_URL must point at a throwaway database named "
        "'period_report_concurrency_probe'. See the module docstring.",
        file=sys.stderr,
    )
    sys.exit(2)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

IDENTITY = dict(
    period_start=date(2026, 1, 1),
    period_end=date(2026, 1, 7),
    disciplines_key="all",
    prompt_id="probe_v1",
    schema_version="1.0",
)
N_THREADS = 8


def _seed_user() -> uuid.UUID:
    db = SessionLocal()
    try:
        user = User(email=f"concurrency-probe-{uuid.uuid4()}@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _cleanup(user_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        db.query(PeriodReport).filter(PeriodReport.user_id == user_id).delete()
        db.query(User).filter(User.id == user_id).delete()
        db.commit()
    finally:
        db.close()


def _row_count(user_id: uuid.UUID) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(func.count(PeriodReport.id))
            .filter(PeriodReport.user_id == user_id)
            .scalar()
        )
    finally:
        db.close()


def unguarded_attempt(user_id: uuid.UUID) -> None:
    """The PRE-FIX shape: no claim in front, just the three reads/write the
    review found racy."""
    db = SessionLocal()
    try:
        existing = store.report_in_flight(db, user_id, **IDENTITY) or store.find_ready(
            db, user_id, **IDENTITY
        )
        if existing is not None:
            return
        store.create_generating_report(
            db, user_id, disciplines=[], **{k: v for k, v in IDENTITY.items() if k != "disciplines_key"}
        )
    finally:
        db.close()


def guarded_attempt(user_id: uuid.UUID) -> None:
    """What `app.api.period_reports.create_period_report` actually does."""
    db = SessionLocal()
    try:
        claimed = store.claim_identity(user_id, **IDENTITY)
        if not claimed:
            return
        existing = store.report_in_flight(db, user_id, **IDENTITY) or store.find_ready(
            db, user_id, **IDENTITY
        )
        if existing is not None:
            return
        store.create_generating_report(
            db, user_id, disciplines=[], **{k: v for k, v in IDENTITY.items() if k != "disciplines_key"}
        )
    finally:
        db.close()


def run_round(label: str, attempt_fn) -> int:
    user_id = _seed_user()
    try:
        with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            list(pool.map(lambda _: attempt_fn(user_id), range(N_THREADS)))
        count = _row_count(user_id)
        print(f"{label}: {N_THREADS} concurrent identical requests -> {count} row(s) created")
        return count
    finally:
        _cleanup(user_id)


def main() -> None:
    print(f"Probing against {DATABASE_URL}\n")

    unguarded_counts = [run_round("UNGUARDED (pre-fix)", unguarded_attempt) for _ in range(5)]
    guarded_counts = [run_round("GUARDED   (post-fix)", guarded_attempt) for _ in range(5)]

    print()
    unguarded_raced = any(c > 1 for c in unguarded_counts)
    guarded_clean = all(c == 1 for c in guarded_counts)

    print(
        f"UNGUARDED reproduced the double-create at least once: "
        f"{'YES (expected)' if unguarded_raced else 'no — could not reproduce the race this run'}"
    )
    print(
        f"GUARDED stayed at exactly one row every round: "
        f"{'YES' if guarded_clean else 'NO — regression!'}"
    )

    if not guarded_clean:
        print("\nFAIL: the guarded path created more than one row for an identical concurrent request.")
        sys.exit(1)
    if not unguarded_raced:
        print(
            "\nWARNING: the unguarded control never raced in this run (timing-dependent); "
            "the guarded result above is still the evidence that matters."
        )
    print("\nPASS")


if __name__ == "__main__":
    main()
