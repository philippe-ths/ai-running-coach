"""The analysis stage sequence, as DATA (#805).

`analyze()` composes a dozen pure stages into one `DerivedMetric`. Before this
module the sequence was a straight-line function body and its ordering
invariants were prose: three comments saying "this must run before that", with
nothing enforcing them. Reordering two stages produced a quietly wrong row and
every test in the repo still passed.

Here the sequence is a tuple of declarations. Each :class:`Stage` names what it
READS and what it WRITES, and :func:`assert_stage_contract` — which runs AT
IMPORT, the same move `app/schemas/coach_context.py` makes for `PackSection` —
turns an ordering violation into a startup ``RuntimeError``:

* a stage may only read a name the LOAD PHASE provides (:data:`PRELOADED_INPUTS`)
  or that an EARLIER stage writes;
* every write names either a real ``DerivedMetricFields`` field (a persisted
  column) or a declared intra-run intermediate (:data:`SCRATCH_NAMES`);
* no field is written twice, and the declared column writes cover EVERY
  analytical column of the row.

That last rule is what stops a new column being added to the model and silently
shipping as a null, and it is the same derivation the golden snapshot test now
uses instead of listing columns by hand.

What this module deliberately does NOT own: the stage functions themselves. The
adapters that call them live in `_orchestrator.py` and are resolved BY NAME off
that module at run time (:func:`run_stages`), so the orchestrator's namespace
stays the single interception seam every composition test already patches — now
including the four stages that used to be imported lazily inside the function
body and were therefore unreachable through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Iterable, Optional

from app.services.analysis.composition import DerivedMetricFields


# Names the LOAD PHASE puts in front of the stages before any of them runs.
# Pure DB reads and profile-derived scalars; no stage writes these.
PRELOADED_INPUTS = frozenset(
    {
        "db",
        "activity",
        # #830: the planned session this activity satisfies, resolved in the load
        # phase. It is what finally feeds `_extract_planned_workout`, a
        # placeholder that returned None from the beginning of the project.
        "planned_session",
        "history",
        "streams_dict",
        "check_in",
        "profile",
        "max_hr",
        "zone_boundaries",
    }
)

# Intra-run intermediates that flow BETWEEN stages but are never persisted.
# Declaring them means a stage that consumes one is subject to the same ordering
# check as one that consumes a column.
SCRATCH_NAMES = frozenset(
    {
        "probed_structure",   # the interval-structure probe's raw output
        "interval_session",   # the ONE "is this an interval session" gate (#701)
        "history_metrics",    # prior DerivedMetric rows, for load-spike detection
    }
)

# The persisted fields, derived from the accumulator (whose own agreement with
# the DerivedMetric model is pinned by test).
_COLUMN_NAMES = frozenset(f.name for f in fields(DerivedMetricFields))


class DeclaredProjection(dict):
    """The narrow dict handed to a stage whose pure function takes a mapping.

    A plain dict would reintroduce, one layer down, exactly the silent
    degradation this change removes: the consumer reads with ``.get()``, so a key
    the projection does not carry comes back as ``None`` — indistinguishable from
    a genuinely absent value, and the row is quietly wrong. Reading an undeclared
    key raises instead, so the declaration in the registry stays load-bearing for
    a consumer that lives in another file.
    """

    __slots__ = ()

    def get(self, key, default=None):
        if key not in self:
            raise RuntimeError(
                f"analysis stage projection: {key!r} was read but not declared. "
                f"Declared keys are {sorted(self)!r}. Add it to the stage's "
                f"`reads` so the import-time check can verify an earlier stage "
                f"produces it — reading it here would silently resolve to a "
                f"default that is indistinguishable from a real absent value."
            )
        return self[key]


@dataclass
class StageContext:
    """What a stage is handed: the load phase's inputs, the accumulator being
    built, and the declared intra-run intermediates.

    A stage reaches values through :meth:`get` / :meth:`set`, which resolve a
    name to whichever of the three homes owns it. That uniformity is what makes
    a single ``reads``/``writes`` vocabulary able to describe the whole
    composition, and what lets :func:`run_stages` police it.
    """

    db: Any
    activity: Any
    history: Any
    streams_dict: dict
    check_in: Any
    profile: Any
    max_hr: int
    zone_boundaries: Any
    state: DerivedMetricFields
    # #830. Defaulted because it is absent for every runner without a schedule,
    # and because "no plan" is the honest reading of its absence rather than a
    # missing argument.
    planned_session: Any = None
    scratch: dict[str, Any] = field(default_factory=dict)

    # Set by run_stages for the duration of one stage: the names that stage
    # DECLARED it reads. None means unenforced (a direct call outside the runner).
    _allowed_reads: Optional[frozenset] = None

    def get(self, name: str) -> Any:
        """Read a declared name.

        The declaration is enforced HERE, which is what makes it more than a
        comment in a tuple: a stage that reaches for a name it did not declare
        raises, so the `reads` lists cannot quietly under-declare and hollow out
        the import-time ordering check that is computed from them.
        """
        if self._allowed_reads is not None and name not in self._allowed_reads:
            raise RuntimeError(
                f"analysis stage read {name!r}, which it did not declare. Add it "
                f"to the stage's `reads` (and the import-time check will then "
                f"verify an earlier stage actually produces it)."
            )
        if name in PRELOADED_INPUTS:
            return getattr(self, name)
        if name in SCRATCH_NAMES:
            return self.scratch.get(name)
        return getattr(self.state, name)

    def set(self, name: str, value: Any) -> None:
        if name in SCRATCH_NAMES:
            self.scratch[name] = value
            return
        setattr(self.state, name, value)

    def project(self, names: Iterable[str]) -> "DeclaredProjection":
        """The narrow dict a stage declared, in place of the whole accumulator."""
        return DeclaredProjection({name: self.get(name) for name in names})

    def snapshot_identities(self, exclude: Iterable[str] = ()) -> dict[str, Any]:
        """Identity of every mutable name a stage could touch, minus the ones it
        declared. Identity, not equality: a stage mutating its OWN JSON value in
        place is legitimate; rebinding someone else's field is not."""
        skip = set(exclude)
        out: dict[str, Any] = {}
        for name in _COLUMN_NAMES:
            if name not in skip:
                out[name] = getattr(self.state, name)
        for name in SCRATCH_NAMES:
            if name not in skip:
                out[name] = self.scratch.get(name)
        return out


