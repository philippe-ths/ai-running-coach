"""
Context pack builder — assembles all facts the LLM needs into a typed pack.

No computation happens here. This module only gathers and shapes existing data
from the database (activity, metrics, check-in, profile, trends).

A2b reframes the assembly as a *working context* (CONTEXT.md): a lean
`B baseline` always present plus a trigger-scoped `focus payload` pulled for the
subject activity. `build_context_pack` composes the two into the flat
`CoachContextPack` the LLM, validator, cache, and eval gate consume — byte-for-
byte identical to the prior one-pass builder, so this is an internal restructure
with no behaviour change. Deeper per-activity detail (the consolidated stream
view) is reachable through the retrieval seam on demand, not forced into the
default pack.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Activity, RunnerBaseline, UserProfile
from app.models.checkin import CheckIn
from app.models.coach_chat_message import CoachChatMessage
from app.services.analysis.baseline import bucket_key
from app.services.analysis.classifier import Classification, compose_headline  # noqa: F401
from app.services.coach.adherence import CandidateActivity, build_adherence
from app.services.coach.belief_store import build_believed_facts, retrieve_beliefs
from app.services.coach.calibration import assess_referral, calibrate_drift
from app.services.coach.narrative_store import build_narrative_context
from app.services.coach.perceived_effort import build_perceived_effort
from app.services.coach.preference import build_preference_profile
from app.services.coach.retrieval import (
    fetch_prior_commitments,
    fetch_prior_digests,
    fetch_stream_view,
)
from app.schemas.coach_context import (
    ActivityContext,
    AdherenceContext,
    BaselineTrendDelta,
    BelievedFactsContext,
    CalibrationContext,
    CheckInContext,
    CoachContextPack,
    LongitudinalContext,
    MetricsContext,
    NarrativeContext,
    PerceivedEffortContext,
    PreferenceProfile,
    ProfileContext,
    RecentTrainingSummary,
    SafetyRules,
    TrainingPeriodSummary,
)
from app.services.trends import _query_activity_facts
from app.services.units.cadence import normalize_cadence_spm

# Defensive cap on prior check-ins scanned for the pain trend. Pain check-ins are
# sparse, so this comfortably covers the recent history the trend needs.
_PAIN_HISTORY_SCAN_LIMIT = 50

# Bound on prior activities scanned to find same-bucket comparables for the M9
# HR-drift calibration. Comfortably covers a recreational runner's recent history
# in any one context bucket while keeping the read bounded.
_CALIBRATION_SCAN_LIMIT = 60

# Recency window for the M9 persistent-pain referral: notable pain must recur
# WITHIN this many days to count as sustained, so old unrelated niggles do not
# accumulate into a false "persistent pain" referral (~6 weeks, a training block).
_PERSISTENT_PAIN_WINDOW_DAYS = 42

# Bound on subsequent activities scanned for the M7 adherence verdict (between
# the source report and this run, oldest-first). A report is generated per
# coached activity, so this window is normally 1-2 runs; the cap only guards the
# degenerate case of a long gap with no intervening reports. Taken oldest-first
# so "the next comparable run" is judged correctly.
_ADHERENCE_CANDIDATE_SCAN_LIMIT = 30

# Curated phrases that signal the runner explicitly pushed back on the prior
# report's advice (in a check-in note or a chat reply on that activity). A
# disputed outcome is treated as settled and the prompt says nothing about it,
# so a false positive only SUPPRESSES an adherence note (the safe direction); it
# never fabricates a "you were wrong" exchange. Known blind spot (deterministic
# v1, mirroring the M5 discipline): pushback phrased outside this list is missed,
# so a wrong implicit label can survive — bounded by the advisory, non-accusatory
# framing of rule 18 and the comparable/opportunity gates upstream.
_PUSHBACK_PHRASES = (
    "that was off", "that's off", "thats off", "was off", "way off",
    "that's wrong", "thats wrong", "is wrong", "was wrong", "were wrong",
    "not right", "not accurate",
    "inaccurate", "incorrect", "disagree", "didn't feel", "did not feel",
    "felt wrong", "that's not right", "thats not right", "not how it felt",
    "doesn't match", "wasn't easy", "wasn't right", "you're wrong",
)


# --- Working context: B baseline + focus payload (A2b) ----------------------
# The assembled view, lean by default. These are internal assembly types; the
# emitted artifact stays the flat CoachContextPack that build_context_pack
# flattens them into.


@dataclass(frozen=True)
class BBaseline:
    """The always-present relationship slice for one exchange: the runner's
    profile and recent-load rollups, the durable-memory narrative (voice), the
    last exchange's digest + matching baseline trend, this run's subjective
    signals, the deterministic durable facts (adherence, beliefs, calibration,
    preference), and the safety rules."""

    profile: ProfileContext
    recent_training_summary: RecentTrainingSummary
    longitudinal: LongitudinalContext
    perceived_effort: PerceivedEffortContext
    adherence: AdherenceContext
    believed_facts: BelievedFactsContext
    calibration: CalibrationContext
    preference_profile: PreferenceProfile
    narrative: NarrativeContext
    safety_rules: SafetyRules


@dataclass(frozen=True)
class FocusPayload:
    """The trigger-scoped detail of the subject activity: its own measured facts
    and check-in. `stream_view` is the deep, pull-on-demand artifact — None on the
    default report path (so the deferred column is never loaded), populated only
    when an exchange deep-dives the subject."""

    activity: ActivityContext
    metrics: MetricsContext
    check_in: CheckInContext
    stream_view: Optional[dict] = None


@dataclass(frozen=True)
class WorkingContext:
    """The lean view assembled per exchange (a view, not a store): a B baseline
    always present plus the subject's focus payload."""

    b_baseline: BBaseline
    focus: FocusPayload


