import logging
import uuid
from typing import Optional
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

logger = logging.getLogger(__name__)

def _extract_planned_workout(check_in) -> dict | None:
    """
    Extract structured planned workout from check-in data.
    Returns None if no planned workout is specified.

    Future: this will come from a dedicated planned_workout field.
    For now, returns None (no planned workout capture yet).
    """
    # Planned workout capture is not yet implemented in the UI.
    # When it is, this function will parse the structured input.
    return None

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
        from app.services.analysis.baseline import recompute_runner_baseline
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

      A. Load phase (steps 1-4): activity, recent history, streams, check-in,
         profile-derived max_hr / HR zones. Pure DB reads.
      B. Pre-commit metrics phase (steps 5-9.6): build the typed
         `DerivedMetricFields` accumulator (`state`, #701) from the pure
         analysis stages, then upsert + commit `state.to_columns()`. This phase
         carries the two real intra-phase ordering invariants, both flagged
         INVARIANT inline below:
           1. The interval-structure PROBE precedes classification, and the
              probed structure feeds the classifier's structure axis.
           2. The single interval-session gate (`IntervalSession.gate`, the ONE
              owner of "is this an interval session") GATES interval
              matching/KPIs and the interval confidence checks.
      C. Post-commit phase (step 11): the runner-baseline recompute, which by
         INVARIANT must follow the commit (see `_post_commit_baseline`).

    A new stage should be placed in the phase whose inputs it depends on, and
    should state any new ordering dependency here rather than relying on
    placement.
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

    # 5. Compute metrics. Pass the check-in RPE so the training-load primitive
    # (#186) can fall back to session-RPE on a strap-less run.
    #
    # The pure metrics stage still returns its own dict (its external contract);
    # the orchestrator seeds the typed accumulator (`state`, #701) from it and
    # writes every subsequent stage's output as a typed attribute rather than a
    # string key. `state.to_columns()` reproduces the exact upsert dict.
    state = DerivedMetricFields.from_base_metrics(
        compute_derived_metrics_data(
            activity,
            streams_dict,
            max_hr=max_hr,
            rpe=check_in.rpe if check_in else None,
            zone_boundaries=zone_boundaries,
        )
    )

    # 6. Classify into orthogonal axes (ADR 0007). Probe interval structure so
    # the structure axis is data-driven; a probed structure is the "repeated
    # work/rest" half of the predicate (the other half, genuine pace
    # variability, is applied in the classifier).
    #
    # ORDERING INVARIANT 1 (probe-before-classify): the interval-structure probe
    # must run BEFORE classify_activity, because the classifier consumes
    # `has_interval_structure` to resolve its structure axis. Reordering these
    # two would feed the classifier a stale/missing structure signal.
    #
    # Prefer the runner's recorded laps (their own lap-button marks) as the
    # authoritative segmentation; fall back to the stream heuristic only when no
    # usable laps are present (#170). The stream re-segmentation can smear short
    # reps, drop a recorded warmup, and fabricate rep variability that the
    # recorded laps do not contain.
    lap_structure = detect_intervals_from_laps(activity.raw_summary, streams_dict, max_hr=max_hr)
    stream_structure = detect_intervals(streams_dict, "Intervals", max_hr=max_hr) if streams_dict else None
    probed_structure = lap_structure or stream_structure
    classification = classify_activity(
        activity, history,
        time_in_zones=state.time_in_zones,
        pace_variability=state.pace_variability,
        has_interval_structure=probed_structure is not None,
        max_hr=max_hr,
    )
    state.effort = classification.effort
    state.duration_class = classification.duration_class
    state.structure = classification.structure
    state.is_hilly = classification.is_hilly
    state.is_race = classification.is_race

    # 6.5 Interval segmentation — resolve the single "is this an interval
    # session" gate (#701). IntervalSession.gate keeps the probed structure only
    # when the classifier's structure axis resolved to intervals; it is the ONE
    # owner of that concept.
    #
    # ORDERING INVARIANT 2 (structure-axis gates interval KPIs): the gate is the
    # single source every downstream interval-only stage keys off — 6.6 workout
    # matching, 6.7 KPIs, and the interval confidence checks in
    # compute_confidence all read `interval_session.structure`. It must be
    # resolved here, AFTER classification and BEFORE those stages read it.
    interval_session = IntervalSession.gate(classification.structure, probed_structure)
    state.interval_structure = interval_session.structure

    # 6.6 Workout matching — compare planned vs detected
    planned_workout = _extract_planned_workout(check_in)
    workout_match = match_planned_to_detected(interval_session.structure, planned_workout)
    state.workout_match = workout_match

    # 6.7 Interval-specific KPIs, gated on the structure axis via the
    # interval_session gate (INVARIANT 2).
    if interval_session.is_session:
        zones_calibrated = bool(
            profile and (profile.hr_zones or (profile.max_hr and profile.max_hr > 100))
        )
        state.interval_kpis = build_interval_kpis(
            interval_session.structure,
            max_hr=max_hr,
            zones_calibrated=zones_calibrated,
            time_in_zones=state.time_in_zones,
        )
    else:
        state.interval_kpis = None

    # 7. Load history metrics for load spike detection
    history_metrics = (
        db.query(DerivedMetric)
        .filter(DerivedMetric.activity_id.in_([h.id for h in history]))
        .all()
        if history else []
    )

    # 8. Flags (all flag logic consolidated in flags.py). flags.py reads the
    # accumulator as a dict; state.to_columns() presents the current typed
    # values in that shape (later-stage fields are still at their defaults and
    # unread here).
    all_flags = generate_flags(
        activity, state.to_columns(), history, check_in,
        history_metrics=history_metrics,
    )
    state.flags = all_flags

    # 8.5 Risk score (deterministic, based on flags + check-in + training context)
    check_in_data = {
        "sleep_quality": check_in.sleep_quality if check_in else None,
        "rpe": check_in.rpe if check_in else None,
    }
    # Compute training context for risk scoring; persist so the coach pipeline
    # can read it instead of recomputing.
    from app.services.analysis._training_context import build_training_context
    training_ctx = build_training_context(db, activity)
    state.training_context = training_ctx
    risk_result = compute_risk_score(all_flags, check_in_data, training_ctx)
    state.risk_level = risk_result["risk_level"]
    state.risk_score = risk_result["risk_score"]
    state.risk_reasons = risk_result["risk_reasons"]

    # 9. Confidence (with interval sanity checks). Consumes the single owner's
    # verdict itself (#701/#739), so the interval-only reasons stay confined to
    # an interval session (#169) and the type flows the whole way through.
    confidence, confidence_reasons = compute_confidence(
        activity, streams_dict, check_in,
        interval_session=interval_session,
        workout_match=workout_match,
    )
    state.confidence = confidence
    state.confidence_reasons = confidence_reasons

    # 9.5 Discount signals (N4) — deterministic confounder annotation. Flags
    # when HR drift is likely inflated by heat/terrain/stimulant rather than
    # genuine fatigue. Reuses the profile loaded for max_hr above.
    from app.services.analysis.discount_signals import compute_discount_signals
    state.discount_signals = compute_discount_signals(
        average_temp=activity.raw_summary.get("average_temp") if activity.raw_summary else None,
        is_hilly=state.is_hilly,
        hr_drift=state.hr_drift,
        stimulant_use=profile.stimulant_use if profile else None,
    )

    # 9.6 Consolidated stream view (A2a processed-artifacts layer) — a small,
    # downsampled, aligned HR/pace/grade/cadence snapshot derived from the same
    # streams_dict. Stored on the DerivedMetric so retrieval is cheap; re-derived
    # every analysis (self-heals on re-sync/backfill), a convenience view that
    # never overrides the re-derived metrics. Degrades to None without streams.
    from app.services.analysis.stream_view import build_stream_view
    state.stream_view = build_stream_view(streams_dict)

    # 10. Upsert DerivedMetric. state.to_columns() reproduces the exact dict the
    # upsert consumed before the typed-intermediate refactor (#701).
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

    # 11. Post-commit phase. ORDERING INVARIANT 3 (commit-before-baseline): the
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
