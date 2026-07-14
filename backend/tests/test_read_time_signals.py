"""The shared read-time history-scan signal seam (#492).

Pins the deepening's load-bearing contract: the read-time signals are reached
through ONE interface, and the two cross-cutting concerns — prompt gating and
dispatch — are handled ONCE in `gather`, not re-implemented per signal.

  - `gather` dispatches to each signal's `compute` with the uniform
    `(db, activity, as_of)` signature.
  - A PROMPT-GATED signal whose feature the active prompt lacks emits None (so the
    section is dropped from serialization, byte-stable) WITHOUT its `compute` ever
    running; under a prompt that carries the feature, `compute` runs.
  - An ALWAYS-EMITTED signal (no `gate_feature`) runs its `compute` on every prompt.

The math/abstention of each signal is covered by the per-signal context tests
(`test_calibration_context.py`, `test_adherence_context.py`, `test_volume_context.py`,
`test_training_load_context.py`, `test_recent_training.py`); this file proves only
that the seam routes/gates them uniformly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.coach.context import (
    READ_TIME_SIGNALS,
    _ADHERENCE_SIGNAL,
    _CALIBRATION_SIGNAL,
    _MEMORY_SIGNAL,
    _RECENT_TRAINING_SIGNAL,
    _TRAINING_HISTORY_SIGNAL,
    _TRAINING_LOAD_SIGNAL,
    _TRAINING_VOLUME_SIGNAL,
)
from app.services.coach.prompt_features import PromptFeature
from app.services.coach.read_time_signals import ReadTimeSignal, gather

_AS_OF = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)


# --- registry shape ----------------------------------------------------------


def test_all_signals_registered_under_one_interface():
    assert set(READ_TIME_SIGNALS) == {
        "calibration",
        "adherence",
        "training_load",
        "training_volume",
        "recent_training",
        # ADR 0026 Slice 2 (#670): the redefined right_now content signals.
        "readiness",
        "recent_weeks",
        "training_history",
        "memory",
        "intensity",
    }
    assert all(isinstance(s, ReadTimeSignal) for s in READ_TIME_SIGNALS.values())


def test_always_emitted_vs_gated_split_is_explicit():
    # Always-emitted (B baseline): no prompt-feature gate.
    assert _CALIBRATION_SIGNAL.gate_feature is None
    assert _ADHERENCE_SIGNAL.gate_feature is None
    # Prompt-gated (Optional-and-drop), each carrying its own feature.
    assert _TRAINING_LOAD_SIGNAL.gate_feature is PromptFeature.TRAINING_LOAD
    assert _TRAINING_VOLUME_SIGNAL.gate_feature is PromptFeature.VOLUME
    assert _RECENT_TRAINING_SIGNAL.gate_feature is PromptFeature.RECENT_TRAINING
    assert _TRAINING_HISTORY_SIGNAL.gate_feature is PromptFeature.TRAINING_HISTORY
    assert _MEMORY_SIGNAL.gate_feature is PromptFeature.MEMORY


# --- gather: dispatch + gating handled once ----------------------------------


def _spy_signal(name, gate_feature):
    """A ReadTimeSignal whose compute records that it ran and returns a sentinel."""
    calls = []

    def compute(db, activity, as_of):
        calls.append((db, activity, as_of))
        return f"section::{name}"

    return ReadTimeSignal(name, compute, gate_feature), calls


def test_gather_dispatches_to_compute_with_uniform_signature():
    sig, calls = _spy_signal("x", None)
    db, activity = object(), object()
    out = gather(sig, db, activity, prompt_id="anything", as_of=_AS_OF)
    assert out == "section::x"
    assert calls == [(db, activity, _AS_OF)]  # exact (db, activity, as_of) seam


def test_always_emitted_signal_runs_under_every_prompt():
    sig, calls = _spy_signal("always", None)
    for pid in (None, "coach_report_v10", "coach_message_v1", "coach_message_v11"):
        assert gather(sig, object(), object(), pid, _AS_OF) == "section::always"
    assert len(calls) == 4  # ran every time — never gated off


def test_gated_signal_abstains_without_running_compute_when_feature_absent():
    sig, calls = _spy_signal("vol", PromptFeature.VOLUME)
    # v8 carries no VOLUME feature -> dropped, and compute must NOT run.
    assert gather(sig, object(), object(), "coach_message_v8", _AS_OF) is None
    # An unknown/None prompt likewise carries nothing.
    assert gather(sig, object(), object(), None, _AS_OF) is None
    assert calls == []  # gating short-circuits before the scan


def test_gated_signal_runs_compute_when_feature_present():
    sig, calls = _spy_signal("vol", PromptFeature.VOLUME)
    # v9 carries VOLUME -> compute runs.
    assert gather(sig, object(), object(), "coach_message_v9", _AS_OF) == "section::vol"
    assert len(calls) == 1


def test_gating_is_per_feature_not_global():
    """Each gated signal keys on its OWN feature: v9 (VOLUME, no RECENT_TRAINING)
    runs volume but drops recent_training; v11 (both) runs both."""
    vol, vol_calls = _spy_signal("vol", PromptFeature.VOLUME)
    rec, rec_calls = _spy_signal("rec", PromptFeature.RECENT_TRAINING)

    assert gather(vol, object(), object(), "coach_message_v9", _AS_OF) == "section::vol"
    assert gather(rec, object(), object(), "coach_message_v9", _AS_OF) is None
    assert len(vol_calls) == 1 and rec_calls == []

    assert gather(vol, object(), object(), "coach_message_v11", _AS_OF) == "section::vol"
    assert gather(rec, object(), object(), "coach_message_v11", _AS_OF) == "section::rec"
    assert len(vol_calls) == 2 and len(rec_calls) == 1