def build_context_pack(db: Session, activity: Activity) -> CoachContextPack:
    """Assemble all facts the LLM needs. No computation, just data gathering.

    Composes the working context (B baseline + the subject's focus payload) into
    the flat CoachContextPack. Byte-identical to the prior one-pass builder.
    """
    wc = assemble_working_context(db, activity)
    b, f = wc.b_baseline, wc.focus
    return CoachContextPack(
        activity=f.activity,
        metrics=f.metrics,
        check_in=f.check_in,
        profile=b.profile,
        recent_training_summary=b.recent_training_summary,
        longitudinal=b.longitudinal,
        perceived_effort=b.perceived_effort,
        adherence=b.adherence,
        believed_facts=b.believed_facts,
        calibration=b.calibration,
        preference_profile=b.preference_profile,
        narrative=b.narrative,
        safety_rules=b.safety_rules,
    )


def assemble_working_context(
    db: Session, activity: Activity, *, deep: bool = False
) -> WorkingContext:
    """Assemble the lean working context for an exchange anchored to `activity`.

    Loads the runner's profile once and shares it across both halves. `deep` pulls
    the subject's consolidated stream view into the focus payload; the default
    post-activity exchange leaves it out (lean by default, deep on demand).
    """
    profile = _load_profile(db, activity.user_id)
    return WorkingContext(
        b_baseline=build_b_baseline(db, activity, profile=profile),
        focus=build_focus_payload(db, activity, profile=profile, deep=deep),
    )