@dataclass(frozen=True)
class Stage:
    """One declared step of the analysis composition.

    ``adapter`` is the NAME of the adapter function on the orchestrator module,
    resolved at run time rather than captured here — that indirection is the
    interception seam, and it is also what keeps this module free of an import
    back to `_orchestrator`.

    ``reads`` and ``writes`` are the contract. They are checked structurally at
    import (ordering, coverage) and, for ``writes``, at run time: a stage that
    touches a field it did not declare raises instead of producing a row whose
    provenance nobody can reconstruct.
    """

    name: str
    adapter: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]


# The sequence. Order here IS the execution order; the three invariants that
# used to be prose are now the read/write declarations that make them checkable.
ANALYSIS_STAGES: tuple[Stage, ...] = (
    Stage(
        name="base_metrics",
        adapter="_stage_base_metrics",
        reads=("activity", "streams_dict", "max_hr", "check_in", "zone_boundaries"),
        writes=(
            "effort_score",
            "pace_variability",
            "hr_drift",
            "time_in_zones",
            "stops_analysis",
            "efficiency_analysis",
        ),
    ),
    # INVARIANT 1 (probe-before-classify) is now a declaration: the probe writes
    # `probed_structure` and `classification` reads it, so the two cannot be
    # swapped without failing the import check.
    Stage(
        name="interval_probe",
        adapter="_stage_interval_probe",
        reads=("activity", "streams_dict", "max_hr"),
        writes=("probed_structure",),
    ),
    Stage(
        name="classification",
        adapter="_stage_classification",
        reads=(
            "activity",
            "history",
            "max_hr",
            "time_in_zones",
            "pace_variability",
            "probed_structure",
        ),
        writes=("effort", "duration_class", "structure", "is_hilly", "is_race"),
    ),
    # INVARIANT 2 (the structure axis gates the interval-only stages): the gate
    # reads the classifier's `structure` axis, and every interval-only stage
    # below reads `interval_session` — so none of them can be hoisted above it.
    Stage(
        name="interval_gate",
        adapter="_stage_interval_gate",
        reads=("structure", "probed_structure"),
        writes=("interval_session", "interval_structure"),
    ),
    Stage(
        name="workout_match",
        adapter="_stage_workout_match",
        reads=("planned_session", "interval_session"),
        writes=("workout_match",),
    ),
    Stage(
        name="interval_kpis",
        adapter="_stage_interval_kpis",
        reads=("interval_session", "profile", "max_hr", "time_in_zones"),
        writes=("interval_kpis",),
    ),
    Stage(
        name="history_metrics",
        adapter="_stage_history_metrics",
        reads=("db", "history"),
        writes=("history_metrics",),
    ),
    # `flags` used to be handed the WHOLE accumulator flattened to a dict — all
    # 23 fields, eight of them still at their defaults because the stages that
    # write them had not run. It now receives exactly the five it declares.
    Stage(
        name="flags",
        adapter="_stage_flags",
        reads=(
            "activity",
            "history",
            "check_in",
            "history_metrics",
            "effort",
            "effort_score",
            "hr_drift",
            "pace_variability",
            "structure",
        ),
        writes=("flags",),
    ),
    Stage(
        name="training_context",
        adapter="_stage_training_context",
        reads=("db", "activity"),
        writes=("training_context",),
    ),
    Stage(
        name="risk",
        adapter="_stage_risk",
        reads=("check_in", "flags", "training_context"),
        writes=("risk_level", "risk_score", "risk_reasons"),
    ),
    Stage(
        name="confidence",
        adapter="_stage_confidence",
        reads=(
            "activity",
            "streams_dict",
            "check_in",
            "interval_session",
            "workout_match",
        ),
        writes=("confidence", "confidence_reasons"),
    ),
    Stage(
        name="discount_signals",
        adapter="_stage_discount_signals",
        reads=("activity", "profile", "is_hilly", "hr_drift"),
        writes=("discount_signals",),
    ),
    Stage(
        name="stream_view",
        adapter="_stage_stream_view",
        reads=("streams_dict",),
        writes=("stream_view",),
    ),
)


