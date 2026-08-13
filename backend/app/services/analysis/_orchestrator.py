import logging
import sys
import uuid
from dataclasses import fields as dataclass_fields
from typing import Any, Optional
from sqlalchemy.orm import Session, undefer
from sqlalchemy import select

from app.models import Activity, DerivedMetric, CheckIn, ActivityStream, StravaAccount, UserProfile
from app.services.analysis.metrics import compute_derived_metrics_data
from app.services.analysis.classifier import classify_activity
from app.services.analysis.flags import generate_flags
from app.services.analysis.intervals import detect_intervals, detect_intervals_from_laps
from app.services.analysis.risk import compute_risk_score
from app.services.analysis.workout_matching import match_planned_to_detected, build_interval_kpis
from app.services.analysis.composition import DerivedMetricFields, IntervalSession

# Eagerly imported (#805). These four stage functions used to be imported LAZILY
# inside the function body, which put them outside this module's namespace — the
# one seam every composition test patches — so no test could intercept them.
# None of the four imports anything that reaches back here, so the lazy form was
# never breaking a cycle; it was only hiding a stage.
from app.services.analysis._training_context import build_training_context
from app.services.analysis.discount_signals import compute_discount_signals
from app.services.analysis.stream_view import build_stream_view
from app.services.analysis.baseline import recompute_runner_baseline
from app.services.analysis.stages import (
    ANALYSIS_STAGES,
    StageContext,
    assert_stage_contract,
    run_stages,
)

logger = logging.getLogger(__name__)

# The column-valued reads the flags stage DECLARED, derived from the registry so
# the projection and the declaration cannot skew. `generate_flags` used to be
# handed all 23 accumulator fields — eight of them still at their defaults.
_FLAGS_METRIC_READS = tuple(
    name
    for stage in ANALYSIS_STAGES
    if stage.name == "flags"
    for name in stage.reads
    if name in {f.name for f in dataclass_fields(DerivedMetricFields)}
)

def _resolve_planned_session(db: Session, activity) -> Any:
    """The planned session this activity most plausibly is, or None.

    Imported lazily: the schedule is a higher layer than analysis, and a
    top-level import would point the dependency the wrong way for a lookup that
    is absent for every runner without a plan. Never fatal — a schedule that
    cannot answer must not stop a run being analysed.
    """
    try:
        from app.services.schedule.completion import find_matching_session

        return find_matching_session(db, activity)
    except Exception:  # noqa: BLE001 — analysis must survive a schedule fault
        logger.exception(
            "planned-session lookup failed for activity %s; analysing without a plan",
            getattr(activity, "id", None),
        )
        return None


def _extract_planned_workout(planned_session) -> dict | None:
    """The rep structure this activity was PLANNED to have, or None.

    This was a placeholder returning None from the beginning of the project
    until #830. `workout_matching.match_planned_to_detected` has compared a plan
    to detected reps all along and had never once been handed one, so its
    plan-scoring block was unreachable through `analyze()` and with it one branch
    of `compute_confidence` — the `match_score < 0.7` test that appends the
    CRITICAL `interval_structure_mismatch` reason. `tests/test_analysis_stages.py`
    pinned that dormancy precisely so the day a real plan arrived the pin would
    fail and the branch would be revisited deliberately rather than waking up
    unnoticed. That day is this one.

    The session is resolved in the load phase (`schedule.completion`), so this
    stays a pure projection.

    A REP COUNT is what makes a plan scoreable, and since #878 a session's
    `structure` may exist without one: a tempo run carries a warm-up and a
    cool-down and no reps. `match_planned_to_detected` scores whatever it is
    handed, and against an edges-only plan it finds nothing to compare, scores
    0.0, and `compute_confidence` then appends the CRITICAL
    `interval_structure_mismatch` — so a tempo run with a warm-up would be
    reported as a botched interval session. A plan with no reps in it is not a
    rep plan, and saying so here is cheaper than teaching every reader of
    `structure` to ask.
    """
    if planned_session is None:
        return None
    structure = getattr(planned_session, "structure", None) or None
    if structure and not structure.get("reps_planned"):
        return None
    return structure

