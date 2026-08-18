"""#870: the chat diagram generator refuses to replace a real capture with none.

`generate_chat_flow_data.py` needs a seeded database to capture one real turn
end to end. Run without one it still completed and wrote a template-only blob,
discarding the capture rather than refusing — so an agent or a developer
satisfying the standing "regenerate the diagram in the same PR" rule could
mechanically leave the committed artifact showing LESS than it did before.

#855's capture-parity check catches the aftermath at the guard. This moves the
failure one step earlier, to the thing that produces the bad artifact, and
makes it say what is missing rather than read as a guard complaint.

The decision is a pure function so it can be tested with no database at all,
which is the condition the whole check is about.
"""

import json
import sys
from pathlib import Path

import pytest

_DIAGRAMS = Path(__file__).resolve().parents[2] / "docs" / "diagrams"
if str(_DIAGRAMS) not in sys.path:
    sys.path.insert(0, str(_DIAGRAMS))

import generate_chat_flow_data as gen  # noqa: E402

_TARGET = _DIAGRAMS / "coach-chat-nodes.js"


def _blob_line(blob: dict) -> str:
    return f"const CHAT = {json.dumps(blob, separators=(',', ':'))};\n"


_REAL = {
    "meta": {"coach_prompt_id": "coach_message_lean_grouped_v9"},
    "assembled": {"ok": True, "chars": 12590, "present": {"baseline_block": True}},
    "template": "unchanged declaration",
}


# --- a run that captured a real turn changes nothing --------------------------


def test_a_real_capture_is_left_exactly_as_the_run_produced_it():
    assert gen.plan_capture({"ok": True}, _blob_line(_REAL)) == {}


# --- a run with no database keeps what is committed ---------------------------


def test_a_template_only_run_keeps_the_committed_capture(capsys):
    """The #870 defect. Before the fix this run wrote its empty capture over a
    real one and the guard reported it a step later."""
    kept = gen.plan_capture({"ok": False, "reason": "CHAT_NO_DB set"}, _blob_line(_REAL))

    assert kept["assembled"] == _REAL["assembled"]
    # The config the capture was taken under travels WITH it. A fresh `meta`
    # over a preserved `assembled` would describe the capture with a config it
    # was not taken under, which is a subtler version of the same dishonesty.
    assert kept["meta"] == _REAL["meta"]
    # Declarations are NOT preserved: refreshing those is what a no-DB run is for.
    assert "template" not in kept

    note = capsys.readouterr().out
    assert "CHAT_NO_DB set" in note
    assert "make seed-local" in note


def test_the_preserved_keys_are_only_the_capture_and_its_config():
    """A widened preserve set would start freezing declarations, which is
    exactly the drift #855's blob comparison exists to catch."""
    assert gen._CAPTURE_KEYS == ("assembled", "meta")


# --- a run with nothing to fall back on refuses -------------------------------


@pytest.mark.parametrize(
    "committed, why",
    [
        (_blob_line({"assembled": {"ok": False, "reason": "no DB"}}), "already template-only"),
        (_blob_line({}), "no assembled key at all"),
        ("const CHAT = not json;\n", "unparseable blob"),
        ("// no blob line here\n", "no blob line"),
    ],
)
def test_a_template_only_run_with_nothing_to_keep_refuses(committed, why):
    with pytest.raises(gen.CaptureRefused) as refused:
        gen.plan_capture({"ok": False, "reason": "no DB"}, committed)

    message = str(refused.value)
    assert "refusing to write a template-only capture" in message, why
    # It must say what is missing and how to get it, per the issue's second AC.
    assert "no DB" in message
    assert "make seed-local" in message


# --- the reader describes the file that is actually committed -----------------


def test_the_committed_diagram_holds_a_real_capture_this_reader_can_find():
    """Anti-rot, and the reason the tests above are not self-referential: if the
    blob line's form changed, `_committed_capture` would return None for the
    real file and every no-DB run would refuse instead of preserving — a guard
    that fails closed, but for the wrong reason and with a misleading message."""
    kept = gen._committed_capture(_TARGET.read_text(encoding="utf-8"))

    assert kept is not None, (
        "the committed coach-chat-nodes.js carries no capture this reader can find"
    )
    assert kept["assembled"]["ok"] is True
    assert kept["meta"]["coach_prompt_id"]
