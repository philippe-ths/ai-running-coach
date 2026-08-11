"""The coach drafts the plan (#830).

There is no plan-builder form and there will not be one. The coach generates the
first schedule from what it already knows — goal, race, history, load, memory,
stance — and the runner refines it in conversation. This module is that
generation: assemble what a coach actually needs, make one forced-tool call
through the metered turn envelope, put the answer through the deterministic gate,
and store it only if it survives.

Three decisions worth knowing before reading
--------------------------------------------
1. **The model never estimates load.** It says what each session IS; `effort.py`
   prices it from the runner's own history. The North Star names `effort_score`
   as the hard case precisely because a model reads a load number as an intensity
   verdict.

2. **A failed draft does not degrade, it fails.** The report path can serve prose
   without its structured tail, because half a report still coaches. Half a plan
   does not: a week whose rules cannot be satisfied is worse than no week, since
   the runner would act on it. So: one retry with the failures fed back, then
   `status="failed"` and nothing stored.

3. **Drafting is runner-triggered, never automatic.** No job wakes up and spends
   tokens writing plans for runners who never opened the screen, and free mode is
   a destination rather than an empty state waiting to be filled.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.services.activity_facts import query_facts
from app.services.coach import turn
from app.services.coach.retrieval import fetch_corpus
from app.services.coach.stance import resolve_stance
from app.services.coach.volume import build_training_volume
from app.services.readiness import build_readiness
from app.services.schedule import store
from app.services.schedule.draft_contract import (
    RECORD_TRAINING_PLAN_TOOL,
    DraftedPlan,
    normalise,
)
from app.services.schedule.effort import build_load_model, estimate_effort
from app.services.schedule.plan_validator import validate_drafted_plan
from app.services.weeks import resolve_week_start, week_start

logger = logging.getLogger(__name__)

# How much history the drafting context reads. The load model wants ~6 months; the
# volume norm wants its 12-week baseline plus the current week.
_HISTORY_DAYS = 200

_SYSTEM_PROMPT = """You are a running coach writing this runner's training plan.

You are given what you already know about them: their goal, their race if they \
have one, what they have actually been doing, their current condition, what they \
have told you, and the school of training you coach from. Write the plan you would \
write for THIS runner.

# HOW TO PLAN

Coach the runner in front of you, not the median one. Their own recent training is \
the reference for what is normal for them — population defaults are a starting \
point to depart from, not a template to apply. A build that is right for a 60 km/week \
runner can injure a 25 km/week one, and the same is true in reverse: do not prescribe \
a beginner's week to someone who has been training for years.

Give CONCRETE sessions for the near weeks and SHAPE ONLY for the weeks beyond. \
Nobody knows what week nine looks like yet, and pretending to is how a plan stops \
being believable.

# PLACEMENT

Every session gets a window — the first and last day it may fall on.

- The same day for both pins it to that day. Use this when the day genuinely \
matters (the long run needs a free morning; the runner has a club night).
- A range means it floats: the session must happen, the exact day does not matter.
- The whole week means it can go anywhere.

Prefer a window to a pin. A pinned week is rigid and a rigid week is the one that \
gets abandoned after one bad Tuesday. Pin what genuinely needs pinning and let the \
rest float.

# RULES

For a runner whose sessions float, the spacing rules ARE the plan. Say them as \
rules rather than in prose: what needs a rest day after it, what must not fall the \
day before what, how far apart the hard days must be, which days a session needs.

Write rules you actually intend. Every one is enforced — a week that cannot satisfy \
its own rules is rejected and you will be asked to write it again.

Each rule kind takes specific arguments and is rejected without them. \
`rest_day_after` needs `intent`. `no_intent_day_before` needs BOTH `before_intent` \
and `target_intent`. `min_days_between` needs `intent_a`, `intent_b` and `days`. \
`preferred_days` needs `intent` and `weekdays`. `max_sessions_per_day` needs \
`count`. The tool description carries a worked example of each.