async def analyze_with_streams(db: Session, activity_id: str) -> Optional[DerivedMetric]:
    """
    Explicitly fetches streams and re-runs analysis.
    """
    activity_uuid = _coerce_uuid(activity_id)
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if not activity: return None

    # Fetch streams
    account = db.query(StravaAccount).filter(StravaAccount.user_id == activity.user_id).first()
    if account:
        from app.services.strava_ingestion import get_strava_port, refetch_streams

        await refetch_streams(db, account, activity, get_strava_port())

    # Run normal processing (which now picks up streams)
    return analyze(db, activity_id)


def _coerce_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def compute_confidence(
    activity,
    streams_dict,
    check_in,
    interval_session: IntervalSession,
    workout_match=None,
):
    """
    Determine confidence level and reasons based on available data.
    Includes interval-specific sanity checks when applicable.
    Returns (level, reasons) tuple.

    `interval_session` is the typed output of the ONE gate (#701/#739). Taking
    the `IntervalSession` itself rather than a raw structure dict means this
    function cannot re-derive "is this an interval session" independently, and
    is required so a caller must have consulted the gate to call at all. Pass
    `IntervalSession(structure=None)` for a non-interval activity.
    """
    reasons = []

    if not activity.avg_hr:
        reasons.append("no_heart_rate_data")
    if not streams_dict:
        reasons.append("no_stream_data")
    elif not streams_dict.get("latlng"):
        reasons.append("no_gps_data")
    if not check_in:
        reasons.append("no_user_checkin")

    # Interval-specific sanity checks. These reasons (no_intervals_detected,
    # rep variability, match mismatch) only describe an interval session, so
    # they must not lower the overall confidence of a non-interval activity
    # (#169). The single owner's verdict (`IntervalSession.is_session`, #701) IS
    # the gate: this function makes no independent session decision, and for a
    # non-session workout_match's reasons stay confined to workout_match.
    if interval_session.is_session and workout_match:
        match_reasons = workout_match.get("confidence_reasons", [])
        for r in match_reasons:
            if r not in reasons:
                reasons.append(r)

        match_score = workout_match.get("match_score")
        if match_score is not None and match_score < 0.7:
            reasons.append("interval_structure_mismatch")

    # These are stream-detection sanity checks: they only make sense when the
    # structure was *inferred* from the velocity stream. Recorded laps are the
    # runner's own ground-truth segmentation (#170), so "implausibly high" work
    # time is just a long session the runner marked, and a missing warmup means
    # they genuinely started at rep 1 — neither is a confidence deficiency.
    structure = interval_session.structure
    if interval_session.is_session and structure.get("source") != "recorded_laps":
        summary = structure.get("summary", {})
        # Check for implausible total work time (> 45 min of hard running)
        total_work = summary.get("total_work_time_s", 0)
        if total_work > 2700:
            reasons.append("work_time_implausibly_high")

        # Check warmup/cooldown detection
        if not structure.get("warmup_duration_s"):
            reasons.append("no_warmup_detected")

    # Determine level — more reasons = lower confidence
    critical = {"no_heart_rate_data", "no_stream_data", "interval_structure_mismatch",
                "work_time_implausibly_high", "high_rep_distance_variability"}
    critical_hits = critical & set(reasons)

    if len(critical_hits) >= 2:
        level = "low"
    elif len(critical_hits) >= 1 or len(reasons) >= 3:
        level = "medium"
    elif len(reasons) == 0:
        level = "high"
    else:
        # 0 < len(reasons) < 3 and no critical hits.
        level = "medium"

    return level, reasons


