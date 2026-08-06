"""One interface for the read-time, history-scan coach-pack signals (#492).

A family of coach-pack sections are each a *deterministic signal computed at read
time from a bounded scan of the runner's recent history, abstaining when history is
thin*. #492 shipped with five (M9 HR-drift calibration, M7 adherence outcomes, #400
training-volume vs norm, P3 training-load readiness, #444 recent-training); the
registered set is now eleven, adding readiness, recent_weeks, training_history,
training_history_2wk, memory, and intensity (see the ``_*_SIGNAL`` block in
``context.py``). Before this module each was wired into the pack by a bespoke
``context._build_*_context`` helper with its own scan limit, its own abstention rule,
and its own gating clause copy-pasted inline — shallow per-signal wiring nearly as
complex as the work behind it.

This module is the single seam they share. A signal is a ``ReadTimeSignal``: a
``compute(db, activity, as_of) -> section | None`` adapter plus the gate and kill
switch DERIVED from its ``signal_registry`` declaration. ``gather`` handles the three
cross-cutting concerns ONCE:

  - **Prompt gating.** A ``gate_feature`` (a ``PromptFeature``) means the section is
    emitted ONLY under a prompt that carries that feature; otherwise ``gather``
    returns ``None`` and the section is dropped from serialization (the
    Optional-and-drop idiom, byte-stable elsewhere). ``gate_feature=None`` marks an
    ALWAYS-EMITTED signal (calibration, adherence live in the B baseline and always
    produce a section, possibly empty/degraded — never dropped).
  - **The kill switch** (#800). A signal whose declared drop-effect
    ``COACH_*_ENABLED`` setting is off emits ``None``, exactly as the gate does. This
    was previously an ``if not settings.X: return None`` line hand-written inside
    each builder — the same fact the declaration already held, restated where it
    could be forgotten.
  - **Abstention.** The bounded-window scan and the thin-history degrade live inside
    each adapter's ``compute`` (delegating to the unchanged pure cores and scan
    helpers), so a caller never re-implements either.

``context.py`` consumes ``gather``, not eleven private helpers, so a signal's private
conventions stop leaking into the assembler; and since #800 it registers only the
``compute`` adapters, with ``build_signals`` supplying each signal's gate and switch
from the declaration.

The adapter ``compute`` functions themselves live in ``context.py`` (they gather ORM
rows, which is ``context``'s job); this module owns only the interface and the
dispatch. Keeping the gathering there avoids an import cycle and keeps the row-reads
next to the other pack assembly.

Why the seam stays (#699 part b, settled 2026-08-03)
----------------------------------------------------
#699 asked whether this seam was premature: one adapter shape and a two-line
``gather`` was arguably a hypothetical seam whose only justification was a FUTURE
stored-artifact adapter (#203), so its fate was deferred to that issue. Two facts
settled it independently of #203:

  - **It has eleven consumers, not one.** Collapsing to inline gating means writing
    the ``has_feature(...) else None`` clause at eleven call sites, restoring exactly
    the copy-paste this module removed.
  - **A read-stored adapter already lives behind it.** ``_MEMORY_SIGNAL`` (ADR 0025)
    reads the stored ``runner_memory`` row rather than scanning history, through the
    same ``(db, activity, as_of) -> section | None`` shape and the same gate. The
    "second adapter kind" #203 was going to supply is already demonstrated, so #203
    materialising adherence/calibration as stored artifacts is now a swap of an
    existing signal's ``compute`` with no ``context.py`` change, not a decision about
    whether the seam earns its keep.

Two live call-site patterns deliberately sit OUTSIDE the seam. Both are transitional,
held open only because the pre-ADR-0026 prompts must stay byte-stable, and both
resolve when those older pack keys retire (the same blocker #704 names):

  - **Exclusive alternatives.** ``training_history`` is fed by either
    ``_TRAINING_HISTORY_SIGNAL`` or ``_TRAINING_HISTORY_2WK_SIGNAL``, never both
    (mutually exclusive by prompt feature), so the assembler joins them with ``or``
    into the one pack key. The seam models one signal to one section, with no
    either/or.
  - **A fan-out reusing a signal's compute under a different gate.** Under an
    intensity-read prompt (ADR 0026 Slice 3) ``build_context_pack`` calls
    ``_build_intensity_context`` directly, because that one scan fans out into THREE
    pack sections (``intensity_read``, ``intensity_mix``, ``referral``) while nulling
    two others. ``_INTENSITY_SIGNAL`` abstains under that prompt, so nothing is
    computed twice. A one-signal-one-section seam cannot absorb a fan-out without
    distorting into something broader than the concern it owns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.coach.prompt_features import PromptFeature, has_feature
from app.services.coach.signal_registry import READ_TIME_ADAPTERS


class SignalCompute(Protocol):
    """A read-time signal adapter: compute one pack section from a bounded history
    scan, or return ``None`` to abstain.

    ``as_of`` is DEAD as of #800 and kept deliberately, not by oversight. Every one of
    the eleven adapters derives its own anchor from ``activity`` (some read
    ``start_date``, some the local calendar day) and none reads this argument. Its
    removal is a signature change across the whole seam with no behavioural effect, so
    it is surfaced rather than taken: #203's stored-artifact adapters are the plausible
    first real reader, and dropping the parameter now would only have to be reinstated.
    The previous docstring claimed it was "passed for the signals that key on it",
    which was never true of any adapter.
    """

    def __call__(self, db: Session, activity: Any, as_of: datetime) -> Optional[Any]:
        ...


@dataclass(frozen=True)
class ReadTimeSignal:
    """One read-time history-scan signal behind the shared seam.

    ``name`` identifies the signal (and is the key a stored-artifact adapter
    re-registers under, as ``_MEMORY_SIGNAL`` already does and #203 would for
    adherence/calibration). ``gate_feature`` is the ``PromptFeature`` that must be
    present for the section to be emitted, or ``None`` for an always-emitted signal.
    ``kill_switch`` is the OPTIONAL ``COACH_*_ENABLED`` setting whose OFF state drops
    the section outright. ``compute`` does the bounded scan + abstention.

    #800: a signal is NOT hand-built here any more. ``build_signals`` derives the
    ``gate_feature`` and ``kill_switch`` of each one from
    ``signal_registry.COACH_SIGNALS``, so the seam and the declaration cannot
    disagree, and ``context.py`` supplies only the ``compute``.
    """

    name: str
    compute: SignalCompute
    gate_feature: Optional[PromptFeature] = None
    kill_switch: Optional[str] = None


def build_signals(
    computes: Mapping[str, SignalCompute]
) -> Dict[str, ReadTimeSignal]:
    """Derive the seam's signal objects from the declaration registry (#800).

    The caller supplies ONLY the ``compute`` adapters, keyed by the adapter name the
    registry declares. The gate and the kill switch come from the declaration, so
    they are stated once. A declared adapter with no compute, or a compute with no
    declaration, raises AT IMPORT rather than silently ungating a section or leaving
    a builder unreachable.
    """
    declared = {a.name: a for a in READ_TIME_ADAPTERS}
    missing = sorted(set(declared) - set(computes))
    if missing:
        raise RuntimeError(
            f"read-time signal seam: declared adapter(s) {missing} have no compute "
            f"registered. Add the builder, or drop the adapter from "
            f"signal_registry.COACH_SIGNALS."
        )
    undeclared = sorted(set(computes) - set(declared))
    if undeclared:
        raise RuntimeError(
            f"read-time signal seam: compute(s) {undeclared} are registered with no "
            f"CoachSignal adapter declaring them. Declare them in "
            f"signal_registry.COACH_SIGNALS (which fixes their gate and kill switch)."
        )
    return {
        name: ReadTimeSignal(
            name=name,
            compute=computes[name],
            gate_feature=declared[name].gate_feature,
            kill_switch=declared[name].kill_switch,
        )
        for name in declared
    }


def gather(
    signal: ReadTimeSignal,
    db: Session,
    activity: Any,
    prompt_id: Optional[str],
    as_of: datetime,
) -> Optional[Any]:
    """Resolve one signal into its pack section (or ``None``).

    The three cross-cutting concerns, applied ONCE here so no adapter and no caller
    repeats them:

      1. Gating: a gated signal whose feature the active prompt does not carry emits
         ``None`` (dropped from serialization). An ungated signal (``gate_feature``
         is ``None``) always runs.
      2. The kill switch (#800): a signal whose declared drop-effect
         ``COACH_*_ENABLED`` setting is off emits ``None``, exactly as the gate does.
         This used to be an ``if not settings.X: return None`` line hand-written
         inside each builder, one more copy of a fact the declaration already holds.
      3. Abstention is delegated to ``compute`` (it owns the bounded scan + thin-history
         degrade).
    """
    if signal.gate_feature is not None and not has_feature(prompt_id, signal.gate_feature):
        return None
    if signal.kill_switch is not None and not getattr(settings, signal.kill_switch):
        return None
    return signal.compute(db, activity, as_of)
