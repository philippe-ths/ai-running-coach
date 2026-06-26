"""One interface for the read-time, history-scan coach-pack signals (#492).

Five coach-pack sections are each a *deterministic signal computed at read time
from a bounded scan of the runner's recent history, abstaining when history is
thin*: M9 HR-drift calibration, M7 adherence outcomes, #400 training-volume vs
norm, P3 training-load readiness, and #444 recent-training. Before this module
each was wired into the pack by a bespoke ``context._build_*_context`` helper with
its own scan limit, its own abstention rule, and its own gating clause copy-pasted
inline — shallow per-signal wiring nearly as complex as the work behind it.

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

``context.py`` consumes ``gather``, not five private helpers, so a signal's private
conventions stop leaking into the assembler. The seam is also what #203 needs:
materialising adherence/calibration as STORED artifacts (read-stored instead of
compute-now) is a second adapter with the SAME
``(db, user_id, activity, as_of) -> section | None`` shape registered for the same
``name`` — swappable with no ``context.py`` change.

The adapter ``compute`` functions themselves live in ``context.py`` (they gather ORM
rows, which is ``context``'s job); this module owns only the interface and the
dispatch. Keeping the gathering there avoids an import cycle and keeps the row-reads
next to the other pack assembly.
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
    the seam so a future stored-artifact adapter (#203) keys off the same point in
    time. Each adapter derives the exact anchor flavour it needs from ``activity``
    (some read ``start_date``, some the local calendar day); ``as_of`` is passed for
    the signals that key on it and ignored by the rest, keeping one uniform
    signature across all adapters.
    """

    def __call__(self, db: Session, activity: Any, as_of: datetime) -> Optional[Any]:
        ...


@dataclass(frozen=True)
class ReadTimeSignal:
    """One read-time history-scan signal behind the shared seam.

    ``name`` identifies the signal (and is the key a stored-artifact adapter would
    re-register under, #203). ``gate_feature`` is the ``PromptFeature`` that must be
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