# --------------------------------------------------------------------------
# Stage adapters (#805).
#
# One adapter per declared stage in `stages.ANALYSIS_STAGES`. Each is thin by
# design: it reads exactly what its declaration names, calls the pure stage, and
# writes exactly what its declaration names. The stage FUNCTIONS are untouched —
# this is composition, not stage logic.
#
# Every pure stage is called through this module's namespace (a plain global
# lookup), so `monkeypatch.setattr(_orchestrator, "<stage fn>", spy)` intercepts
# it. That is the seam the composition tests already use, and it now reaches the
# four stages that were previously imported lazily inside `analyze`.
# --------------------------------------------------------------------------

def _stage_base_metrics(ctx: StageContext) -> None:
    """Seed the accumulator from the pure metrics stage.

    The base stage returns its own dict (its external contract). Assigning the
    declared keys — rather than constructing the accumulator from **metrics —
    keeps the write set honest, so the strict check below preserves the loud
    failure the old `cls(**metrics)` gave on an unexpected key.
    """
    metrics = compute_derived_metrics_data(
        ctx.get("activity"),
        ctx.get("streams_dict"),
        max_hr=ctx.get("max_hr"),
        rpe=ctx.get("check_in").rpe if ctx.get("check_in") else None,
        zone_boundaries=ctx.get("zone_boundaries"),
    )
    declared = {
        name for stage in ANALYSIS_STAGES if stage.name == "base_metrics" for name in stage.writes
    }
    if set(metrics) != declared:
        raise TypeError(
            f"compute_derived_metrics_data returned {sorted(metrics)!r}, but the "
            f"base_metrics stage declares {sorted(declared)!r}."
        )
    for name, value in metrics.items():
        ctx.set(name, value)


def _stage_interval_probe(ctx: StageContext) -> None:
    """Probe interval structure. INVARIANT 1's producer: the classifier consumes
    this, so the declaration (not the placement) is what orders the two.

    Prefer the runner's recorded laps (their own lap-button marks) as the
    authoritative segmentation; fall back to the stream heuristic only when no
    usable laps are present (#170). The stream re-segmentation can smear short
    reps, drop a recorded warmup, and fabricate rep variability that the recorded
    laps do not contain.
    """
    lap_structure = detect_intervals_from_laps(
        ctx.get("activity").raw_summary, ctx.get("streams_dict"), max_hr=ctx.get("max_hr")
    )
    stream_structure = (
        detect_intervals(ctx.get("streams_dict"), "Intervals", max_hr=ctx.get("max_hr"))
        if ctx.get("streams_dict")
        else None
    )
    ctx.set("probed_structure", lap_structure or stream_structure)


def _stage_classification(ctx: StageContext) -> None:
    """Classify into orthogonal axes (ADR 0007). A probed structure is the
    "repeated work/rest" half of the structure predicate; the other half (genuine
    pace variability) is applied inside the classifier."""
    classification = classify_activity(
        ctx.get("activity"),
        ctx.get("history"),
        time_in_zones=ctx.get("time_in_zones"),
        pace_variability=ctx.get("pace_variability"),
        has_interval_structure=ctx.get("probed_structure") is not None,
        max_hr=ctx.get("max_hr"),
    )
    ctx.set("effort", classification.effort)
    ctx.set("duration_class", classification.duration_class)
    ctx.set("structure", classification.structure)
    ctx.set("is_hilly", classification.is_hilly)
    ctx.set("is_race", classification.is_race)


def _stage_interval_gate(ctx: StageContext) -> None:
    """Resolve the single "is this an interval session" gate (#701).

    INVARIANT 2's producer. Every interval-only stage below declares a read of
    `interval_session`, so none can run before this and none can re-derive the
    concept independently.
    """
    interval_session = IntervalSession.gate(
        ctx.get("structure"), ctx.get("probed_structure")
    )
    ctx.set("interval_session", interval_session)
    ctx.set("interval_structure", interval_session.structure)