def build_focus_payload(
    db: Session,
    subject: Activity,
    *,
    profile: Optional[UserProfile] = None,
    deep: bool = False,
) -> FocusPayload:
    """The subject activity's own detail, assembled on demand.

    Works for ANY subject activity, not only the current run, so a conversation
    can deep-dive a specific past activity (the memory half of subject
    resolution). When `deep`, also pulls the consolidated stream view through the
    retrieval seam; otherwise `stream_view` stays None and the deferred
    DerivedMetric.stream_view column is never loaded, keeping the default build
    lean (A2a).
    """
    metrics = subject.metrics
    check_in = getattr(subject, "check_in", None)
    if profile is None:
        profile = _load_profile(db, subject.user_id)

    # Zone calibration: only true if user explicitly set max_hr with a known source
    has_explicit_max_hr = bool(
        profile
        and profile.max_hr
        and profile.max_hr > 100
        and getattr(profile, "max_hr_source", None)  # must have a source
    )
    zones_calibrated = has_explicit_max_hr
    if has_explicit_max_hr:
        zones_basis = f"user_{profile.max_hr_source}"
    else:
        zones_basis = "uncalibrated"

    # Training context: intensity distribution and recency signals (persisted
    # by the analysis pipeline on DerivedMetric).
    training_context = metrics.training_context if metrics else None

    return FocusPayload(
        activity=ActivityContext(
            date=subject.start_date.isoformat(),
            name=subject.name,
            type=subject.user_intent or subject.type,
            distance_m=subject.distance_m,
            moving_time_s=subject.moving_time_s,
            avg_hr=subject.avg_hr,
            max_hr=subject.max_hr,
            avg_cadence=normalize_cadence_spm(
                subject.user_intent or subject.type, subject.avg_cadence
            ),
            elev_gain_m=subject.elev_gain_m,
        ),
        metrics=MetricsContext(
            headline=compose_headline(subject, Classification.from_metrics(metrics)),
            effort=metrics.effort if metrics else None,
            duration_class=metrics.duration_class if metrics else None,
            structure=metrics.structure if metrics else None,
            is_hilly=metrics.is_hilly if metrics else None,
            is_race=metrics.is_race if metrics else None,
            effort_score=round(metrics.effort_score, 1) if metrics else None,
            hr_drift=round(metrics.hr_drift, 1) if metrics and metrics.hr_drift else None,
            pace_variability=(
                round(metrics.pace_variability, 1)
                if metrics and metrics.pace_variability
                else None
            ),
            flags=metrics.flags if metrics else [],
            confidence=metrics.confidence if metrics else "low",
            confidence_reasons=metrics.confidence_reasons if metrics else [],
            time_in_zones=metrics.time_in_zones if metrics else None,
            zones_calibrated=zones_calibrated,
            zones_basis=zones_basis,
            efficiency_analysis=metrics.efficiency_analysis if metrics else None,
            stops_analysis=metrics.stops_analysis if metrics else None,
            interval_structure=metrics.interval_structure if metrics else None,
            workout_match=metrics.workout_match if metrics else None,
            interval_kpis=metrics.interval_kpis if metrics else None,
            risk_level=metrics.risk_level if metrics else None,
            risk_score=metrics.risk_score if metrics else None,
            risk_reasons=metrics.risk_reasons if metrics else [],
            training_context=training_context,
            discount_signals=metrics.discount_signals if metrics else None,
        ),
        check_in=CheckInContext(
            rpe=check_in.rpe if check_in else None,
            pain_score=check_in.pain_score if check_in else None,
            pain_location=check_in.pain_location if check_in else None,
            sleep_quality=check_in.sleep_quality if check_in else None,
            notes=check_in.notes if check_in else None,
        ),
        stream_view=fetch_stream_view(db, subject.id) if deep else None,
    )


def build_b_baseline(
    db: Session, activity: Activity, *, profile: Optional[UserProfile] = None
) -> BBaseline:
    """The always-present relationship slice for an exchange anchored to
    `activity`. Lean by design: rollups and digests, not deep per-activity detail
    (that lives in the focus payload). Each section reads existing rows only and
    degrades cleanly to empty/None."""
    metrics = activity.metrics
    check_in = getattr(activity, "check_in", None)
    if profile is None:
        profile = _load_profile(db, activity.user_id)

    # Recent training summary relative to this activity's date
    activity_date = activity.start_date.date()
    facts_7d = _query_activity_facts(
        db, activity_date - timedelta(days=7), activity_date
    )
    facts_28d = _query_activity_facts(
        db, activity_date - timedelta(days=28), activity_date
    )
    facts_prev_28d = _query_activity_facts(
        db, activity_date - timedelta(days=56), activity_date - timedelta(days=28)
    )

    return BBaseline(
        profile=ProfileContext(
            goal_type=profile.goal_type if profile else None,
            experience_level=profile.experience_level if profile else None,
            weekly_days_available=profile.weekly_days_available if profile else None,
            injury_notes=profile.injury_notes if profile else None,
            max_hr=profile.max_hr if profile else None,
            max_hr_source=getattr(profile, "max_hr_source", None) if profile else None,
            current_weekly_km=profile.current_weekly_km if profile else None,
        ),
        recent_training_summary=RecentTrainingSummary(
            last_7d=_summarize_period(facts_7d),
            last_28d=_summarize_period(facts_28d),
            previous_28d=_summarize_period(facts_prev_28d),
        ),
        longitudinal=_build_longitudinal_context(db, activity),
        perceived_effort=build_perceived_effort(
            rpe=check_in.rpe if check_in else None,
            effort_axis=metrics.effort if metrics else None,
            effort_score=round(metrics.effort_score, 1) if metrics and metrics.effort_score is not None else None,
            discount_signals=metrics.discount_signals if metrics else None,
            pain_scores=_recent_pain_scores(db, activity),
        ),
        adherence=_build_adherence_context(db, activity),
        believed_facts=build_believed_facts(db, activity),
        calibration=_build_calibration_context(db, activity),
        preference_profile=_build_preference_profile(db, activity),
        narrative=build_narrative_context(db, activity),
        safety_rules=SafetyRules(
            never_diagnose=True,
            pain_severe_threshold=7,
            no_invented_facts=True,
        ),
    )


