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

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, undefer

from app.core.config import settings
from app.models import Activity, Block, DerivedMetric, RunnerBaseline, UserProfile
from app.models.checkin import CheckIn
from app.models.coach_chat_message import CoachChatMessage
from app.services.analysis.baseline import bucket_key
from app.services.analysis.classifier import Classification, compose_headline  # noqa: F401
from app.services.analysis.novelty import AxisSnapshot, compute_novelty
from app.services.coach.adherence import CandidateActivity, build_adherence
from app.services.coach.calibration import assess_referral, calibrate_drift
from app.services.coach.memory_store import get_memory
from app.services.coach.perceived_effort import build_perceived_effort
from app.services.coach.salience import compute_safety_override
from app.services.coach.corpus import DEFAULT_SCHOOL_ID
from app.services.coach.prompts import (
    _describe_dial,
    is_corpus_prompt,
    is_body_prompt,
    is_intensity_mix_prompt,
    is_intensity_read_prompt,
    is_stance_prompt,
    is_stream_view_prompt,
    is_user_materials_prompt,
)
from app.services.coach.read_time_signals import (
    SignalCompute,
    build_signals,
    gather,
    will_run,
)
from app.services.coach.stance import StanceProfile, resolve_stance
from app.services.coach.volume import build_training_volume
from app.services.coach.recent_training import build_recent_training
from app.services.coach.recent_weeks import CheckInFacts, build_recent_weeks
from app.services.weeks import resolve_week_start
from app.services.coach.training_history import build_training_history
from app.services.coach.intensity import build_intensity
from app.services.coach.intensity_read import build_intensity_mix, build_intensity_read
from app.services.readiness import build_readiness
from app.services.coach.retrieval import (
    fetch_corpus,
    fetch_prior_commitments,
    fetch_prior_digests,
    fetch_stream_view,
)
from app.schemas.coach_context import (
    ActivityContext,
    AdherenceContext,
    BaselineTrendDelta,
    BlockContext,
    BlockMember,
    CalibrationContext,
    CheckInContext,
    CoachContextPack,
    ContinuityContext,
    CorpusContext,
    CorpusSchoolContext,
    StanceContext,
    StanceEmphasisAxis,
    TrainingLoadContext,
    TrainingVolumeContext,
    RecentTrainingContext,
    ReadinessContext,
    RecentWeeksContext,
    TrainingHistoryContext,
    IntensityContext,
    IntensitySession,
    LongitudinalContext,
    MemoryContext,
    MetricsContext,
    NoveltyContext,
    PerceivedEffortContext,
    BodyContext,
    ProfileContext,
    SafetyOverride,
    SafetyRules,
    SalienceContext,
)
from app.schemas.coach_memory import RunnerMemoryProfile
from app.services.activity_facts import query_facts as _query_activity_facts
from app.services.activity_facts import scan as _scan
from app.services.activity_facts import scan_cache
from app.services.units.cadence import normalize_cadence_spm

logger = logging.getLogger(__name__)

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