def _stage_workout_match(ctx: StageContext) -> None:
    """Compare planned vs detected.

    Live since #830: `planned_session` is resolved in the load phase, so this
    stage now runs its plan-scoring path whenever the runner had a planned
    session with rep structure covering this activity. Without a schedule — or on
    a session the coach prescribed no reps for — it still runs the no-plan path
    exactly as before.
    """
    planned_workout = _extract_planned_workout(ctx.get("planned_session"))
    ctx.set(
        "workout_match",
        match_planned_to_detected(ctx.get("interval_session").structure, planned_workout),
    )


def _stage_interval_kpis(ctx: StageContext) -> None:
    """Interval-specific KPIs, gated on the structure axis via the session gate."""
    interval_session = ctx.get("interval_session")
    if not interval_session.is_session:
        ctx.set("interval_kpis", None)
        return
    profile = ctx.get("profile")
    zones_calibrated = bool(
        profile and (profile.hr_zones or (profile.max_hr and profile.max_hr > 100))
    )
    ctx.set(
        "interval_kpis",
        build_interval_kpis(
            interval_session.structure,
            max_hr=ctx.get("max_hr"),
            zones_calibrated=zones_calibrated,
            time_in_zones=ctx.get("time_in_zones"),
        ),
    )


def _stage_history_metrics(ctx: StageContext) -> None:
    """Load prior DerivedMetric rows for load-spike detection."""
    history = ctx.get("history")
    ctx.set(
        "history_metrics",
        (
            ctx.get("db").query(DerivedMetric)
            .filter(DerivedMetric.activity_id.in_([h.id for h in history]))
            .all()
            if history
            else []
        ),
    )


def _stage_flags(ctx: StageContext) -> None:
    """All flag logic is consolidated in flags.py; it consumes the accumulator as
    a dict. It now receives a projection of the five fields the stage DECLARES,
    not the whole 23-field accumulator with eight later stages' defaults in it."""
    ctx.set(
        "flags",
        generate_flags(
            ctx.get("activity"),
            ctx.project(_FLAGS_METRIC_READS),
            ctx.get("history"),
            ctx.get("check_in"),
            history_metrics=ctx.get("history_metrics"),
        ),
    )


def _stage_training_context(ctx: StageContext) -> None:
    """Compute the training context for risk scoring; persisted so the coach
    pipeline can read it instead of recomputing (ADR 0001)."""
    ctx.set("training_context", build_training_context(ctx.get("db"), ctx.get("activity")))


def _stage_risk(ctx: StageContext) -> None:
    """Deterministic risk score from flags + check-in + training context."""
    check_in = ctx.get("check_in")
    check_in_data = {
        "sleep_quality": check_in.sleep_quality if check_in else None,
        "rpe": check_in.rpe if check_in else None,
    }
    risk_result = compute_risk_score(
        ctx.get("flags"), check_in_data, ctx.get("training_context")
    )
    ctx.set("risk_level", risk_result["risk_level"])
    ctx.set("risk_score", risk_result["risk_score"])
    ctx.set("risk_reasons", risk_result["risk_reasons"])


def _stage_confidence(ctx: StageContext) -> None:
    """Confidence, consuming the single gate's own verdict (#701/#739) so the
    interval-only reasons stay confined to an interval session (#169)."""
    confidence, confidence_reasons = compute_confidence(
        ctx.get("activity"),
        ctx.get("streams_dict"),
        ctx.get("check_in"),
        interval_session=ctx.get("interval_session"),
        workout_match=ctx.get("workout_match"),
    )
    ctx.set("confidence", confidence)
    ctx.set("confidence_reasons", confidence_reasons)


def _stage_discount_signals(ctx: StageContext) -> None:
    """Discount signals (N4) — deterministic confounder annotation. Flags when HR
    drift is likely inflated by heat/terrain/stimulant rather than genuine
    fatigue."""
    activity = ctx.get("activity")
    profile = ctx.get("profile")
    ctx.set(
        "discount_signals",
        compute_discount_signals(
            average_temp=activity.raw_summary.get("average_temp") if activity.raw_summary else None,
            is_hilly=ctx.get("is_hilly"),
            hr_drift=ctx.get("hr_drift"),
            stimulant_use=profile.stimulant_use if profile else None,
        ),
    )


