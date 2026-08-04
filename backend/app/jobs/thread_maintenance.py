"""Thread maintenance jobs (#766): the written title and the quiet-thread
memory pass.

Both are fail-soft background niceties enqueued after a thread turn commits —
neither may break or delay the runner's reply. NOTE: RQ serialises a deferred
job as its `module.function` string, so these entrypoints must keep this module
path (the tests/test_cadence_seam.py contract applies here too).
"""

import asyncio
import logging
import uuid as uuid_module

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.coach.budget import record as budget_record
from app.services.coach.llm import AnthropicClient

logger = logging.getLogger(__name__)

# The title writer is a cheap style task on the same auxiliary path as the
# receipt-voice generator and the memory writer: hardcoded Haiku by design (no
# config knob), spend recorded on the per-user budget counter (#472).
_TITLE_MODEL_ID = "claude-haiku-4-5"
_TITLE_MAX_TOKENS = 64

# One memory pass per runner per this interval, however many threads go quiet
# (ADR 0027: bounded; safe to run often because the writer rebuilds from source,
# but there is no value in stacking passes).
_THREAD_MEMORY_COOLDOWN_SECONDS = 3600

_TITLE_SYSTEM = (
    "You name conversations between a running coach and a runner. Given the "
    "opening exchange, answer with ONLY a short name for the conversation: 2-6 "
    "words, plain language, naming what it is about (like a note-to-self, e.g. "
    "\"Why distance is down\", \"Valencia build\", \"Tuesday's easy run\"). No "
    "quotes, no punctuation at the end, no explanation."
)


def enqueue_title_generation(db, thread: Thread) -> None:
    """After the FIRST exchange of an untitled thread, write its title offline.

    Enqueued once (on the turn that produced the first assistant reply); if the
    job fails the display falls back to the first user message, so a missing
    title is never a broken switcher row.
    """
    if thread.title:
        return
    assistant_count = (
        db.query(CoachChatMessage)
        .filter(
            CoachChatMessage.thread_id == thread.id,
            CoachChatMessage.role == "assistant",
        )
        .count()
    )
    if assistant_count != 1:
        return
    from app.core.queue import queue

    queue.enqueue(generate_thread_title_job, str(thread.id))


def generate_thread_title_job(thread_id: str) -> None:
    """RQ entrypoint: one cheap Haiku call names the thread from its opening
    exchange. A runner-written title always wins (never overwritten)."""
    db = SessionLocal()
    try:
        # Coerce once: the Uuid column does not bind a str under SQLite (tests).
        thread = (
            db.query(Thread).filter(Thread.id == uuid_module.UUID(thread_id)).first()
        )
        if thread is None or thread.title:
            return
        rows = (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.thread_id == thread.id)
            .order_by(CoachChatMessage.created_at.asc(), CoachChatMessage.id.asc())
            .limit(4)
            .all()
        )
        if not rows:
            return
        exchange = "\n".join(
            f"{'Runner' if r.role == 'user' else 'Coach'}: {r.content[:500]}"
            for r in rows
        )
        client = AnthropicClient(
            api_key=settings.ANTHROPIC_API_KEY, model=_TITLE_MODEL_ID
        )
        title, usage = asyncio.run(
            client.generate_json_with_usage(
                system=_TITLE_SYSTEM, user=exchange, max_tokens=_TITLE_MAX_TOKENS
            )
        )
        budget_record(
            thread.user_id,
            client.model,
            usage.input_tokens,
            usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
        )
        title = " ".join((title or "").split()).strip("\"'“”")
        if not title:
            return
        thread.title = title[:200]
        db.add(thread)
        db.commit()
    except Exception:  # noqa: BLE001 — background work, never crash the worker
        logger.exception("thread title generation failed for %s", thread_id)
    finally:
        db.close()


def schedule_thread_quiet_check(db, thread: Thread) -> None:
    """Debounce: check back after the block-gap; if the thread stayed quiet,
    fire one memory pass for the runner (bounded per interval)."""
    from datetime import timedelta

    from app.core.queue import queue

    message_count = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.thread_id == thread.id)
        .count()
    )
    queue.enqueue_in(
        timedelta(seconds=settings.BLOCK_GAP_SECONDS),
        thread_quiet_job,
        str(thread.id),
        message_count,
    )


def thread_quiet_job(thread_id: str, as_of_message_count: int) -> None:
    """RQ entrypoint: a thread that went quiet updates the runner's memory
    profile (ADR 0027). A newer message re-armed its own check, so a stale count
    no-ops; the per-runner cooldown collapses many quiet threads into one pass."""
    db = SessionLocal()
    try:
        thread = (
            db.query(Thread).filter(Thread.id == uuid_module.UUID(thread_id)).first()
        )
        if thread is None:
            return
        current = (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.thread_id == thread.id)
            .count()
        )
        if current != as_of_message_count:
            return  # not quiet: the newer turn's check owns the debounce
        from app.jobs.batch_chain import acquire_enqueue_slot

        if not acquire_enqueue_slot(
            f"thread-memory-pass:{thread.user_id}",
            _THREAD_MEMORY_COOLDOWN_SECONDS,
            degrade_open=False,
        ):
            return
        from app.core.queue import queue
        from app.jobs.memory_update import update_memory_job

        queue.enqueue(update_memory_job, str(thread.user_id))
    except Exception:  # noqa: BLE001 — background work, never crash the worker
        logger.exception("thread quiet check failed for %s", thread_id)
    finally:
        db.close()