def _load_profile(db: Session, user_id) -> Optional[UserProfile]:
    """The runner's profile, or None. A relationship-level fact shared across the
    B baseline (profile section) and the focus payload (zone calibration)."""
    return (
        db.query(UserProfile)
        .filter(UserProfile.user_id == user_id)
        .first()
    )


def _summarize_period(facts) -> TrainingPeriodSummary:
    return TrainingPeriodSummary(
        activity_count=len(facts),
        total_distance_m=sum(f.distance_m for f in facts),
        total_moving_time_s=sum(f.moving_time_s for f in facts),
        total_effort=round(sum(f.effort_score or 0 for f in facts), 1),
    )


def _build_longitudinal_context(db: Session, activity: Activity) -> LongitudinalContext:
    """Assemble the M4 longitudinal contrast: a digest of the runner's last 1-2
    reports plus the M2 baseline trend matching this activity's context bucket.

    The prior-report digests are pulled through the retrieval seam (A2b), which
    reads A2a's stored CoachReport.digest artifact (falling back to re-projection
    for pre-A2a rows). Both halves degrade to empty/None (no prior reports, no
    comparable trend) without failing.
    """
    return LongitudinalContext(
        prior_reports=fetch_prior_digests(db, activity),
        baseline_trend=_matching_baseline_trend(db, activity),
    )


def _recent_pain_scores(db: Session, activity: Activity) -> list[int]:
    """Chronological (oldest-first) pain scores for the M6 pain trend, scoped to
    THIS run's pain location so the trend never conflates distinct injuries (a
    knee easing while a shin builds is not one trend). Returns an empty list when
    this run has no pain location to anchor on, so the trend degrades to absent.
    Reads existing rows only."""
    current = getattr(activity, "check_in", None)
    location = (current.pain_location or "").strip() if current else ""
    if not location:
        return []  # no location anchor -> no location-specific trend
    rows = (
        db.query(CheckIn.pain_score, Activity.start_date)
        .join(Activity, CheckIn.activity_id == Activity.id)
        .filter(
            Activity.user_id == activity.user_id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date <= activity.start_date,
            CheckIn.pain_score.isnot(None),
            func.lower(func.trim(CheckIn.pain_location)) == location.lower(),
        )
        # Newest-first so the cap keeps the most recent; id breaks start_date ties.
        .order_by(Activity.start_date.desc(), Activity.id.desc())
        .limit(_PAIN_HISTORY_SCAN_LIMIT)
        .all()
    )
    return [pain for pain, _ in reversed(rows)]  # return oldest-first


