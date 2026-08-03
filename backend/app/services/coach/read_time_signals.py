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
``compute(db, activity, as_of) -> section | None`` adapter plus an optional
``gate_feature``. ``gather`` handles the two cross-cutting concerns ONCE:

  - **Prompt gating.** A ``gate_feature`` (a ``PromptFeature``) means the section is
    emitted ONLY under a prompt that carries that feature; otherwise ``gather``
    returns ``None`` and the section is dropped from serialization (the
    Optional-and-drop idiom, byte-stable elsewhere). ``gate_feature=None`` marks an
    ALWAYS-EMITTED signal (calibration, adherence live in the B baseline and always
    produce a section, possibly empty/degraded — never dropped).
  - **Abstention.** The bounded-window scan and the thin-history degrade live inside
    each adapter's ``compute`` (delegating to the unchanged pure cores and scan
    helpers), so a caller never re-implements either.

``context.py`` consumes ``gather``, not eleven private helpers, so a signal's private
conventions stop leaking into the assembler.

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
from datetime import date, datetime
from typing import Any, Callable, Optional, Protocol

from sqlalchemy.orm import Session

from app.services.coach.prompt_features import PromptFeature, has_feature


class SignalCompute(Protocol):
    """A read-time signal adapter: compute one pack section from a bounded history
    scan as of ``as_of``, or return ``None`` to abstain.

    ``as_of`` is the read anchor (the subject activity's reference instant), part of
    the seam so a stored-artifact adapter (``_MEMORY_SIGNAL`` today, adherence and
    calibration under #203) keys off the same point in time. Each adapter derives the exact anchor flavour it needs from ``activity``
    (some read ``start_date``, some the local calendar day); ``as_of`` is passed for
    the signals that key on it and ignored by the rest, keeping one uniform
    signature across all adapters.
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
    ``compute`` does the bounded scan + abstention.
    """

    name: str
    compute: SignalCompute
    gate_feature: Optional[PromptFeature] = None


def gather(
    signal: ReadTimeSignal,
    db: Session,
    activity: Any,
    prompt_id: Optional[str],
    as_of: datetime,
) -> Optional[Any]:
    """Resolve one signal into its pack section (or ``None``).

    The two cross-cutting concerns, applied ONCE here so no adapter and no caller
    repeats them:

      1. Gating: a gated signal whose feature the active prompt does not carry emits
         ``None`` (dropped from serialization). An ungated signal (``gate_feature``
         is ``None``) always runs.
      2. Abstention is delegated to ``compute`` (it owns the bounded scan + thin-history
         degrade).
    """
    if signal.gate_feature is not None and not has_feature(prompt_id, signal.gate_feature):
        return None
    return signal.compute(db, activity, as_of)
