"""Characterisation + query-shape guard for the thread switcher list (#788).

`list_threads` used to run three queries per thread beyond the initial fetch
(last message, first user message for the untitled-title fallback, and a lazy
load of the anchor activity). This file is the oracle for the behaviour-
preserving refactor that collapses those into per-CALL aggregates:

  1. `TestThreadListCharacterisation` pins the FULL output against a JSON
     snapshot captured by running this file against the pre-refactor
     implementation (commit 76bb7e0). Regenerate deliberately with
     `UPDATE_SNAPSHOTS=1` (the convention `test_analysis_interface.py` uses).
  2. `TestListCap` pins the `_MAX_LISTED_THREADS` cap and the ordering at that
     boundary, where a snapshot would be unreadable.
  3. `TestQueryShape` pins the query COUNT and, crucially, that it does not grow
     with the number of threads: the same scenario shape is measured at 3 and at
     25 threads and the counts must be equal.

The scenario deliberately covers every branch of the three collapsed reads:
written title / first-user-message title / no user message / empty / whitespace
first message, truncation with and without a trailing space at the cut point,
whitespace collapsing, snippet from an empty and a whitespace-only last message,
anchored / unanchored / soft-deleted-anchor / dangling-anchor threads, ties in
`last_message_at` between threads, ties in `created_at` between messages, a
thread with no messages at all, and another user's thread (owner scoping).

All row data below is synthetic test setup (exercises code paths; represents no
real runner).
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from app.models import Activity, User
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.coach import threads as thread_service

SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "threads"
SNAPSHOT_FILE = SNAPSHOT_DIR / "list_threads_branch_matrix.json"

# Stable identifiers so the snapshot is reproducible across runs. Ordering ties
# are resolved by id, so these values are load-bearing, not decorative.
USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
ACTIVITY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
DELETED_ACTIVITY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
DANGLING_ACTIVITY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bf")

BASE = datetime(2026, 8, 1, 12, 0, 0)


def _tid(suffix: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-00000{suffix}")


def _mid(suffix: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-00000010{suffix}")


class _CountQueries:
    """Count statements executed on the test session's bind (the
    `test_raw_summary_deferred.py` idiom)."""

    def __init__(self, db):
        self.bind = db.get_bind()
        self.statements: list[str] = []

    def _on_exec(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        sa.event.listen(self.bind, "after_cursor_execute", self._on_exec)
        return self

    def __exit__(self, *a):
        sa.event.remove(self.bind, "after_cursor_execute", self._on_exec)

    @property
    def count(self) -> int:
        return len(self.statements)


def _seed_users_and_activities(db) -> None:
    db.add(User(id=USER_ID, email="switcher@example.com"))
    db.add(User(id=OTHER_USER_ID, email="other@example.com"))
    db.flush()
    db.add(
        Activity(
            id=ACTIVITY_ID,
            user_id=USER_ID,
            strava_activity_id=910001,
            name="Tuesday intervals",
            type="Run",
            start_date=BASE - timedelta(days=1),
            distance_m=8000,
            moving_time_s=2400,
            elapsed_time_s=2500,
            elev_gain_m=30.0,
            raw_summary={},
        )
    )
    db.add(
        Activity(
            id=DELETED_ACTIVITY_ID,
            user_id=USER_ID,
            strava_activity_id=910002,
            name="Deleted from Strava",
            type="Run",
            start_date=BASE - timedelta(days=2),
            distance_m=5000,
            moving_time_s=1800,
            elapsed_time_s=1800,
            elev_gain_m=10.0,
            is_deleted=True,
            raw_summary={},
        )
    )
    db.flush()


def _thread(
    db,
    tid: uuid.UUID,
    *,
    title=None,
    activity_id=None,
    last_message_at=None,
    created_at=None,
    user_id=USER_ID,
) -> Thread:
    thread = Thread(
        id=tid,
        user_id=user_id,
        activity_id=activity_id,
        title=title,
        created_at=created_at or BASE,
        last_message_at=last_message_at,
    )
    db.add(thread)
    return thread


def _message(db, mid, thread_id, role, content, created_at) -> None:
    db.add(
        CoachChatMessage(
            id=mid,
            thread_id=thread_id,
            role=role,
            content=content,
            created_at=created_at,
        )
    )


LONG_ANSWER = (
    "You held the effort well through the middle reps and only faded on the last "
    "one, which is exactly the shape we want at this point in the block."
)
# 59 'a's then a space, so the 60-character title cut lands ON a space and the
# rstrip branch fires.
TITLE_CUT_ON_SPACE = "a" * 59 + " tail beyond the sixty character boundary"


def _seed_branch_matrix(db) -> None:
    """One thread per branch of the three reads being collapsed."""
    _seed_users_and_activities(db)

    # 1. Written title, anchored, long last message (snippet truncation).
    _thread(
        db,
        _tid("0000001"),
        title="Marathon build",
        activity_id=ACTIVITY_ID,
        last_message_at=BASE - timedelta(minutes=1),
    )
    _message(db, _mid("0001"), _tid("0000001"), "user", "how did that go?", BASE - timedelta(minutes=2))
    _message(db, _mid("0002"), _tid("0000001"), "assistant", LONG_ANSWER, BASE - timedelta(minutes=1))

    # 2. No title: falls back to the first user message. Unanchored.
    _thread(db, _tid("0000002"), last_message_at=BASE - timedelta(minutes=3))
    _message(db, _mid("0011"), _tid("0000002"), "user", "What about my calf tightness?", BASE - timedelta(minutes=5))
    _message(db, _mid("0012"), _tid("0000002"), "assistant", "Keep it easy this week.", BASE - timedelta(minutes=3))

    # 3. No title and NO user message at all: "New thread", snippet from the
    #    assistant turn.
    _thread(db, _tid("0000003"), last_message_at=BASE - timedelta(minutes=6))
    _message(db, _mid("0021"), _tid("0000003"), "assistant", "Nice session today.", BASE - timedelta(minutes=6))

    # 4. No title, no messages at all: last_message_at null, so ordering falls
    #    back to created_at and the snippet is None.
    _thread(db, _tid("0000004"), created_at=BASE - timedelta(minutes=7))

    # 5. Whitespace-only first user message: the title collapses to "" (not
    #    "New thread"), and the snippet collapses to "" (not None).
    _thread(db, _tid("0000005"), last_message_at=BASE - timedelta(minutes=8))
    _message(db, _mid("0031"), _tid("0000005"), "user", "   \n  ", BASE - timedelta(minutes=8))

    # 6. Empty first user message: falsy, so the title IS "New thread"; the
    #    snippet comes from the later assistant turn.
    _thread(db, _tid("0000006"), last_message_at=BASE - timedelta(minutes=9))
    _message(db, _mid("0041"), _tid("0000006"), "user", "", BASE - timedelta(minutes=10))
    _message(db, _mid("0042"), _tid("0000006"), "assistant", "Say more?", BASE - timedelta(minutes=9))

    # 7. Empty LAST message: snippet is None while the title still resolves.
    _thread(db, _tid("0000007"), last_message_at=BASE - timedelta(minutes=11))
    _message(db, _mid("0051"), _tid("0000007"), "user", "quick question", BASE - timedelta(minutes=12))
    _message(db, _mid("0052"), _tid("0000007"), "assistant", "", BASE - timedelta(minutes=11))

    # 8. Title truncation whose cut lands on a space (the rstrip branch).
    _thread(db, _tid("0000008"), last_message_at=BASE - timedelta(minutes=13))
    _message(db, _mid("0061"), _tid("0000008"), "user", TITLE_CUT_ON_SPACE, BASE - timedelta(minutes=13))

    # 9. Newlines and runs of spaces collapse in both the title and the snippet.
    _thread(db, _tid("0000009"), last_message_at=BASE - timedelta(minutes=14))
    _message(
        db,
        _mid("0071"),
        _tid("0000009"),
        "user",
        "  first   line\n\n second\tline  ",
        BASE - timedelta(minutes=14),
    )

    # 10. Anchored to a soft-deleted activity: the anchor is still the full row.
    _thread(
        db,
        _tid("0000010"),
        title="About the deleted run",
        activity_id=DELETED_ACTIVITY_ID,
        last_message_at=BASE - timedelta(minutes=15),
    )
    _message(db, _mid("0081"), _tid("0000010"), "user", "what happened here?", BASE - timedelta(minutes=15))

    # 11. Dangling anchor (no activity row): the id-only anchor branch.
    _thread(
        db,
        _tid("0000011"),
        title="Orphaned anchor",
        activity_id=DANGLING_ACTIVITY_ID,
        last_message_at=BASE - timedelta(minutes=16),
    )

    # 12 + 13. Tied last_message_at between two threads: id DESC breaks the tie.
    tie_at = BASE - timedelta(minutes=17)
    _thread(db, _tid("0000012"), title="Tie lower id", last_message_at=tie_at)
    _thread(db, _tid("0000013"), title="Tie higher id", last_message_at=tie_at)

    # 14. Tied created_at between messages: the last message is the higher id and
    #     the first user message is the lower id.
    _thread(db, _tid("0000014"), last_message_at=BASE - timedelta(minutes=18))
    tied = BASE - timedelta(minutes=18)
    _message(db, _mid("0091"), _tid("0000014"), "user", "first user turn by id", tied)
    _message(db, _mid("0092"), _tid("0000014"), "user", "second user turn by id", tied)
    _message(db, _mid("0093"), _tid("0000014"), "assistant", "last turn by id", tied)

    # 15. Two user turns at DISTINCT times whose id order is the reverse of
    #     their time order, so the first-user-message read is only right if it
    #     orders by created_at ASCENDING; an id-only tie-break would pick the
    #     other one.
    _thread(db, _tid("0000015"), last_message_at=BASE - timedelta(minutes=19))
    _message(db, _mid("00a2"), _tid("0000015"), "user", "older user turn wins the title", BASE - timedelta(minutes=21))
    _message(db, _mid("00a1"), _tid("0000015"), "user", "newer user turn", BASE - timedelta(minutes=20))
    _message(db, _mid("00a3"), _tid("0000015"), "assistant", "assistant closes", BASE - timedelta(minutes=19))

    # 16. An EMPTY written title is falsy, so it falls back like a null one.
    _thread(db, _tid("0000016"), title="", last_message_at=BASE - timedelta(minutes=20))
    _message(db, _mid("00b1"), _tid("0000016"), "user", "empty title falls back", BASE - timedelta(minutes=20))

    # 17. Another user's thread: never listed.
    _thread(
        db,
        _tid("00000ff"),
        title="Someone else's conversation",
        last_message_at=BASE,
        user_id=OTHER_USER_ID,
    )
    _message(db, _mid("00f1"), _tid("00000ff"), "user", "not yours", BASE)

    db.commit()


def _serialise(items) -> list:
    return [item.model_dump(mode="json") for item in items]


class TestThreadListCharacterisation:
    def test_branch_matrix_matches_captured_baseline(self, db):
        _seed_branch_matrix(db)

        actual = _serialise(thread_service.list_threads(db, USER_ID))

        if os.environ.get("UPDATE_SNAPSHOTS"):
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_FILE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
        expected = json.loads(SNAPSHOT_FILE.read_text())
        assert actual == expected

    def test_snapshot_covers_the_branches_it_claims_to(self, db):
        """A snapshot only has authority if the scenario actually reaches every
        branch, so assert the distinguishing values directly rather than trusting
        the blob."""
        _seed_branch_matrix(db)

        items = {str(i.id): i for i in thread_service.list_threads(db, USER_ID)}

        assert items[str(_tid("0000001"))].title == "Marathon build"
        assert items[str(_tid("0000002"))].title == "What about my calf tightness?"
        assert items[str(_tid("0000003"))].title == "New thread"
        assert items[str(_tid("0000004"))].title == "New thread"
        assert items[str(_tid("0000004"))].snippet is None
        # Whitespace-only first message collapses to an empty title/snippet.
        assert items[str(_tid("0000005"))].title == ""
        assert items[str(_tid("0000005"))].snippet == ""
        # Empty first user message is falsy, so the fallback name wins.
        assert items[str(_tid("0000006"))].title == "New thread"
        assert items[str(_tid("0000007"))].snippet is None
        assert items[str(_tid("0000008"))].title == "a" * 59 + "…"
        assert items[str(_tid("0000009"))].title == "first line second line"
        # Long last message truncates at 90 characters with an ellipsis.
        snippet = items[str(_tid("0000001"))].snippet
        assert snippet is not None and len(snippet) == 91 and snippet.endswith("…")
        # Anchors: full row (including a soft-deleted activity), id-only when the
        # row is gone, absent when unanchored.
        assert items[str(_tid("0000001"))].anchor.name == "Tuesday intervals"
        assert items[str(_tid("0000010"))].anchor.name == "Deleted from Strava"
        assert items[str(_tid("0000011"))].anchor.name is None
        assert items[str(_tid("0000011"))].anchor.activity_id == DANGLING_ACTIVITY_ID
        assert items[str(_tid("0000002"))].anchor is None
        # Message-level ties: last by id DESC, first user message by id ASC.
        assert items[str(_tid("0000014"))].title == "first user turn by id"
        assert items[str(_tid("0000014"))].snippet == "last turn by id"
        # Untied user turns: the OLDEST wins the title even though it carries the
        # higher id, so the created_at direction is load-bearing here.
        assert items[str(_tid("0000015"))].title == "older user turn wins the title"
        assert items[str(_tid("0000015"))].snippet == "assistant closes"
        # An empty written title falls back exactly like a null one.
        assert items[str(_tid("0000016"))].title == "empty title falls back"
        # Owner scoping.
        assert str(_tid("00000ff")) not in items

    def test_ordering_including_the_thread_level_tie(self, db):
        _seed_branch_matrix(db)

        order = [str(i.id) for i in thread_service.list_threads(db, USER_ID)]

        assert order == [
            str(_tid("0000001")),
            str(_tid("0000002")),
            str(_tid("0000003")),
            str(_tid("0000004")),
            str(_tid("0000005")),
            str(_tid("0000006")),
            str(_tid("0000007")),
            str(_tid("0000008")),
            str(_tid("0000009")),
            str(_tid("0000010")),
            str(_tid("0000011")),
            # Tied last_message_at: higher id first.
            str(_tid("0000013")),
            str(_tid("0000012")),
            str(_tid("0000014")),
            str(_tid("0000015")),
            str(_tid("0000016")),
        ]


def _seed_many(db, count: int, *, user_id=USER_ID, offset: int = 0) -> None:
    """`count` threads, most recent first by construction, alternating titled and
    untitled so both title branches stay live at every scale.

    Every anchored thread gets its OWN activity. Sharing one activity would let
    the identity map absorb all but the first lazy load and flatter the
    before-count into looking cheaper than production, where each thread is
    anchored to a different run.
    """
    for i in range(count):
        n = offset + i
        tid = uuid.UUID(int=0x50000000 + n)
        titled = n % 2 == 0
        anchor_id = None
        if n % 3 == 0:
            anchor_id = uuid.UUID(int=0x90000000 + n)
            db.add(
                Activity(
                    id=anchor_id,
                    user_id=user_id,
                    strava_activity_id=920000 + n,
                    name=f"Run {n}",
                    type="Run",
                    start_date=BASE - timedelta(days=n + 1),
                    distance_m=6000,
                    moving_time_s=1800,
                    elapsed_time_s=1800,
                    elev_gain_m=5.0,
                    raw_summary={},
                )
            )
        _thread(
            db,
            tid,
            title=f"Thread {n}" if titled else None,
            activity_id=anchor_id,
            last_message_at=BASE - timedelta(minutes=n),
            user_id=user_id,
        )
        _message(
            db,
            uuid.UUID(int=0x60000000 + n * 2),
            tid,
            "user",
            f"question {n}",
            BASE - timedelta(minutes=n + 1),
        )
        _message(
            db,
            uuid.UUID(int=0x60000000 + n * 2 + 1),
            tid,
            "assistant",
            f"answer {n}",
            BASE - timedelta(minutes=n),
        )
    db.commit()


class TestListCap:
    def test_cap_and_ordering_at_the_boundary(self, db):
        _seed_users_and_activities(db)
        db.commit()
        _seed_many(db, thread_service._MAX_LISTED_THREADS + 5)

        items = thread_service.list_threads(db, USER_ID)

        assert len(items) == thread_service._MAX_LISTED_THREADS
        # The seed orders threads newest-first by index, so the listed set is
        # exactly indices 0..99 in order and index 100 is the first one cut.
        expected = [
            uuid.UUID(int=0x50000000 + n)
            for n in range(thread_service._MAX_LISTED_THREADS)
        ]
        assert [i.id for i in items] == expected
        assert uuid.UUID(int=0x50000000 + thread_service._MAX_LISTED_THREADS) not in {
            i.id for i in items
        }


class TestQueryShape:
    """The point of #788: the query count must not scale with thread count."""

    def _measure(self, db, count: int, *, offset: int = 0) -> int:
        _seed_many(db, count, offset=offset)
        # Empty the identity map so a lazy anchor load is a real round trip
        # rather than an identity-map hit, and so nothing is left pending to
        # autoflush inside the measurement.
        db.expunge_all()
        with _CountQueries(db) as counter:
            thread_service.list_threads(db, USER_ID)
        return counter.count

    def test_count_does_not_grow_with_thread_count(self, db):
        _seed_users_and_activities(db)
        db.commit()

        small = self._measure(db, 3)
        db.query(CoachChatMessage).delete(synchronize_session=False)
        db.query(Thread).delete(synchronize_session=False)
        db.commit()
        # A fresh index range so the second batch's activities do not collide
        # with the first's on strava_activity_id.
        large = self._measure(db, 25, offset=200)

        assert small == large, f"query count scales with threads: {small} -> {large}"

    def test_count_is_the_three_aggregate_reads(self, db):
        """Threads (with the anchor joined), the last message per thread, and the
        first user message for the untitled ones."""
        _seed_users_and_activities(db)
        db.commit()

        assert self._measure(db, 25) == 3

    def test_all_titled_skips_the_first_message_read(self, db):
        _seed_users_and_activities(db)
        for n in range(10):
            tid = uuid.UUID(int=0x70000000 + n)
            _thread(
                db,
                tid,
                title=f"Named {n}",
                activity_id=ACTIVITY_ID,
                last_message_at=BASE - timedelta(minutes=n),
            )
            _message(
                db,
                uuid.UUID(int=0x80000000 + n),
                tid,
                "user",
                f"q {n}",
                BASE - timedelta(minutes=n),
            )
        db.commit()
        db.expunge_all()

        with _CountQueries(db) as counter:
            thread_service.list_threads(db, USER_ID)

        assert counter.count == 2

    def test_no_threads_costs_one_query(self, db):
        _seed_users_and_activities(db)
        db.commit()
        db.expunge_all()

        with _CountQueries(db) as counter:
            assert thread_service.list_threads(db, USER_ID) == []

        assert counter.count == 1