def _matching_baseline_trend(
    db: Session, activity: Activity
) -> Optional[BaselineTrendDelta]:
    """The RunnerBaseline trend for this activity's context bucket, if any.

    Returns None when there is no baseline, no metrics, no matching bucket, or
    the matching bucket is still abstaining (too few comparable samples).
    """
    metrics = activity.metrics
    if metrics is None:
        return None

    baseline: Optional[RunnerBaseline] = (
        db.query(RunnerBaseline)
        .filter(RunnerBaseline.user_id == activity.user_id)
        .first()
    )
    if baseline is None or not baseline.bucketed_trends:
        return None

    average_temp = None
    if activity.raw_summary:
        average_temp = activity.raw_summary.get("average_temp")

    key = bucket_key(metrics.effort, metrics.is_hilly, average_temp)
    bucket = baseline.bucketed_trends.get(key)
    if not bucket or bucket.get("abstained"):
        return None

    return BaselineTrendDelta(
        bucket=key,
        sample_count=bucket.get("sample_count", 0),
        efficiency_factor=bucket.get("efficiency_factor"),
        hr_drift=bucket.get("hr_drift"),
    )


def _build_adherence_context(db: Session, activity: Activity) -> AdherenceContext:
    """Assemble the M7 adherence section: did the runner act on the LAST report's
    next_steps?

    Compute-on-demand (no durable store, the M4/M6 pattern): re-derive the labels
    each run from the most recent prior non-fallback report's next_steps and the
    subsequent comparable activities' already-stored DerivedMetrics. The prior
    commitments are pulled through the retrieval seam (A2d), which reads the
    structured next_steps from the stored exchange record instead of this builder
    re-querying and carrying the full report body. Degrades to empty when there is
    no prior report. All judging lives in the pure `adherence` module; this
    function only gathers rows.
    """
    prior = fetch_prior_commitments(db, activity)
    if prior is None or not prior.next_steps:
        return AdherenceContext(prior_report_date=None, outcomes=[])

    candidates = _adherence_candidates(db, activity, prior.source_start_date)
    pushback = _detect_pushback(db, prior.source_activity_id)

    return build_adherence(
        prior_report_date=(
            prior.source_start_date.isoformat() if prior.source_start_date else None
        ),
        prior_next_steps=prior.next_steps,
        candidates=candidates,
        pushback=pushback,
    )


def _adherence_candidates(
    db: Session, activity: Activity, source_start_date
) -> list[CandidateActivity]:
    """Subsequent analysed activities between the source report and this run
    (inclusive of this run), oldest-first, each projected to the facts the
    adherence verdict needs. Unanalysed runs (no DerivedMetric) are excluded so
    they never pollute a window verdict."""
    rows = (
        db.query(Activity)
        # Eager-load metrics: it is read for every row below, so a joinedload
        # avoids an N+1 lazy-select per candidate on this per-report build.
        .options(joinedload(Activity.metrics))
        .filter(
            Activity.user_id == activity.user_id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date > source_start_date,
            Activity.start_date <= activity.start_date,
        )
        .order_by(Activity.start_date.asc(), Activity.id.asc())
        .limit(_ADHERENCE_CANDIDATE_SCAN_LIMIT)
        .all()
    )
    candidates: list[CandidateActivity] = []
    for act in rows:
        metrics = act.metrics
        if metrics is None:
            continue  # unanalysed -> not a fair comparable
        candidates.append(
            CandidateActivity(
                date=act.start_date.isoformat(),
                effort=metrics.effort,
                duration_class=metrics.duration_class,
                structure=metrics.structure,
                is_race=metrics.is_race,
                confidence=metrics.confidence,
                user_intent=act.user_intent,
            )
        )
    return candidates


def _detect_pushback(db: Session, source_activity_id) -> bool:
    """Did the runner explicitly push back on the source report's advice? Scans
    that activity's check-in note and its user chat replies for a dispute
    phrase. Conservative: only an explicit signal flips an implicit label."""
    texts: list[str] = []

    check_in = (
        db.query(CheckIn).filter(CheckIn.activity_id == source_activity_id).first()
    )
    if check_in and check_in.notes:
        texts.append(check_in.notes)

    chat_rows = (
        db.query(CoachChatMessage.content)
        .filter(
            CoachChatMessage.activity_id == source_activity_id,
            CoachChatMessage.role == "user",
        )
        .all()
    )
    texts.extend(content for (content,) in chat_rows if content)

    blob = " ".join(texts).lower()
    return any(phrase in blob for phrase in _PUSHBACK_PHRASES)