def _stage_stream_view(ctx: StageContext) -> None:
    """Consolidated stream view (A2a) — a small, downsampled, aligned
    HR/pace/grade/cadence snapshot re-derived every analysis (so it self-heals on
    re-sync/backfill). Degrades to None without streams."""
    ctx.set("stream_view", build_stream_view(ctx.get("streams_dict")))


# Complete the import-time check now the adapters exist: every declared stage
# must resolve to a callable on THIS module. A renamed adapter is a startup
# failure, not a runtime AttributeError halfway through an analysis.
assert_stage_contract(ANALYSIS_STAGES, namespace=sys.modules[__name__])


def _post_commit_baseline(db: Session, user_id) -> None:
    """Post-commit phase: recompute the runner baseline trend substrate (M2).

    ORDERING INVARIANT: this MUST run AFTER the DerivedMetric commit. The
    baseline is recomputed over a rolling window that includes the
    just-analysed activity, so the activity's DerivedMetric has to be visible
    in the DB before this reads. Calling it before the commit would silently
    exclude the current run from its own baseline.

    Best-effort by contract: the baseline is a derived convenience, not part of
    the activity's own analysis result, so a failure here is logged and
    swallowed and never breaks analyze().
    """
    try:
        recompute_runner_baseline(db, user_id)
    except Exception as exc:  # noqa: BLE001 baseline is best-effort, must not break analyze
        # Keep the guard (a failure here never breaks analyze), but make a genuine
        # failure VISIBLE: a legitimate "not enough data" abstention returns cleanly
        # from recompute_runner_baseline (None) without raising, so reaching here is
        # always a real fault (a malformed stored bucketed_trends, a bad value type).
        # Log at ERROR with context and route it to Sentry (a no-op when
        # unconfigured) so corruption surfaces instead of silently freezing the
        # baseline at its last good value (#513).
        from app.core.observability import capture_exception
        logger.exception("runner baseline recompute failed for user %s", user_id)
        capture_exception(exc)