# WHAT NOT TO DO

- Do not estimate training load, effort scores or TRIMP for a session. Say what the \
session is — discipline, intent, how long or how far — and the app computes what it \
costs from this runner's own history.
- Do not put a distance or duration on a rest day.
- Every other session needs enough to size it: a distance, a duration, or rep \
structure. A session with none of the three is rejected.
- Do not plan sessions in the past.
- Do not invent a race, a goal or an injury the runner has not told you about.
- Do not give medical advice, diagnose, or prescribe treatment. If something in \
their history looks like a medical question, plan conservatively around it and say \
nothing clinical.

Answer only by calling record_training_plan."""


@dataclass
class DraftOutcome:
    ok: bool
    plan_id: Optional[uuid.UUID] = None
    failures: Optional[List[str]] = None
    summary: Optional[str] = None


def fetch_draft_facts(db: Session, user: User, today: date) -> List[Any]:
    """The one fact projection a draft needs.

    It was fetched twice — once to write the context and again for the load model
    and the running norm — which is the duplication `activity_facts.scan_cache`
    exists to collapse for the coach pack.
    """
    return query_facts(
        db,
        today - timedelta(days=_HISTORY_DAYS),
        today + timedelta(days=1),
        user_id=user.id,
    )


def _profile_lines(user: User, profile: Any) -> List[str]:
    lines = []
    if profile is None:
        return ["Nothing stated yet."]
    for label, value in (
        ("Goal", getattr(profile, "goal_type", None)),
        ("Experience", getattr(profile, "experience_level", None)),
        ("Days available each week", getattr(profile, "weekly_days_available", None)),
        ("Current weekly km (stated)", getattr(profile, "current_weekly_km", None)),
        ("Max HR", getattr(profile, "max_hr", None)),
        ("Injury notes", getattr(profile, "injury_notes", None)),
        ("Weight (kg)", getattr(profile, "weight_kg", None)),
        ("Height (cm)", getattr(profile, "height_cm", None)),
    ):
        if value not in (None, "", []):
            lines.append(f"- {label}: {value}")
    return lines or ["Nothing stated yet."]


def build_draft_context(
    db: Session,
    user: User,
    *,
    today: date,
    weeks: int,
    facts: Optional[List[Any]] = None,
) -> str:
    """What a coach needs to write this plan — and nothing it does not.

    Deliberately not the report's context pack: that is anchored to one finished
    activity and answers "what just happened". This answers "what should happen
    next", so it carries the runner's shape over months rather than one run's
    measurements, and no stream data at all.
    """
    starts_on = resolve_week_start(getattr(user, "profile", None))
    first_week = week_start(today, starts_on)
    # One projection serves the whole draft. `draft_plan` fetches it once and
    # passes it in; a caller that has none (a test, a preview) gets its own.
    if facts is None:
        facts = fetch_draft_facts(db, user, today)

    parts: List[str] = []
    parts.append(f"TODAY: {today.isoformat()}")
    parts.append(
        f"PLAN FROM: {first_week.isoformat()} (the runner's week starts on "
        f"{'Sunday' if starts_on == 6 else 'Monday'})"
    )
    parts.append(
        f"HORIZON: {weeks} weeks. Give concrete sessions for the first "
        f"{settings.SCHEDULE_CONCRETE_WEEKS} weeks and shape only beyond that."
    )

    parts.append("\n## THE RUNNER")
    parts.extend(_profile_lines(user, getattr(user, "profile", None)))

    races = store.list_goal_races(db, user.id, on_or_after=today)
    if races:
        parts.append("\n## THEIR RACE")
        for race in races[:3]:
            weeks_out = (race.race_date - today).days / 7
            parts.append(
                f"- {race.name}: {race.race_date.isoformat()} "
                f"({race.distance_m / 1000:.1f} km, priority {race.priority}, "
                f"{weeks_out:.0f} weeks away)"
            )
    else:
        parts.append("\n## THEIR RACE\nNo race stated. Plan for general progression.")

    volume = build_training_volume(facts, today, starts_on)
    parts.append("\n## WHAT THEY ACTUALLY DO")
    if volume.has_baseline:
        for metric in volume.calendar_week.metrics:
            if metric.norm_weekly is None:
                continue
            if metric.metric == "distance_m":
                parts.append(
                    f"- Typical week, ALL activities: {metric.norm_weekly / 1000:.1f} "
                    f"km (every discipline together — walks included)"
                )
            elif metric.metric == "sessions":
                parts.append(
                    f"- Typical week: {metric.norm_weekly:.1f} sessions of any kind"
                )
        # The number that actually bounds a running plan, given explicitly. The
        # all-activity figure above is the one a coach is most likely to misread
        # as running volume — for a runner who walks a lot it is more than double
        # their running — so the running-only norm sits beside it rather than
        # being left to be inferred from a label.
        running_norm = _norm_running_metres(facts, today, starts_on)
        if running_norm:
            parts.append(
                f"- Typical week, RUNNING ONLY: {running_norm / 1000:.1f} km. This is "
                f"the figure a running plan is built against."
            )
    else:
        parts.append(
            "- Not enough history to establish what is typical for them. Plan "
            "conservatively and say so in your summary."
        )

    model = build_load_model(facts, today)
    if model.sessions_seen:
        mix = ", ".join(
            f"{discipline} x{count}"
            for discipline, count in sorted(
                model.sessions_seen.items(), key=lambda kv: -kv[1]
            )
        )
        parts.append(f"- Disciplines they train, last 6 months: {mix}")
        parts.append(
            "- Plan the disciplines they actually do. Cross-training is how load "
            "moves sideways when running has to come down."
        )

    readiness = build_readiness(db, user.id, today)
    if readiness is not None:
        parts.append("\n## THEIR CURRENT CONDITION")
        parts.append(
            f"- Fitness {readiness.fitness:.0f}, fatigue {readiness.fatigue:.0f}, "
            f"form {readiness.form:.0f} ({readiness.condition}). These are load "
            f"balances, not intensity verdicts and not a diagnosis."
        )

    memory = _memory_lines(db, user)
    if memory:
        parts.append("\n## WHAT THEY HAVE TOLD YOU")
        parts.extend(memory)

    relationship = turn.relationship_for_user(db, user.id)
    stance = resolve_stance(relationship)
    corpus = fetch_corpus(db, user.id, getattr(stance, "school_id", None))
    school = getattr(corpus, "school", None)
    if school is not None:
        parts.append("\n## HOW YOU COACH")
        parts.append(f"- School: {getattr(school, 'name', '')}")
        stance_text = getattr(school, "stance", None)
        if stance_text:
            parts.append(f"- {stance_text}")
        for principle in (getattr(school, "principles", None) or [])[:5]:
            parts.append(f"- {principle}")

    return "\n".join(parts)


def _memory_lines(db: Session, user: User) -> List[str]:
    if not settings.COACH_MEMORY_ENABLED:
        return []
    try:
        from app.services.coach.memory_store import get_memory

        row = get_memory(db, user.id)
    except Exception:
        return []
    if row is None or not getattr(row, "profile", None):
        return []
    lines: List[str] = []
    profile = row.profile
    if isinstance(profile, dict):
        for section in (
            "who_you_are",
            "limits_and_constraints",
            "goals_and_plans",
            "what_works_for_you",
        ):
            for entry in (profile.get(section) or [])[:4]:
                lines.append(f"- {entry}")
    return lines[:16]


async def draft_plan(
    db: Session, user: User, plan: TrainingPlan, *, today: Optional[date] = None
) -> DraftOutcome:
    """Generate, validate and store one plan. One retry, then fail visibly."""
    today = today or date.today()
    weeks = settings.SCHEDULE_HORIZON_WEEKS
    starts_on = resolve_week_start(getattr(user, "profile", None))

    if turn.over_budget(user.id):
        return DraftOutcome(ok=False, failures=["over the spend cap for this period"])

    facts = fetch_draft_facts(db, user, today)
    context = build_draft_context(db, user, today=today, weeks=weeks, facts=facts)
    client = turn.build_client(turn.TurnKind.SCHEDULE, user.id)

    load_model = build_load_model(facts, today)
    norm_running = _norm_running_metres(facts, today, starts_on)

    # Two budgets, deliberately separate. A transport blip is not the coach's
    # fault, so it must not consume the one chance to REWRITE a rejected plan —
    # sharing them meant a 429 on the first call left a genuinely fixable plan
    # with no attempt left. Transport errors also never reach the prompt: feeding
    # a provider's exception text back would put its internals into model input
    # to no purpose, since the coach cannot act on them.
    failures: List[str] = []
    rewrites_left = 2
    transport_retries_left = 1

    while rewrites_left > 0:
        user_message = context
        if failures:
            user_message = (
                f"{context}\n\n## YOUR PREVIOUS ATTEMPT WAS REJECTED\n"
                + "\n".join(f"- {failure}" for failure in failures)
                + "\n\nWrite the plan again, fixing every one of these."
            )

        try:
            raw = await client.generate_structured(
                system=_SYSTEM_PROMPT,
                user=user_message,
                tool=RECORD_TRAINING_PLAN_TOOL,
                max_tokens=8192,
            )
        except Exception as exc:  # transport, timeout, refusal
            logger.warning("schedule draft: generation call failed: %s", exc)
            if transport_retries_left > 0:
                transport_retries_left -= 1
                continue
            return DraftOutcome(
                ok=False, failures=["the coach could not be reached"]
            )

        rewrites_left -= 1

        try:
            drafted = DraftedPlan.model_validate(normalise(raw))
        except Exception as exc:
            logger.warning("schedule draft: off-contract plan: %s", exc)
            failures = [f"the plan was not the shape the tool requires: {exc}"]
            continue

        check = validate_drafted_plan(
            drafted,
            today=today,
            starts_on=starts_on,
            norm_weekly_running_m=norm_running,
            horizon_weeks=weeks,
        )
        if not check.ok:
            logger.info("schedule draft: rejected: %s", check.failures)
            failures = check.failures
            continue

        _persist(db, user, plan, drafted, load_model, model_id=client.model)
        return DraftOutcome(ok=True, plan_id=plan.id, summary=drafted.summary)

    return DraftOutcome(ok=False, failures=failures)


def _norm_running_metres(facts, today: date, starts_on: int) -> Optional[float]:
    """The runner's own typical weekly RUNNING distance, or None.

    The volume builder's norm is all-activity by design, so it cannot answer this
    on its own; the running share is taken from the same fact stream over the same
    baseline rather than by inventing a second definition of typical.
    """
    from app.services.activity_facts import (
        BASELINE_WEEKS,
        baseline_window,
        is_run,
        norm_per_day,
    )

    baseline_end = today - timedelta(days=7)
    window = baseline_window(facts, baseline_end, BASELINE_WEEKS * 7)
    if window is None:
        return None
    start, end, days = window
    runs = [f for f in facts if is_run(f)]
    per_day = norm_per_day(runs, start, end, days)
    distance = per_day.get("distance_m")
    return distance * 7 if distance is not None else None


def _persist(
    db: Session,
    user: User,
    plan: TrainingPlan,
    drafted: DraftedPlan,
    load_model,
    *,
    model_id: str,
) -> None:
    """Write the accepted plan and make it the runner's active one."""
    plan.rules = [rule.model_dump(mode="json") for rule in drafted.rules]
    plan.week_shapes = [
        shape
        for shape in (
            _shape_for(week, load_model) for week in drafted.sketch_weeks
        )
        if shape is not None
    ]
    horizon_ends = [w.week_start for w in drafted.weeks] + [
        s.week_start for s in drafted.sketch_weeks
    ]
    plan.horizon_end = (
        max(horizon_ends) + timedelta(days=6) if horizon_ends else None
    )
    # The model the draft ACTUALLY ran on, taken from the client rather than
    # re-read from config — the provenance gap `service._persist_report` still
    # carries and that this path has no reason to inherit.
    plan.model_id = model_id

    for week in drafted.weeks:
        for session in week.sessions:
            db.add(
                PlannedSession(
                    plan_id=plan.id,
                    user_id=user.id,
                    window_start=session.window_start,
                    window_end=session.window_end,
                    intent=session.intent,
                    discipline=session.discipline,
                    commitment=session.commitment,
                    title=session.title,
                    detail=session.detail,
                    target_distance_m=session.target_distance_m,
                    target_duration_s=session.target_duration_s,
                    target_effort_score=estimate_effort(
                        load_model,
                        session.discipline,
                        duration_s=session.target_duration_s,
                        distance_m=session.target_distance_m,
                    ),
                    structure=session.structure(),
                )
            )

    store.activate_plan(db, plan)