def assert_stage_contract(
    stages: Iterable[Stage],
    *,
    require_full_column_cover: bool = True,
    namespace: Optional[Any] = None,
) -> None:
    """Fail loudly on a stage sequence that cannot be correct.

    Raises ``RuntimeError`` — not ``AssertionError`` — so the check survives
    ``python -O`` and reads as a startup failure rather than a test artefact.

    ``namespace``, when given, is the module the adapters must resolve on; the
    orchestrator passes itself once its adapters are defined. Left ``None`` the
    adapter existence check is skipped (this module cannot import the
    orchestrator without a cycle, so the import-time run checks the declarative
    half and the orchestrator completes it).
    """
    stages = tuple(stages)
    available: set[str] = set(PRELOADED_INPUTS)
    seen_names: set[str] = set()
    written_by: dict[str, str] = {}

    for stage in stages:
        if stage.name in seen_names:
            raise RuntimeError(
                f"analysis stage registry: duplicate stage name {stage.name!r}."
            )
        seen_names.add(stage.name)

        for name in stage.reads:
            if name not in _COLUMN_NAMES and name not in SCRATCH_NAMES and name not in PRELOADED_INPUTS:
                raise RuntimeError(
                    f"analysis stage registry: stage {stage.name!r} reads {name!r}, "
                    f"which is neither a load-phase input, a declared scratch "
                    f"intermediate, nor a DerivedMetric field."
                )
            if name not in available:
                raise RuntimeError(
                    f"analysis stage registry: stage {stage.name!r} reads {name!r} "
                    f"before any earlier stage writes it. Either move the stage "
                    f"after its producer or correct the declaration — this is the "
                    f"ordering violation that used to produce a quietly wrong row."
                )

        for name in stage.writes:
            if name not in _COLUMN_NAMES and name not in SCRATCH_NAMES:
                raise RuntimeError(
                    f"analysis stage registry: stage {stage.name!r} writes {name!r}, "
                    f"which is neither a DerivedMetric field nor a declared scratch "
                    f"intermediate."
                )
            if name in written_by:
                raise RuntimeError(
                    f"analysis stage registry: {name!r} is written by more than one "
                    f"stage ({written_by[name]!r} and {stage.name!r}); the row's "
                    f"provenance must be unambiguous."
                )
            written_by[name] = stage.name

        available |= set(stage.writes)

        if namespace is not None and not callable(getattr(namespace, stage.adapter, None)):
            raise RuntimeError(
                f"analysis stage registry: stage {stage.name!r} names adapter "
                f"{stage.adapter!r}, which is not callable on "
                f"{getattr(namespace, '__name__', namespace)!r}."
            )

    if require_full_column_cover:
        covered = set(written_by) - set(SCRATCH_NAMES)
        missing = _COLUMN_NAMES - covered
        if missing:
            raise RuntimeError(
                f"analysis stage registry: no stage writes {sorted(missing)!r}. "
                f"Every analytical column must be claimed by exactly one stage, "
                f"or it ships as a silent default."
            )


def run_stages(namespace: Any, ctx: Any, stages: Iterable[Stage] = ANALYSIS_STAGES) -> None:
    """Run the declared sequence against ``ctx``.

    Each adapter is resolved off ``namespace`` BY NAME at call time, so a test
    that patches the orchestrator module intercepts the stage — including the
    four that used to be lazily imported inside the function body.

    After each stage, fields it did not declare are checked to be untouched (by
    identity, so a stage mutating its own JSON value in place is fine). An
    undeclared write is a bug that produces a row nobody can trace, so it raises
    rather than persisting.
    """
    for stage in stages:
        before = ctx.snapshot_identities(exclude=stage.writes)
        adapter = getattr(namespace, stage.adapter, None)
        if not callable(adapter):
            raise RuntimeError(
                f"analysis stage {stage.name!r}: adapter {stage.adapter!r} is not "
                f"callable on {getattr(namespace, '__name__', namespace)!r}."
            )
        ctx._allowed_reads = frozenset(stage.reads)
        try:
            adapter(ctx)
        finally:
            ctx._allowed_reads = None
        after = ctx.snapshot_identities(exclude=stage.writes)
        changed = [k for k, v in before.items() if after[k] is not v]
        if changed:
            raise RuntimeError(
                f"analysis stage {stage.name!r} wrote undeclared field(s) "
                f"{sorted(changed)!r}; declared writes are {list(stage.writes)!r}."
            )


# The declarative half runs at import, the same move `PACK_SECTIONS` makes: a
# skewed sequence becomes a startup failure rather than a silently wrong row.
assert_stage_contract(ANALYSIS_STAGES)
_CONTRACT_CHECKED = True