def analyze(db: Session, activity_id: str, skip_baseline: bool = False) -> Optional[DerivedMetric]:
    """
    Main entry point.
    Loads activity, history, computes all metrics, saves DerivedMetric.

    How a DerivedMetric is assembled (the composition contract):

      A. Load phase: activity, recent history, streams, check-in, profile-derived
         max_hr / HR zones. Pure DB reads, and the only thing this function still
         does by hand.
      B. Stage phase: `stages.ANALYSIS_STAGES` — the declared sequence — is run
         against a `StageContext`, then `state.to_columns()` is upserted and
         committed. Each stage names what it READS and what it WRITES, so the
         two intra-phase ordering invariants that used to be prose are now
         checkable declarations enforced AT IMPORT (#805):
           1. probe-before-classify: `interval_probe` writes `probed_structure`
              and `classification` reads it.
           2. the structure axis gates the interval-only stages: `interval_gate`
              reads `structure` and writes `interval_session`, which workout
              matching, KPIs and the confidence stage all read.
      C. Post-commit phase: the runner-baseline recompute, which by INVARIANT
         must follow the commit (see `_post_commit_baseline`). This one stays
         prose because it is an ordering constraint against the COMMIT, not
         against another stage's output, so the read/write graph cannot express
         it.

    A new stage is added to the registry in `stages.py` with its reads and
    writes declared. Placement no longer carries the contract.
    """
    activity_uuid = _coerce_uuid(activity_id)

    # 1. Load Activity. undefer raw_summary (#359): analyze reads it (lap-based
    # interval detection + average_temp), so load it with the row.
    stmt = (
        select(Activity)
        .where(Activity.id == activity_uuid)
        .options(undefer(Activity.raw_summary))
    )
    activity = db.execute(stmt).scalars().first()
    if not activity:
        return None

    # 2. Load History (last 20 activities before this one). undefer raw_summary
    # (#359): the classifier calls _is_run(a) (-> sport_type -> raw_summary) on
    # every history row, so a deferred column would lazy-load per row -> N+1.
    history = db.query(Activity).options(undefer(Activity.raw_summary)).filter(
        Activity.user_id == activity.user_id,
        Activity.start_date < activity.start_date
    ).order_by(Activity.start_date.desc()).limit(20).all()

    # 2.5: Load Streams if available
    streams = db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).all()
    streams_dict = {s.stream_type: s.data for s in streams}

    # 3. Load CheckIn (if exists)
    check_in = db.query(CheckIn).filter(CheckIn.activity_id == activity.id).first()

    # 4. Fetch Profile for Max HR
    profile = db.query(UserProfile).filter(UserProfile.user_id == activity.user_id).first()
    max_hr = 190
    if profile and profile.max_hr and profile.max_hr > 100:
        max_hr = profile.max_hr

    # The runner's own Strava HR zones, when synced, drive the time-in-zone
    # binning so it matches Strava (#297). Absent, binning falls back to %max.
    zone_boundaries = profile.hr_zones if profile and profile.hr_zones else None

    # 5. Run the declared stage sequence (#805). The order is `ANALYSIS_STAGES`,
    # not this function body; each stage reads what it declared and writes what
    # it declared, and `run_stages` resolves the adapters off THIS module so a
    # test can intercept any of them.
    #
    # The accumulator starts blank: `base_metrics` is the stage that seeds it, so
    # it is subject to the same declaration as every other stage rather than
    # being a privileged constructor call. `state.to_columns()` still reproduces
    # the exact upsert dict (#701).
    # The planned session this activity satisfies, if the runner has a schedule.
    # Read here in the load phase alongside the check-in and the profile, so
    # every entry point — the pipeline, a re-analysis, the backfill — resolves it
    # the same way, and so the stage layer stays free of a service import.
    planned_session = _resolve_planned_session(db, activity)

    state = DerivedMetricFields(effort_score=0.0)
    ctx = StageContext(
        db=db,
        activity=activity,
        history=history,
        streams_dict=streams_dict,
        check_in=check_in,
        planned_session=planned_session,
        profile=profile,
        max_hr=max_hr,
        zone_boundaries=zone_boundaries,
        state=state,
    )
    run_stages(sys.modules[__name__], ctx)

    # 6. Upsert DerivedMetric.
    #
    # SURFACED, NOT CHANGED (#805): this writes all 23 columns unconditionally,
    # so a stage that abstains overwrites the prior value with its default rather
    # than leaving it. That is the CURRENT behaviour and changing it is a
    # behaviour change, not a refactor — see the issue's follow-up note.
    metric_columns = state.to_columns()
    existing_dm = db.query(DerivedMetric).filter(DerivedMetric.activity_id == activity.id).first()

    if existing_dm:
        for k, v in metric_columns.items():
            setattr(existing_dm, k, v)
        dm = existing_dm
    else:
        dm = DerivedMetric(activity_id=activity.id, **metric_columns)
        db.add(dm)

    db.commit()
    db.refresh(dm)

    # 7. Post-commit phase. ORDERING INVARIANT 3 (commit-before-baseline): the
    # runner-baseline recompute must follow the commit above so the
    # just-analysed activity is included in its rolling window. See
    # `_post_commit_baseline`.
    #
    # `skip_baseline` lets a bulk caller (the reanalyze job, #366) defer the
    # recompute to once-per-user after its whole batch instead of paying it on
    # every activity. recompute_runner_baseline is a full idempotent pass over
    # the rolling window, so the deferred result is identical. The per-activity
    # ingest path leaves it False and recomputes per activity as before.
    if not skip_baseline:
        _post_commit_baseline(db, activity.user_id)

    return dm