def _build_calibration_context(db: Session, activity: Activity) -> CalibrationContext:
    """Assemble the M9 calibration section: individualise this run's HR-drift
    reading against the runner's own typical drift for these conditions, and a
    non-diagnostic referral nudge for any computable red-flag pattern. Both are
    computed at read time (the baseline is recomputed after the pipeline) and
    degrade cleanly. Neither overrides the re-derived DerivedMetric."""
    metrics = activity.metrics
    observed_drift = metrics.hr_drift if metrics else None
    comparable_drifts = _comparable_bucket_drifts(db, activity) if metrics else []
    hr_drift = calibrate_drift(observed_drift, comparable_drifts)

    referral = assess_referral(
        flags=metrics.flags if metrics else [],
        pain_scores=_recent_pain_scores_any(db, activity),
    )
    return CalibrationContext(hr_drift=hr_drift, referral=referral)


def _comparable_bucket_drifts(db: Session, activity: Activity) -> list[float]:
    """HR-drift values from this runner's PRIOR runs in the same context bucket
    (effort | terrain | temp-band), the like-for-like set the personal expected
    drift is computed from. Bounded scan; reads existing rows only."""
    metrics = activity.metrics
    if metrics is None:
        return []
    this_temp = (activity.raw_summary or {}).get("average_temp")
    this_bucket = bucket_key(metrics.effort, metrics.is_hilly, this_temp)

    rows = (
        db.query(Activity)
        .options(joinedload(Activity.metrics))
        .filter(
            Activity.user_id == activity.user_id,
            Activity.id != activity.id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date < activity.start_date,
        )
        # id breaks start_date ties so the scanned set is deterministic at the
        # scan-limit boundary (matches the other prior-activity scans in this file).
        .order_by(Activity.start_date.desc(), Activity.id.desc())
        .limit(_CALIBRATION_SCAN_LIMIT)
        .all()
    )
    drifts: list[float] = []
    for act in rows:
        m = act.metrics
        if m is None or m.hr_drift is None:
            continue
        temp = (act.raw_summary or {}).get("average_temp")
        if bucket_key(m.effort, m.is_hilly, temp) == this_bucket:
            drifts.append(m.hr_drift)
    return drifts


def _recent_pain_scores_any(db: Session, activity: Activity) -> list[int]:
    """Pain scores for this runner across ALL locations within a recent WINDOW
    (the referral persistence check is about sustained pain in general, not one
    injury). The window matters: without it, three unrelated niggles spread over
    a year would read as 'persistent' and fire a false referral. Reads existing
    rows only; bounded scan."""
    window_start = activity.start_date - timedelta(days=_PERSISTENT_PAIN_WINDOW_DAYS)
    rows = (
        db.query(CheckIn.pain_score)
        .join(Activity, CheckIn.activity_id == Activity.id)
        .filter(
            Activity.user_id == activity.user_id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date <= activity.start_date,
            Activity.start_date >= window_start,
            CheckIn.pain_score.isnot(None),
        )
        .order_by(Activity.start_date.desc(), Activity.id.desc())
        .limit(_PAIN_HISTORY_SCAN_LIMIT)
        .all()
    )
    return [p for (p,) in rows]


def _build_preference_profile(db: Session, activity: Activity):
    """Assemble the M10 preference profile from the runner's accumulated
    adherence_pattern beliefs (M8). Reuses the belief retrieval (active,
    non-decayed, quality-cleared), so a stale or thin adherence record yields an
    empty profile and the coach simply has no preference to lean on."""
    beliefs = [
        b for b in retrieve_beliefs(db, activity.user_id)
        if b.kind == "adherence_pattern"
    ]
    return build_preference_profile(beliefs)


def hash_context_pack(pack: Dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of a legacy dict-shaped pack.

    Retained for migration safety (tests pin the typed model's fingerprint against this);
    new code should call CoachContextPack.fingerprint() instead.
    """
    return hashlib.sha256(
        json.dumps(pack, sort_keys=True, default=str).encode()
    ).hexdigest()
