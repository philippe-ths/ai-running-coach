"""#1124: a reply cut off at max_tokens is a failed turn like any other.

#936 kept failed turns out of what the model reads back, by their stored text.
The cut-off reply's text was written inline where it is raised and never joined
that set, so a retry still sent it to the model as a coach turn and the
cross-thread digest still carried it as the coach's words.
"""

import ast
from pathlib import Path

from app.services.coach import chat
from app.services.coach.chat import TRUNCATED_FAIL_MESSAGE
from app.services.coach.thread_turn import _build_cross_thread_block, _llm_history
from tests.test_failed_turn_history_936 import _production_thread, _thread, _user


def test_a_retry_does_not_send_a_cut_off_reply_as_a_coach_turn(db):
    thread = _production_thread(db, _user(db), failure=TRUNCATED_FAIL_MESSAGE)

    assert all(m["role"] == "user" for m in _llm_history(db, thread))


def test_the_digest_agrees_a_cut_off_reply_is_not_conversation(db):
    user = _user(db)
    current = _thread(db, user)
    _production_thread(db, user, failure=TRUNCATED_FAIL_MESSAGE)

    assert "ran too long" not in _build_cross_thread_block(db, current, user.id)


def test_every_failure_a_turn_stores_is_one_the_readers_know():
    """A failure text assigned anywhere in the turn must be a known name, so a new
    one cannot be written inline and slip past the readers again."""
    tree = ast.parse(Path(chat.__file__).read_text())
    assigned = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "stream_fail_message"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    names = set()
    for value in assigned:
        for leaf in ast.walk(value):
            if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                raise AssertionError(f"inline failure text: {leaf.value!r}")
            if isinstance(leaf, ast.Name) and leaf.id.isupper():
                names.add(leaf.id)
    assert assigned
    assert {getattr(chat, name) for name in names} <= chat._TURN_FAILURE_MESSAGES