# Bound on prior analysed activities scanned for the A4 novelty signal. Unlike the
# recency-scoped scans above, novelty needs EXISTENCE across the runner's WHOLE
# history (is this the first interval session ever?), so this caps high; a
# recreational runner stays well under it. Exceeding it could in theory
# false-positive a "first" on an axis last seen beyond the cap — acceptable, and
# it mirrors the bounded-scan convention the other reads follow.
_NOVELTY_HISTORY_SCAN_LIMIT = 1000

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
    profile and recent-load rollups, the last exchange's digest + matching
    baseline trend, this run's subjective signals, the deterministic durable
    facts (adherence, calibration), and the safety rules. (The belief /
    preference / narrative durable-memory facts were retired in M4, ADR 0025.)"""

    profile: ProfileContext
    longitudinal: LongitudinalContext
    perceived_effort: PerceivedEffortContext
    adherence: AdherenceContext
    calibration: CalibrationContext
    salience: SalienceContext
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


def zones_calibration(profile) -> tuple:
    """Zone calibration, resolved from the runner's PROFILE (never a stored
    pack): calibrated when the runner has Strava-sourced HR zones (the actual
    time-in-zone binning basis, #297/#458) OR explicitly set max_hr with a known
    source. Strava zones take precedence as the basis and are never discounted;
    the analysis pipeline already treats them as calibrated for interval KPIs.

    Shared by the pack build and the conversational policy floor (#767, ADR
    0028: zone calibration was never activity-scoped — it only lived on the pack
    because that was the nearest copy). Returns (zones_calibrated, zones_basis).
    """
    has_strava_zones = bool(profile and getattr(profile, "hr_zones", None))
    has_explicit_max_hr = bool(
        profile
        and profile.max_hr
        and profile.max_hr > 100
        and getattr(profile, "max_hr_source", None)  # must have a source
    )
    zones_calibrated = has_strava_zones or has_explicit_max_hr
    if has_strava_zones:
        zones_basis = "strava_zones"
    elif has_explicit_max_hr:
        zones_basis = f"user_{profile.max_hr_source}"
    else:
        zones_basis = "uncalibrated"
    return zones_calibrated, zones_basis


def build_context_pack(
    db: Session,
    activity: Activity,
    *,
    continuity: Optional[ContinuityContext] = None,
    prompt_id: Optional[str] = None,
    stance: Optional[StanceProfile] = None,
) -> CoachContextPack:
    """Assemble all facts the LLM needs. No computation, just data gathering.

    Composes the working context (B baseline + the subject's focus payload) into
    the flat CoachContextPack. `continuity` carries the A4 fuller-turn continuity
    (the opener prose this exchange already sent + any chat reply); the standard
    and opener paths leave it empty. The pack also carries the A4 `salience`
    section (novelty + safety override), computed in the B baseline. `prompt_id`
    gates the P1.2 `corpus` and P1.3 `stance` sections: each is emitted ONLY under
    a corpus-/stance-aware prompt id, so the pack stays byte-stable for every other
    prompt and for callers that pass no prompt_id (the default). `stance` (P1.3,
    ADR 0015) is the runner's resolved StanceProfile: its `school_id` keys the
    `corpus` section (replacing the hardcoded default) and its `emphasis` fills the
    `stance` section. When omitted, the corpus falls to the hardcoded default school
    and the stance section uses the balanced default — keeping callers that pass no
    stance byte-stable against the P1.2-wired behaviour.
    """
    # #443: under a stream-view-aware prompt the DEFAULT pack carries the consolidated
    # stream view (deep=True pulls it through the retrieval seam). Under any other
    # prompt deep=False, so the deferred column is never loaded and the pack stays
    # byte-stable (stream_view stays None and is dropped from serialization).
    with scan_cache(db):
        _prewarm_fact_scans(db, activity, prompt_id)
        return _assemble_pack(db, activity, continuity, prompt_id, stance)


def _assemble_pack(db, activity, continuity, prompt_id, stance) -> CoachContextPack:
    """The pack assembly itself, unchanged, split out only so `build_context_pack`
    can hold the fact-scan cache open across every signal it gathers (#804)."""
    wc = assemble_working_context(db, activity, deep=is_stream_view_prompt(prompt_id))
    b, f = wc.b_baseline, wc.focus
    # The read anchor shared across the read-time signal seam (#492). Calibration and
    # adherence (always-emitted, in the B baseline above) ignore it; the gated
    # training-load/volume/recent-training signals key on it (each derives the exact
    # flavour it needs — start_date for readiness, the local calendar day for volume).
    as_of = activity.start_date
    # #578 combined intensity section (distribution + trend + this-run), gated to
    # INTENSITY prompts. Under an intensity-read prompt (Slice 3) this abstains (None) and
    # the merged reads below take over instead.
    intensity = gather(_INTENSITY_SIGNAL, db, activity, prompt_id, as_of)
    # ADR 0026 Slice 3 (#673): under an intensity-read prompt the four this-run intensity
    # lenses collapse into `intensity_read` (+ the promoted safety `referral`) and the
    # recent distribution moves to `intensity_mix`; the standalone perceived_effort /
    # calibration / intensity sections retire (None, dropped from serialization). Under
    # every prior prompt this block is inert and those three sections emit byte-identically.
    perceived_effort = b.perceived_effort
    calibration = b.calibration
    intensity_read = None
    intensity_mix = None
    referral = None
    if is_intensity_read_prompt(prompt_id):
        # The intensity scan feeds BOTH new reads (this run's band/split + the recent
        # distribution). Computed directly since the INTENSITY gate does not carry this
        # prompt; the merge reshapes it, never re-scans.
        ictx = _build_intensity_context(db, activity, as_of)
        ds = f.metrics.discount_signals if f.metrics else None
        confounders = list(ds.get("likely_inflated_by") or []) if isinstance(ds, dict) else []
        intensity_read = build_intensity_read(
            this_session=ictx.this_session if ictx is not None else IntensitySession(),
            distribution_adjusted=ictx.distribution_adjusted if ictx is not None else None,
            perceived_effort=b.perceived_effort,
            calibration_hr_drift=b.calibration.hr_drift if b.calibration else None,
            confounders=confounders,
        )
        if is_intensity_mix_prompt(prompt_id):
            intensity_mix = build_intensity_mix(ictx)
        ref = b.calibration.referral if b.calibration else None
        referral = ref.get("nudge") if isinstance(ref, dict) else None
        perceived_effort = None
        calibration = None
    # #742: the runner's stated build reaches the coach ONLY under a body-aware prompt.
    # The builder populates it unconditionally (it is a plain profile-row read, no
    # scan), so the gate is one reassignment here and the nested drop in
    # to_serializable_dict keeps every prior prompt's profile section byte-identical.
    profile_ctx = b.profile
    if profile_ctx.body is not None and not is_body_prompt(prompt_id):
        profile_ctx = profile_ctx.model_copy(update={"body": None})
    # ADR 0026: build the grouped pack from flat section objects. from_sections wraps
    # each section into its coaching-question group (this_run/right_now/…); the flat
    # kwargs below are unchanged, so the gathering logic is untouched.
    return CoachContextPack.from_sections(
        activity=f.activity,
        metrics=f.metrics,
        check_in=f.check_in,
        profile=profile_ctx,
        longitudinal=b.longitudinal,
        perceived_effort=perceived_effort,
        adherence=b.adherence,
        calibration=calibration,
        intensity_read=intensity_read,
        referral=referral,
        intensity_mix=intensity_mix,
        salience=b.salience,
        # #522: COACH_CONTINUITY_ENABLED drops the fuller-turn continuity section.
        continuity=(
            (continuity or ContinuityContext())
            if settings.COACH_CONTINUITY_ENABLED
            else None
        ),
        block=_build_block_context(db, activity),
        # The runner's selected school takes effect ONLY under a stance-aware prompt
        # (v5). Under v4 the corpus keeps the hardcoded default school (P1.2 stays
        # byte-stable, AC1); P1.3's stance — school AND emphasis — activates together
        # with v5. A non-stance/non-corpus prompt drops the corpus section entirely.
        corpus=_build_corpus_context(
            db,
            activity,
            prompt_id,
            school_id=stance.school_id if (stance and is_stance_prompt(prompt_id)) else None,
        ),
        stance=_build_stance_context(prompt_id, stance),
        # The three prompt-gated read-time signals go through the shared seam (#492):
        # `gather` applies each signal's PromptFeature gate ONCE, returning None
        # (dropped from serialization, byte-stable) under a prompt that lacks it. The
        # bounded scan + abstention live inside each adapter's compute.
        #   - P3 readiness (ADR 0016): as-of this activity, so the run is in the acute load.
        #   - #400 volume-vs-norm: over the runner's local-day windows.
        #   - #444 modality-aware recent-training picture.
        training_load=gather(_TRAINING_LOAD_SIGNAL, db, activity, prompt_id, as_of),
        training_volume=gather(_TRAINING_VOLUME_SIGNAL, db, activity, prompt_id, as_of),
        # #443 consolidated stream view: present in the DEFAULT pack only under a
        # stream-view-aware prompt (focus.stream_view is None otherwise, dropped from
        # serialization), so the pack stays byte-stable under v9 and below. (Not a
        # history-scan signal — it rides the focus payload via the deep flag above.)
        stream_view=f.stream_view,
        recent_training=gather(_RECENT_TRAINING_SIGNAL, db, activity, prompt_id, as_of),
        # ADR 0026 Slice 2 (#670): the redefined right_now content — readiness (renamed
        # training_load) and the merged day-resolved recent_weeks. Gated to the new
        # grouped prompt, so under every prior prompt these are None (dropped) and the old
        # three sections above still emit, byte-stable.
        readiness=gather(_READINESS_SIGNAL, db, activity, prompt_id, as_of),
        recent_weeks=gather(_RECENT_WEEKS_SIGNAL, db, activity, prompt_id, as_of),
        schedule=gather(_SCHEDULE_SIGNAL, db, activity, prompt_id, as_of),
        # #561 multi-year training-history picture (LOD volume ladder + durability traits).
        # ADR 0026 Slice 2 (#670): EITHER the original 60d ladder (every prior prompt) OR
        # the rebased-after-recent_weeks 14d ladder (grouped_v2), never both — the two
        # signals are mutually exclusive by prompt feature, so one abstains (None) and the
        # `or` selects the other into the ONE `training_history` pack key.
        training_history=(
            gather(_TRAINING_HISTORY_SIGNAL, db, activity, prompt_id, as_of)
            or gather(_TRAINING_HISTORY_2WK_SIGNAL, db, activity, prompt_id, as_of)
        ),
        # ADR 0025 runner memory profile (stored-artifact read, memory-aware prompt only).
        memory=gather(_MEMORY_SIGNAL, db, activity, prompt_id, as_of),
        # #578 intensity distribution + trend (read-time scan, intensity-aware prompt only);
        # None under an intensity-read prompt (Slice 3 splits it into intensity_read +
        # intensity_mix, computed above).
        intensity=intensity,
        safety_rules=b.safety_rules,
    )


def _build_corpus_context(
    db: Session,
    activity: Activity,
    prompt_id: Optional[str],
    *,
    school_id: Optional[str] = None,
) -> Optional[CorpusContext]:
    """The P1.2 coaching-corpus pack section (ADR 0014), extended with P4 user
    materials (#286, ADR 0017), or None.

    Emitted ONLY under a corpus-aware prompt id (`is_corpus_prompt`), so the pack is
    byte-stable for every other prompt (AC1) — the section is dropped from
    serialization when None, exactly like the `block` section. P1.3 threads the
    runner's selected `school_id` here (via `build_context_pack`'s `stance` param);
    when none is passed it falls back to the hardcoded `DEFAULT_SCHOOL_ID`, so a
    caller that passes no stance stays byte-stable against P1.2. Reads the corpus
    through the `fetch_corpus` seam, which degrades to house-core-only when no school
    resolves (AC5).

    The runner's distilled `user_materials` are read AND attached ONLY under a
    user-materials-aware prompt (`is_user_materials_prompt`, v7) — under v4/v5/v6 the
    materials read is not even performed and `user_materials` stays None, so the
    corpus section is byte-identical to its P1.2/P1.3 shape (the activation boundary).
    Materials are the runner's chosen reference; they reweight emphasis/framing only
    and never touch the run's DerivedMetric (validator rule 8, prompt rule 28). The
    corpus is judgment knowledge, never a fact.
    """
    if not is_corpus_prompt(prompt_id):
        return None
    # #522: COACH_USER_MATERIALS_ENABLED off => skip the materials read entirely
    # (db=None keeps the seam a pure school lookup), so user_materials stays None and
    # is dropped from the corpus section. HOUSE_CORE (house_principles) is retained.
    materials_aware = is_user_materials_prompt(prompt_id) and settings.COACH_USER_MATERIALS_ENABLED
    # Read materials ONLY under a user-materials-aware prompt — a null db/user keeps
    # the seam a pure school lookup (and byte-stable) under v4/v5/v6.
    resolved = fetch_corpus(
        db if materials_aware else None,
        activity.user_id if materials_aware else None,
        school_id or DEFAULT_SCHOOL_ID,
    )
    # #522: COACH_HOUSE_SCHOOLS_ENABLED off => drop the named school; HOUSE_CORE stays.
    school = resolved.school if settings.COACH_HOUSE_SCHOOLS_ENABLED else None
    return CorpusContext(
        house_principles=list(resolved.house_core.principles),
        school=(
            CorpusSchoolContext(
                id=school.id,
                name=school.name,
                stance=school.stance,
                principles=list(school.principles),
                method_framing=school.method_framing,
                emphasis_hints=list(school.emphasis_hints),
            )
            if school is not None
            else None
        ),
        # None under v4/v5/v6 (dropped from serialization → byte-stable); a list
        # (possibly empty) under v7, where the materials are load-bearing.
        user_materials=list(resolved.user_materials) if materials_aware else None,
    )


def _build_stance_context(
    prompt_id: Optional[str], stance: Optional[StanceProfile]
) -> Optional[StanceContext]:
    """The P1.3 coaching-stance pack section — the runner's two emphasis axes
    (ADR 0015), or None.

    Emitted ONLY under a stance-aware prompt id (`is_stance_prompt`), so the pack is
    byte-stable for every other prompt (AC1) — the section is dropped from
    serialization when None, exactly like `corpus`/`block`. Carries ONLY the
    emphasis tilt (the selected school rides the `corpus` section); the values are
    runner PREFERENCE, never evidence and never grounding — they never touch the
    run's DerivedMetric. A caller that passes no stance under a stance prompt gets
    the balanced default (resolve_stance(None)).
    """
    if not is_stance_prompt(prompt_id):
        return None
    resolved = stance or resolve_stance(None)
    return StanceContext(
        emphasis=[
            StanceEmphasisAxis(
                key=axis.key,
                low_pole=axis.low_pole,
                high_pole=axis.high_pole,
                value=value,
                descriptor=_describe_dial(value, axis.low_pole, axis.high_pole),
            )
            for axis, value in resolved.emphasis.as_ordered()
        ]
    )


_ReadinessSectionT = TypeVar("_ReadinessSectionT", TrainingLoadContext, ReadinessContext)


def _build_readiness_section(
    db: Session, activity: Activity, section_cls: type[_ReadinessSectionT]
) -> Optional[_ReadinessSectionT]:
    """The runner's current-condition readiness read, projected into `section_cls`.

    ONE read behind the two keyed twins: `training_load` (P3/ADR 0016) and its ADR 0026
    Slice 2 rename `right_now.readiness` carry byte-for-byte the same content under
    different pack keys, so the read and the projection live here once and the two
    builders below differ only in which model they fill (#704).

    Computed at read time (mirroring M9 calibration) as of this activity's LOCAL day
    (#507, the `local_start` convention the volume signal uses), so this run is in the
    acute load, the daily series agrees with the volume signal on the day boundary, and
    a regen of an old activity reproduces the condition as of then. Degrades to None when
    no history is available (build_readiness guards)."""
    # #507: anchor on the runner's LOCAL day so the daily-load series buckets by local
    # calendar day, agreeing with the volume signal on the day boundary (a late-evening
    # run that rolled to the next UTC day stays on its local day across both signals).
    model = build_readiness(db, activity.user_id, activity.local_start)
    if model is None:
        return None
    return section_cls(
        fitness=model.fitness,
        fatigue=model.fatigue,
        form=model.form,
        ramp_rate=model.ramp_rate,
        condition=model.condition,
        trend=model.trend,
        ramp_aggressive=model.ramp_aggressive,
        warming_up=model.warming_up,
        sample_count=model.sample_count,
    )


def _build_training_load_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[TrainingLoadContext]:
    """The P3 training-load readiness pack section (ADR 0016), or None.

    A read-time history-scan signal behind the shared `ReadTimeSignal` seam (#492):
    the `TRAINING_LOAD` gating is applied once in `gather`, so this `compute` is
    reached ONLY under a training-load-aware prompt — the section is dropped from
    serialization when None, exactly like `corpus`/`stance`/`block`. A tier-3
    deterministic FACT the coach may cite, but it never overrides the run's re-derived
    DerivedMetric or the safety floor (prompt rule 27). The read itself is
    `_build_readiness_section`, shared with the `readiness` twin."""
    return _build_readiness_section(db, activity, TrainingLoadContext)


def _build_training_volume_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[TrainingVolumeContext]:
    """The #400 frequency-/volume-vs-norm pack section, or None.

    A read-time history-scan signal behind the shared `ReadTimeSignal` seam (#492):
    the `VOLUME` gating is applied once in `gather`, so this `compute` is reached ONLY
    under a volume-aware prompt — the section is dropped from serialization when None,
    exactly like `training_load`/`corpus`/`block`. Computed read-time as of this
    activity's LOCAL day (so the windows match the runner's calendar, #399) over a
    91-day fact span (the trailing 7d plus the 12-week norm baseline before it). A
    deterministic FACT the coach may cite, but it never overrides the run's re-derived
    DerivedMetric or the safety floor (the volume addendum). Degrades gracefully:
    thin history yields `has_baseline=False` and `no_norm` directions."""
    local_day = activity.local_start.date()
    # The scan spans the trailing 7d plus the 12-week norm baseline before it; its
    # width and the half-open UTC bound live once in `_FACT_SCANS` (#804).
    facts = _scan_facts(db, activity, "training_volume")
    week_starts_on = resolve_week_start(_load_profile(db, activity.user_id))
    return build_training_volume(facts, local_day, week_starts_on)


def _build_recent_training_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[RecentTrainingContext]:
    """The #444 modality-aware recent-training section, or None.

    A read-time history-scan signal behind the shared `ReadTimeSignal` seam (#492):
    the `RECENT_TRAINING` gating is applied once in `gather`, so this `compute` is
    reached ONLY under a recent-training-aware prompt — the pack stays byte-stable
    otherwise (the Optional-and-drop idiom). Computed read-time as of this activity's
    LOCAL day over a ~210-day fact span (the 30d window plus its ~6-month vs-typical
    baseline), reusing the Trends per-day-rate core so "typical" has one meaning across
    the product. A deterministic FACT the coach may cite, but it never overrides the
    run's re-derived DerivedMetric or the safety floor (the recent-training addendum).
    Degrades gracefully: thin history yields `has_baseline=False` and `no_norm`
    directions."""
    local_day = activity.local_start.date()
    facts = _scan_facts(db, activity, "recent_training")
    return build_recent_training(
        facts, local_day, include_previous_30d=settings.COACH_PREVIOUS_30D_ENABLED
    )


def _build_readiness_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[ReadinessContext]:
    """ADR 0026 Slice 2 (#670): the `right_now.readiness` pack section, or None.

    The renamed successor to `training_load` (identical content, coaching-question key),
    behind the shared `ReadTimeSignal` seam gated on `READINESS` — reached ONLY under a
    readiness-aware prompt (the new grouped prompt), so it is dropped from serialization
    (byte-stable) under every prior prompt that still reads `training_load`. Literally the
    same read as `_build_training_load_context` — both delegate to
    `_build_readiness_section` and differ only in the model they fill (#704) — so the two
    keys can never drift apart while both are live. A deterministic FACT the coach may
    cite; never overrides the run's re-derived DerivedMetric or the safety floor."""
    return _build_readiness_section(db, activity, ReadinessContext)


def _query_recent_checkins(db: Session, activity_ids: list) -> dict:
    """Narrow supplementary read: the per-activity check-in facts (rpe/pain/notes) for
    the recent_weeks per-session read, keyed by activity_id. Sourced separately from
    `_query_activity_facts` (a CheckIn LEFT join risks double-counting an activity's
    volume if two check-in rows ever exist) — mirroring `_query_confounded_activity_ids`.
    write_checkin upserts one row per activity, so `.first()`-style collapse is safe; the
    dict keeps the last row per id defensively."""
    if not activity_ids:
        return {}
    rows = db.execute(
        select(
            CheckIn.activity_id, CheckIn.rpe, CheckIn.pain_score, CheckIn.notes
        ).where(CheckIn.activity_id.in_(activity_ids))
    ).all()
    return {
        aid: CheckInFacts(rpe=rpe, pain_score=pain, notes=notes)
        for aid, rpe, pain, notes in rows
    }


def _build_recent_weeks_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[RecentWeeksContext]:
    """ADR 0026 Slice 2 (#670): the merged day-resolved `right_now.recent_weeks` section,
    or None.

    The single recent-training read that replaces `training_volume` + `recent_training`,
    behind the shared `ReadTimeSignal` seam gated on `RECENT_WEEKS` — reached ONLY under a
    recent-weeks-aware prompt (the new grouped prompt), so it is dropped from
    serialization (byte-stable) under every prior prompt that still reads the old two
    sections. Computed read-time as of this activity's LOCAL day over a 91-day fact span
    (the two weeks of structure plus the ~12-week norm baseline), reusing the volume core
    so "typical" has one meaning across the product. A deterministic FACT the coach may
    cite; never overrides the run's re-derived DerivedMetric or the safety floor."""
    local_day = activity.local_start.date()
    facts = _scan_facts(db, activity, "recent_weeks")
    # Per-activity check-ins only for the two weeks the day-resolved log covers (rolling
    # 7d and this/last calendar week all fall within the trailing ~14 days).
    recent_ids = [
        f.activity_id
        for f in facts
        if f.local_date >= local_day - timedelta(days=14)
    ]
    checkins = _query_recent_checkins(db, recent_ids)
    week_starts_on = resolve_week_start(_load_profile(db, activity.user_id))
    return build_recent_weeks(facts, checkins, local_day, week_starts_on)


# The wide fetch span for the multi-year history scan: ~10 years, so the deep ladder
# buckets and the durability traits see the runner's full available history. The
# fetch is summary-only (no streams), so even a deep history is cheap; the builder
# self-limits to whatever is present.
_TRAINING_HISTORY_FETCH_DAYS = 3660


def _build_training_history_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[TrainingHistoryContext]:
    """The #561 multi-year training-history pack section, or None.

    A read-time history-scan signal behind the shared `ReadTimeSignal` seam (#492):
    the `TRAINING_HISTORY` gating is applied once in `gather`, so this `compute` is
    reached ONLY under a training-history-aware prompt — the pack stays byte-stable
    otherwise (the Optional-and-drop idiom). Computed read-time as of this activity's
    LOCAL day over the runner's full available history (a ~10-year summary-only fetch).
    A deterministic FACT the coach may cite, but it never overrides the run's
    re-derived DerivedMetric or the safety floor (the training-history addendum).
    Degrades gracefully: the builder returns None when there is too little history
    beyond the recent window to describe, and abstains per-trait when a comparison
    cannot resolve.

    `COACH_TRAINING_HISTORY_ENABLED` (#561) is an independent operator off-switch:
    when False the section is dropped without a full prompt rollback."""
    return _training_history_section(db, activity, recent_weeks_bound=False)


def _build_training_history_2wk_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[TrainingHistoryContext]:
    """ADR 0026 Slice 2 (#670): the REBASED `training_history` pack section, or None.

    Identical to `_build_training_history_context` (same wide fetch, same abstention, same
    `COACH_TRAINING_HISTORY_ENABLED` off-switch) except the ladder begins after the 2-week
    `recent_weeks` window instead of the retired 60d `recent_training` window, and each
    bucket is enriched with a modality-agnostic weekly LOAD rate, a per-type split, and
    calendar bounds. Gated on `TRAINING_HISTORY_2WK` via the seam, carried ONLY by the
    grouped_v2 prompt (INSTEAD of `TRAINING_HISTORY`), so exactly one training-history
    signal ever fires and every prior prompt keeps the 60d ladder byte-identically."""
    return _training_history_section(db, activity, recent_weeks_bound=True)


def _training_history_section(
    db: Session, activity: Activity, *, recent_weeks_bound: bool
) -> Optional[TrainingHistoryContext]:
    """Shared body for the two training-history signals: the wide summary-only history
    fetch, dispatching to the original or the rebased/enriched ladder.

    #800: `COACH_TRAINING_HISTORY_ENABLED` is no longer re-checked here. It is declared
    on the `training_history` signal as a drop-effect kill switch and applied by the
    shared seam (`read_time_signals.gather`) before either adapter runs, which is the
    only route into this function."""
    local_day = activity.local_start.date()
    facts = _scan_facts(db, activity, "training_history")
    return build_training_history(facts, local_day, recent_weeks_bound=recent_weeks_bound)


# The fetch span for the intensity scan: the 28-day recent window plus its equal prior
# window (for the trend), with a few days of slack. Summary-only (no streams).
_INTENSITY_FETCH_DAYS = 60


def _query_confounded_activity_ids(
    db: Session, start_date: date, end_date: date, user_id
) -> set:
    """The set of activity ids in [start_date, end_date) whose stored `discount_signals`
    fired a REAL HR-inflation confounder (a non-empty `likely_inflated_by`: heat, terrain,
    or stimulant). Sourced separately from `_query_activity_facts` so the shared trends
    projection (#367, which deliberately excludes the discount_signals JSON for chart
    performance) stays untouched; this narrow id+JSON read runs only under the gated
    intensity prompt. The temperature-unknown caution case (empty inflators) is NOT
    counted — only an explicit confounder is exculpatory."""
    stmt = (
        select(DerivedMetric.activity_id, DerivedMetric.discount_signals)
        .join(Activity, DerivedMetric.activity_id == Activity.id)
        .where(Activity.is_deleted == False)  # noqa: E712
        .where(DerivedMetric.discount_signals.isnot(None))
        .where(Activity.start_date >= datetime.combine(start_date, datetime.min.time()))
        .where(Activity.start_date < datetime.combine(end_date, datetime.min.time()))
    )
    if user_id is not None:
        stmt = stmt.where(Activity.user_id == user_id)
    out = set()
    for activity_id, signals in db.execute(stmt).all():
        if isinstance(signals, dict) and signals.get("likely_inflated_by"):
            out.add(activity_id)
    return out


def _build_intensity_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[IntensityContext]:
    """The #578 intensity-distribution-and-trend pack section, or None.

    A read-time history-scan signal behind the shared `ReadTimeSignal` seam (#492):
    the `INTENSITY` gating is applied once in `gather`, so this `compute` is reached
    ONLY under an intensity-aware prompt — the pack stays byte-stable otherwise (the
    Optional-and-drop idiom). Computed read-time as of this activity's LOCAL day over a
    ~60-day fact span (the 28-day recent window plus its equal prior window). Reuses the
    shared `_query_activity_facts` projection (which already carries `effort` and
    `time_in_zones`) for the bulk, plus a narrow `_query_confounded_activity_ids` read
    for the exculpatory discount signals — keeping the trends projection untouched. A
    deterministic FACT the coach may cite, never overriding the run's re-derived
    DerivedMetric or the safety floor (the intensity addendum). Degrades gracefully:
    thin history yields `has_distribution=False` and `no_norm` directions, and the
    builder returns None (section dropped) only when there is nothing to say."""
    local_day = activity.local_start.date()
    start = local_day - timedelta(days=_INTENSITY_FETCH_DAYS)
    end = local_day + timedelta(days=1)
    facts = _scan_facts(db, activity, "intensity")
    confounded_ids = _query_confounded_activity_ids(db, start, end, activity.user_id)
    return build_intensity(facts, confounded_ids, activity.id, local_day)


def _build_memory_context(
    db: Session, activity: Activity, as_of: datetime
) -> Optional[MemoryContext]:
    """The ADR 0025 runner memory profile pack section, or None.

    A stored-artifact read behind the shared `ReadTimeSignal` seam (#492): the
    MEMORY gating is applied once in `gather`, so this `compute` is reached ONLY
    under a memory-aware prompt — the pack stays byte-stable otherwise (the
    Optional-and-drop idiom). Reads the runner's single stored profile row and
    surfaces it WHOLE (no retrieval/ranking) — the citable stated tier that yields
    to this run's re-derived `DerivedMetric` and never lowers the safety floor (the
    memory addendum); it carries no behavioral verdict.

    Degrades to None (section dropped, byte-stable) when no profile row exists yet
    (cold start) or the profile has no lines in any section. A stored row that fails to
    parse drops too, never breaking the pack.

    #800: the `COACH_MEMORY_ENABLED` operator switch is no longer re-checked here. It
    is declared on the `memory` signal as a drop-effect kill switch and applied by the
    shared seam (`read_time_signals.gather`) before this compute runs, which is the
    only route into this function. (Its OTHER half — disabling the runner-memory update
    WRITER — stays in service.py, which is a different concern from the pack.)"""
    row = get_memory(db, activity.user_id)
    if row is None or not row.profile:
        return None
    try:
        profile = RunnerMemoryProfile.model_validate(row.profile)
    except Exception:  # noqa: BLE001 — an off-shape stored profile drops, never crashes the pack
        logger.exception(
            "memory pack: stored profile failed to parse for user %s", activity.user_id
        )
        return None

    sections = (
        profile.who_you_are,
        profile.limits_and_constraints,
        profile.goals_and_plans,
        profile.what_works_for_you,
        profile.lately,
    )
    if not any(sections):
        return None  # a row exists but graduated nothing yet — drop, byte-stable

    last_updated_days_ago: Optional[int] = None
    grounded = _to_aware(row.grounded_through)
    ref = _to_aware(activity.start_date)
    if grounded is not None and ref is not None:
        last_updated_days_ago = max(0, (ref - grounded).days)

    return MemoryContext(
        who_you_are=profile.who_you_are,
        limits_and_constraints=profile.limits_and_constraints,
        goals_and_plans=profile.goals_and_plans,
        what_works_for_you=profile.what_works_for_you,
        lately=profile.lately,
        last_updated_days_ago=last_updated_days_ago,
        source_report_count=row.source_report_count,
    )


def _to_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a naive datetime (SQLite drops tzinfo) to UTC-aware so recency
    arithmetic never mixes naive and aware. No-op on Postgres."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_block_context(db: Session, activity: Activity) -> Optional[BlockContext]:
    """The A1 multi-member block aggregate, or None for a block-of-one (AC8:
    the solo-run pack must stay byte-stable, so no section is emitted at all)."""
    if activity.block_id is None:
        return None
    members = (
        db.query(Activity)
        .filter(Activity.block_id == activity.block_id)
        .order_by(Activity.start_date)
        .all()
    )
    if len(members) < 2:
        return None
    block = db.query(Block).filter(Block.id == activity.block_id).first()
    primary_id = block.primary_activity_id if block else activity.id
    return BlockContext(
        members=[
            BlockMember(
                type=m.type,
                duration_s=m.elapsed_time_s or 0,
                distance_m=m.distance_m or 0,
                is_primary=m.id == primary_id,
            )
            for m in members
        ],
        combined_duration_s=sum(m.elapsed_time_s or 0 for m in members),
        combined_distance_m=sum(m.distance_m or 0 for m in members),
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


# Deadband for the coarse efficiency-shape descriptor (#441): the run's first
# third vs last third must differ by more than this to read as improving/declining
# rather than stable, so ordinary minute-to-minute wobble is not narrated as a trend.
_EFFICIENCY_TREND_DEADBAND_PCT = 5.0


def _efficiency_trend(curve) -> Optional[str]:
    """A coarse shape descriptor for the efficiency curve: did efficiency hold,
    rise, or fade across the run (first third vs last third)?

    Returns "improving" / "declining" / "stable", or None when the curve is too
    short or carries no usable values. Stopped/invalid samples (stored as 0.0 in the
    chart curve) are ignored WITHIN each third so a mid-run stop does not masquerade
    as a fade; the thirds stay positional in time so the descriptor reads the run's
    actual arc. This is the lean coaching signal #441 keeps in place of the raw
    chart-resolution series.
    """
    if not curve or not isinstance(curve, list):
        return None
    n = len(curve)
    third = n // 3
    if third == 0:
        return None  # fewer than 3 points: no shape to read

    def _mean_nonzero(segment) -> Optional[float]:
        nums = [v for v in segment if isinstance(v, (int, float)) and v > 0]
        return sum(nums) / len(nums) if nums else None

    first = _mean_nonzero(curve[:third])
    last = _mean_nonzero(curve[-third:])
    if first is None or last is None or first <= 0:
        return None

    pct = (last - first) / first * 100.0
    if pct > _EFFICIENCY_TREND_DEADBAND_PCT:
        return "improving"
    if pct < -_EFFICIENCY_TREND_DEADBAND_PCT:
        return "declining"
    return "stable"


def _summarize_efficiency_for_coach(efficiency: Optional[dict]) -> Optional[dict]:
    """Project the stored efficiency analysis to the decision-relevant summary the
    coach pack carries (#441).

    The stored `efficiency_analysis` includes a full chart-resolution `curve` (a long
    per-sample series built for the activity-detail chart). The coach cannot reason
    over a raw numeric series dumped as text, and it costs tokens, so this drops the
    `curve` and keeps the summary scalars (average, best_sustained, unit) plus a
    coarse `trend` shape descriptor derived from the curve. The STORED metric and the
    activity-detail efficiency chart read `efficiency_analysis` directly and are
    untouched — only this projection into the coach pack changes.

    Pass-through for None / an empty dict (no analysis to project)."""
    if not efficiency:
        return efficiency
    summary = {k: v for k, v in efficiency.items() if k != "curve"}
    trend = _efficiency_trend(efficiency.get("curve"))
    if trend is not None:
        summary["trend"] = trend
    return summary


def _strip_stops_for_coach(stops_analysis: Optional[dict]) -> Optional[dict]:
    """Project the stored stops analysis to what the coach pack carries.

    Each stored stop carries a raw `[lat, lng]` `location` (consumed by the
    activity-detail StopsPanel). The coach cannot reason over raw coordinates and
    it costs tokens, so this drops `location` from each stop while keeping the
    decision-relevant fields (timing, duration, distance) and the summary scalars.
    The STORED metric and the detail view read `stops_analysis` directly and are
    untouched — only this projection into the coach pack changes.

    Pass-through for None / an empty dict (no analysis to project)."""
    if not stops_analysis:
        return stops_analysis
    summary = dict(stops_analysis)
    summary["stops"] = [
        {k: v for k, v in stop.items() if k != "location"}
        for stop in stops_analysis.get("stops", [])
    ]
    return summary


def _strip_training_context_for_coach(training_context: Optional[dict]) -> Optional[dict]:
    """Project the stored training_context to what the coach pack carries (#462).

    The stored `DerivedMetric.training_context` carries `intensity_distribution_7d`
    (easy/moderate/hard session counts) plus the recovery-recency signals
    `days_since_last_hard` / `hard_sessions_this_week`. The intensity distribution is
    referenced by NO prompt rule and NO deterministic consumer, and its modality picture
    is already covered by the `recent_training` section, so it is dropped from the coach
    pack — the recency signals (which `risk.py` and prompt rule 11 use) are kept. The
    STORED column is untouched: `risk.py` reads the full dict at analyse time, before this.

    Pass-through for None / an empty dict."""
    if not training_context:
        return training_context
    return {k: v for k, v in training_context.items() if k != "intensity_distribution_7d"}


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

    zones_calibrated, zones_basis = zones_calibration(profile)

    # Training context: the recovery-recency signals (days_since_last_hard /
    # hard_sessions_this_week) the coach uses for recovery advice. The redundant,
    # unreferenced intensity_distribution_7d sub-field is stripped from the pack (#462);
    # the stored DerivedMetric column keeps it for risk.py, which already ran.
    training_context = (
        _strip_training_context_for_coach(metrics.training_context) if metrics else None
    )

    return FocusPayload(
        activity=ActivityContext(
            date=subject.local_start.isoformat(),  # local wall-clock (#399)
            weekday=subject.local_start.strftime("%a"),  # #650: the "today" anchor
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
            efficiency_analysis=(
                _summarize_efficiency_for_coach(metrics.efficiency_analysis)
                if metrics
                else None
            ),
            # #522: COACH_STOPS_ANALYSIS_ENABLED off => None (the coach reads no stops,
            # the same shape as a run with no stops). The pipeline still computes and
            # stores stops_analysis on the DerivedMetric for non-coach use.
            stops_analysis=(
                _strip_stops_for_coach(metrics.stops_analysis)
                if (metrics and settings.COACH_STOPS_ANALYSIS_ENABLED)
                else None
            ),
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
            # #522: COACH_SLEEP_QUALITY_ENABLED off => the coach never sees sleep
            # (same shape as a check-in with no sleep). risk.py/flags.py still read
            # the stored value with their safe null-defaults.
            sleep_quality=(
                check_in.sleep_quality
                if (check_in and settings.COACH_SLEEP_QUALITY_ENABLED)
                else None
            ),
            notes=check_in.notes if check_in else None,
        ),
        stream_view=fetch_stream_view(db, subject.id) if deep else None,
    )


def _build_body_context(profile: Optional[UserProfile]) -> Optional[BodyContext]:
    """The runner's stated build (#742), or None when they have stated neither figure.

    Abstains rather than partially filling: a body section present with both values
    null would tell the coach "we asked and got nothing", which is noise. Absent means
    absent. One figure alone IS emitted — a runner who has given only their weight has
    still told the coach the thing that most changes how they should be trained.
    """
    if profile is None:
        return None
    weight = getattr(profile, "weight_kg", None)
    height = getattr(profile, "height_cm", None)
    if weight is None and height is None:
        return None
    return BodyContext(weight_kg=weight, height_cm=height)


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

    return BBaseline(
        profile=ProfileContext(
            goal_type=profile.goal_type if profile else None,
            experience_level=profile.experience_level if profile else None,
            weekly_days_available=profile.weekly_days_available if profile else None,
            injury_notes=profile.injury_notes if profile else None,
            max_hr=profile.max_hr if profile else None,
            max_hr_source=getattr(profile, "max_hr_source", None) if profile else None,
            current_weekly_km=profile.current_weekly_km if profile else None,
            # #742: built unconditionally here (like perceived_effort/calibration) and
            # gated in build_context_pack, which is the layer that knows the prompt id.
            body=_build_body_context(profile),
        ),
        longitudinal=_build_longitudinal_context(db, activity),
        perceived_effort=build_perceived_effort(
            rpe=check_in.rpe if check_in else None,
            effort_axis=metrics.effort if metrics else None,
            effort_score=round(metrics.effort_score, 1) if metrics and metrics.effort_score is not None else None,
            discount_signals=metrics.discount_signals if metrics else None,
            pain_scores=_recent_pain_scores(db, activity),
        ),
        # M7 adherence is gated off pending recalibration (it nagged about prior
        # next_steps the runner had already moved past); off => the empty form. When
        # on, it resolves through the shared read-time signal seam (#492) — an
        # always-emitted signal (no PromptFeature gate), so `gather` runs its compute
        # on every prompt. (The COACH_ADHERENCE_ENABLED feature-flag is orthogonal to
        # the prompt-feature gate and stays here, an explicit kill switch.)
        adherence=(
            gather(_ADHERENCE_SIGNAL, db, activity, None, activity.start_date)
            if settings.COACH_ADHERENCE_ENABLED
            else AdherenceContext(prior_report_date=None, outcomes=[])
        ),
        # (believed_facts / preference_profile / narrative were retired in M4,
        # ADR 0025, replaced by the runner memory profile. The fields remain as
        # never-populated Optional stubs so historical stored packs still
        # strict-parse; left unset here, they default None and drop from
        # serialization via the PACK_SECTIONS registry.)
        # M9 calibration: an always-emitted read-time signal through the shared seam
        # (#492; no PromptFeature gate, so `gather` runs its compute on every prompt).
        calibration=gather(_CALIBRATION_SIGNAL, db, activity, None, activity.start_date),
        # #522: COACH_SALIENCE_ENABLED drops the pack section only. The deterministic
        # fuller-turn scheduling (compute_safety_override in the opener path) reads its
        # own salience compute, not this pack section, so it is untouched.
        salience=(
            _build_salience_context(db, activity)
            if settings.COACH_SALIENCE_ENABLED
            else None
        ),
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


def _build_longitudinal_context(db: Session, activity: Activity) -> Optional[LongitudinalContext]:
    """Assemble the M4 longitudinal contrast: a digest of the runner's last 1-2
    reports plus the M2 baseline trend matching this activity's context bucket.

    The prior-report digests are pulled through the retrieval seam (A2b), which
    reads A2a's stored CoachReport.digest artifact (falling back to re-projection
    for pre-A2a rows). Both halves degrade to empty/None (no prior reports, no
    comparable trend) without failing.
    """
    # #522: the whole section can be killed (both halves). Off => None, dropped from
    # serialization by PACK_SECTIONS so the coach receives no longitudinal section.
    if not settings.COACH_LONGITUDINAL_ENABLED:
        return None
    # The prior-report digest is gated off pending recalibration: it replayed a
    # stale rest-day theme forward each run. Off => empty digest, while the numeric
    # baseline trend (no report language to echo) is retained.
    return LongitudinalContext(
        prior_reports=(
            fetch_prior_digests(db, activity)
            if settings.COACH_PRIOR_REPORTS_ENABLED
            else []
        ),
        baseline_trend=_matching_baseline_trend(db, activity),
    )


def _recent_pain_scores(db: Session, activity: Activity) -> list[int]:
    """Chronological (oldest-first) pain scores for the M6 pain trend, scoped to
    THIS run's pain location so the trend never conflates distinct injuries (a
    knee easing while a shin builds is not one trend). Returns an empty list when
    this run has no pain location to anchor on, so the trend degrades to absent.
    Reads existing rows only.

    INTENTIONAL DESIGN (#303): a receipt pain tap (from the Telegram receipt
    keyboard, #296 / ADR 0018) records a pain score with NO body location because
    the coarse tap set has no place to capture one. When this function encounters
    no location it returns [] and the M6 location-scoped trend stays absent for
    that run -- that is the correct behaviour. The receipt pain is NOT lost: it IS
    visible to the location-agnostic M9 referral / red-flag path via
    `_recent_pain_scores_any`, so a flagged niggle still drives the safety check.
    The gap between the two paths is a deliberate choice, not a silent drop."""
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


def _build_adherence_context(
    db: Session, activity: Activity, as_of: Optional[datetime] = None
) -> AdherenceContext:
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

    An ALWAYS-EMITTED read-time history-scan signal (#492): no `gate_feature`, so it
    is reached on every prompt and always produces a section (empty when no prior
    report), never dropped. `as_of` is part of the shared seam signature and is
    unused here — the scans key on the source report / this run's start_date. This
    compute-now adapter is exactly the swappable seam #203 turns into a read-stored
    one without touching `context.py`."""
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
                date=act.local_start.isoformat(),  # local wall-clock (#399)
                effort=metrics.effort,
                duration_class=metrics.duration_class,
                structure=metrics.structure,
                is_race=metrics.is_race,
                confidence=metrics.confidence,
                user_intent=act.user_intent,
                # A fired discount-signals annotation marks a confounded run
                # (heat/hills/stimulant); the adherence window themes treat it as
                # exculpatory, not a missed opportunity (#579).
                discount_signals_fired=bool(metrics.discount_signals),
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


def _build_calibration_context(
    db: Session, activity: Activity, as_of: Optional[datetime] = None
) -> CalibrationContext:
    """Assemble the M9 calibration section: individualise this run's HR-drift
    reading against the runner's own typical drift for these conditions, and a
    non-diagnostic referral nudge for any computable red-flag pattern. Both are
    computed at read time (the baseline is recomputed after the pipeline) and
    degrade cleanly. Neither overrides the re-derived DerivedMetric.

    An ALWAYS-EMITTED read-time history-scan signal (#492): no `gate_feature`, so it
    is reached on every prompt and always produces a section (empty/degraded when no
    drift was measured), never dropped. `as_of` is part of the shared seam signature
    and is unused here — the scans key on the activity's own bucket/window."""
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
        # undefer raw_summary (#359): the loop below reads average_temp from each
        # scanned row for bucket matching, so a deferred column would lazy-load
        # once per row -> N+1 across the calibration scan.
        .options(joinedload(Activity.metrics), undefer(Activity.raw_summary))
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
    rows only; bounded scan.

    INTENTIONAL DESIGN (#303): this query has NO location filter, so a receipt
    pain tap (which records pain_score with pain_location=None, #296 / ADR 0018)
    IS included here. That is deliberate: the M9 referral fires on SUSTAINED pain
    in general, and a coarse "felt a niggle" tap carries exactly the signal it
    needs for that check. Compare with `_recent_pain_scores`, which requires a
    location and intentionally excludes location-free receipt pains from the
    stricter M6 per-location trend."""
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


def _build_salience_context(db: Session, activity: Activity) -> SalienceContext:
    """Assemble the A4 salience facts: the deterministic novelty signal and the
    deterministic safety override.

    Both read existing rows only and degrade cleanly. Salience is hybrid (ADR
    0010): these facts plus the existing baseline/flag/adherence sections feed the
    opener LLM's depth judgment, and the safety override is the only deterministic
    force-fuller signal. Neither overrides this run's re-derived DerivedMetric."""
    metrics = activity.metrics
    current = AxisSnapshot(
        structure=metrics.structure if metrics else None,
        duration_class=metrics.duration_class if metrics else None,
        is_race=metrics.is_race if metrics else None,
        is_hilly=metrics.is_hilly if metrics else None,
    )
    novelty = compute_novelty(current, _novelty_axis_history(db, activity))
    override = compute_safety_override(
        flags=metrics.flags if metrics else [],
        pain_scores=_recent_pain_scores_any(db, activity),
    )
    return SalienceContext(
        novelty=NoveltyContext(**novelty),
        safety_override=SafetyOverride(**override),
    )


def _novelty_axis_history(db: Session, activity: Activity) -> list[AxisSnapshot]:
    """The classification axes of the runner's PRIOR analysed activities (this run
    excluded), for the novelty first-of-its-kind check. Projects only the four
    axis columns (cheap), reads existing rows only, bounded by
    _NOVELTY_HISTORY_SCAN_LIMIT."""
    rows = (
        db.query(
            DerivedMetric.structure,
            DerivedMetric.duration_class,
            DerivedMetric.is_race,
            DerivedMetric.is_hilly,
        )
        .join(Activity, DerivedMetric.activity_id == Activity.id)
        .filter(
            Activity.user_id == activity.user_id,
            Activity.id != activity.id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date < activity.start_date,
        )
        .order_by(Activity.start_date.desc(), Activity.id.desc())
        .limit(_NOVELTY_HISTORY_SCAN_LIMIT)
        .all()
    )
    return [
        AxisSnapshot(structure=s, duration_class=d, is_race=r, is_hilly=h)
        for s, d, r, h in rows
    ]


# --- The read-time history-scan signals (#492) ------------------------------
# The signals reached through the one `ReadTimeSignal` seam. Each pairs a
# `_build_*_context` adapter (the bounded scan + thin-history abstention) with its
# optional prompt-feature gate, so `gather` applies gating + dispatch uniformly and
# `build_context_pack` / `build_b_baseline` never repeat a private convention.
#
# The ALWAYS-EMITTED signals (gate_feature=None — calibration and adherence live in
# the B baseline and always produce a section, possibly empty/degraded) run on every
# prompt; the PROMPT-GATED signals are dropped from serialization when the active
# prompt does not carry the feature, keeping the pack byte-stable elsewhere.
#
# A stored-artifact adapter is just a signal whose compute reads a stored row instead
# of scanning history, at the SAME (db, activity, as_of) shape and gate: `_MEMORY_SIGNAL`
# already is one, and #203 slots adherence/calibration in the same way, with no
# call-site change. See read_time_signals.py for why the seam stays (#699 part b).
#
# #800: this module now registers ONLY the compute adapters. Each signal's prompt
# gate and its drop-effect kill switch come from `signal_registry.COACH_SIGNALS`, so
# they are stated once rather than restated here (and, for the kill switches, a
# third time inside the builder). `build_signals` fails AT IMPORT if a declared
# adapter has no compute or a compute has no declaration.
def _build_schedule_context(db, activity, as_of):
    """#830: the runner's plan for this week, as the coach receives it.

    Lazily imported: the schedule is a separate surface, and a fault in it must
    never stop a report being written.
    """
    try:
        from app.services.schedule.coach_view import build_schedule_context

        return build_schedule_context(db, activity, as_of)
    except Exception:  # noqa: BLE001 — a report must survive a schedule fault
        logger.exception(
            "schedule: pack section failed for activity %s; coaching without it",
            getattr(activity, "id", None),
        )
        return None


_COMPUTES: dict[str, SignalCompute] = {
    "calibration": _build_calibration_context,
    "adherence": _build_adherence_context,
    "training_load": _build_training_load_context,
    "training_volume": _build_training_volume_context,
    "recent_training": _build_recent_training_context,
    # ADR 0026 Slice 2 (#670): the redefined right_now content — readiness (the renamed
    # training_load) and recent_weeks (the merged volume + recent_training read).
    "readiness": _build_readiness_context,
    "recent_weeks": _build_recent_weeks_context,
    "training_history": _build_training_history_context,
    # ADR 0026 Slice 2 (#670): the rebased/enriched ladder for grouped_v2. Mutually
    # exclusive with `training_history` by prompt feature; the assembler `or`s the two
    # into the ONE `training_history` pack key.
    "training_history_2wk": _build_training_history_2wk_context,
    "memory": _build_memory_context,
    "intensity": _build_intensity_context,
    # #830: the runner's own plan for this week. The gate and the kill switch are
    # declared in signal_registry and applied by `gather`, so this registers only
    # the compute.
    "schedule": _build_schedule_context,
}

_SIGNALS = build_signals(_COMPUTES)

_CALIBRATION_SIGNAL = _SIGNALS["calibration"]
_ADHERENCE_SIGNAL = _SIGNALS["adherence"]
_TRAINING_LOAD_SIGNAL = _SIGNALS["training_load"]
_TRAINING_VOLUME_SIGNAL = _SIGNALS["training_volume"]
_RECENT_TRAINING_SIGNAL = _SIGNALS["recent_training"]
_READINESS_SIGNAL = _SIGNALS["readiness"]
_RECENT_WEEKS_SIGNAL = _SIGNALS["recent_weeks"]
_TRAINING_HISTORY_SIGNAL = _SIGNALS["training_history"]
_TRAINING_HISTORY_2WK_SIGNAL = _SIGNALS["training_history_2wk"]
_MEMORY_SIGNAL = _SIGNALS["memory"]
_INTENSITY_SIGNAL = _SIGNALS["intensity"]
_SCHEDULE_SIGNAL = _SIGNALS["schedule"]


# ---------------------------------------------------------------------------
# The pack's fact scans (#804)
# ---------------------------------------------------------------------------
#
# Five read-time signals each scan the runner's history over their own bounded
# window, all through the SAME projection, all as of the subject activity's local
# day. Four of those windows are sub-windows of the widest. Declaring the spans
# here — one home per number, read by the builder that needs it AND by the planner
# below — lets one fetch per session-shape answer every one of them, instead of the
# projection being re-issued per signal over overlapping ranges.
#
# `days` is how far back from the local day the scan reaches; the upper bound is
# always local_day + 1, because the projection's end is EXCLUSIVE and the current
# local day (today's run) must stay inside the window. `shape` is the #650
# session-shape opt-in: it keys the cache separately because the wide scans must
# stay lean and never load the per-rep interval JSON.
_FACT_SCANS: dict[str, tuple[int, bool]] = {
    "training_volume": (91, False),      # trailing 7d + the 12-week norm baseline
    "recent_training": (210, True),      # the 30d window + its ~6-month vs-typical baseline
    "recent_weeks": (91, True),          # two weeks of structure + the ~12-week norm
    "training_history": (_TRAINING_HISTORY_FETCH_DAYS, False),   # ~10 years, summary-only
    "intensity": (_INTENSITY_FETCH_DAYS, False),                 # 28d window + its prior 28d
}

# Which signal's gating decides whether a scan happens at all. `training_history`
# is fed by either of two mutually-exclusive signals, so the scan is needed when
# EITHER will run.
_SCAN_SIGNALS: dict[str, tuple] = {
    "training_volume": (_TRAINING_VOLUME_SIGNAL,),
    "recent_training": (_RECENT_TRAINING_SIGNAL,),
    "recent_weeks": (_RECENT_WEEKS_SIGNAL,),
    "training_history": (_TRAINING_HISTORY_SIGNAL, _TRAINING_HISTORY_2WK_SIGNAL),
    "intensity": (_INTENSITY_SIGNAL,),
}


def _scan_window(activity: Activity, name: str) -> tuple:
    days, shape = _FACT_SCANS[name]
    day = activity.local_start.date()
    return day - timedelta(days=days), day + timedelta(days=1), shape


def _scan_facts(db: Session, activity: Activity, name: str):
    """One declared fact scan, served from the pack's cache when one is open."""
    start, end, shape = _scan_window(activity, name)
    return _scan(
        db, start, end, user_id=activity.user_id, include_session_shape=shape
    )


def _prewarm_fact_scans(db: Session, activity: Activity, prompt_id) -> None:
    """Fetch, per session-shape, the WIDEST scan this prompt will actually ask for.

    Every narrower scan is then a sub-window of it and costs no query. Deliberately
    driven by `will_run` rather than by the widest declared span, so a prompt that
    gates the 10-year history scan off does not pay for it: this only ever fetches a
    span some signal was going to request anyway.

    Best-effort — a prewarm failure must not break the pack. Each builder still asks
    for its own window, so a cold cache simply means the previous behaviour.
    """
    widest: dict[bool, int] = {}

    def want(name: str) -> None:
        days, shape = _FACT_SCANS[name]
        if days > widest.get(shape, 0):
            widest[shape] = days

    for name, signals in _SCAN_SIGNALS.items():
        if any(will_run(sig, prompt_id) for sig in signals):
            want(name)
    # The intensity scan also fans out directly under an intensity-read prompt, where
    # _INTENSITY_SIGNAL itself abstains (ADR 0026 Slice 3).
    if is_intensity_read_prompt(prompt_id):
        want("intensity")

    day = activity.local_start.date()
    for shape, days in widest.items():
        try:
            _scan(
                db,
                day - timedelta(days=days),
                day + timedelta(days=1),
                user_id=activity.user_id,
                include_session_shape=shape,
            )
        except Exception:  # noqa: BLE001 - a prewarm is never load-bearing
            logger.exception("coach fact-scan prewarm failed for activity %s", activity.id)