def _shape_for(sketch, load_model) -> Optional[dict]:
    """A sketched week as the stored `PlannedWeekShape`.

    The model gives counts; the mixes are shares of load, so they are computed
    here from the same load model that prices concrete sessions. One number, one
    owner — a mix can never contradict the total it is a mix of.
    """
    counts = sketch.sessions_by_discipline or {}
    by_discipline = {}
    for discipline, count in counts.items():
        if count <= 0:
            continue
        if discipline == "run" and sketch.target_running_distance_m:
            # Price the running share off the DISTANCE the week names, not off a
            # per-session median. Sizing it by session count alone made "4 runs,
            # 20 km" and "4 runs, 40 km" store the same load and therefore draw
            # the same horizon bar — which would make the ramp, the one thing the
            # horizon exists to show, invisible.
            by_discipline[discipline] = (
                estimate_effort(
                    load_model,
                    discipline,
                    duration_s=None,
                    distance_m=sketch.target_running_distance_m,
                )
                or 0.0
            )
            continue
        per_session = (
            estimate_effort(load_model, discipline, duration_s=None, distance_m=None)
            or 0.0
        )
        by_discipline[discipline] = per_session * count
    total = sum(by_discipline.values())
    discipline_mix = (
        {k: round(v / total, 4) for k, v in by_discipline.items() if v > 0}
        if total > 0
        else {}
    )

    intent_total = sum((sketch.intent_counts or {}).values())
    intent_mix = (
        {
            intent: round(count / intent_total, 4)
            for intent, count in sketch.intent_counts.items()
            if count > 0
        }
        if intent_total > 0
        else {}
    )

    return {
        "week_start": sketch.week_start.isoformat(),
        "phase": sketch.phase,
        "target_running_distance_m": sketch.target_running_distance_m,
        "target_effort_score": round(total, 1) if total > 0 else None,
        "discipline_mix": discipline_mix,
        "intent_mix": intent_mix,
    }


def enqueue_draft(user_id, plan_id) -> None:
    """Enqueue the drafting job, decoupled from the request.

    Imported lazily and swallowing enqueue errors, the `enqueue_distillation`
    idiom: the queue dependency stays off the read endpoints' import path, and a
    Redis hiccup leaves a `drafting` row the runner can retry rather than a 500
    on a request that has already written one.
    """
    try:
        from app.core.queue import queue
        from app.jobs.generate_schedule import generate_schedule_job

        queue.enqueue(generate_schedule_job, str(user_id), str(plan_id))
    except Exception:  # noqa: BLE001 — enqueue is fire-and-forget
        logger.exception("failed to enqueue schedule draft for plan %s", plan_id)
