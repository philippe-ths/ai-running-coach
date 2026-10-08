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

import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.schemas.season import DraftLog, SeasonPlan
from app.services.activity_facts import query_facts
from app.services.coach import turn
from app.services.coach.retrieval import fetch_corpus
from app.services.coach.stance import resolve_stance
from app.services.coach.volume import build_training_volume
from app.services.readiness import build_readiness
from app.services.schedule import goals
from app.services.schedule import season_store, store
from app.services.schedule.draft_contract import (
    MAX_CONCRETE_WEEKS,
    MAX_SESSIONS_PER_WEEK,
    RECORD_TRAINING_PLAN_TOOL,
    DraftedPlan,
    normalise,
)
from app.services.schedule.effort import build_load_model, estimate_effort
from app.services.schedule.frames import (
    DATED_KINDS,
    build_frames,
    describe_frame,
    describe_season,
    phase_label,
)
from app.services.schedule.norms import (
    running_norm_weekly_m,
    weekly_hours_norm_s,
    weekly_norms_by_discipline,
)
from app.services.schedule.plan_validator import (
    VOLUME_CEILING,
    hours_ceilings,
    validate_drafted_plan,
    volume_ceilings,
)
from app.services.schedule.repair import repair_weeks
from app.services.schedule.run_log import attempt_cost
from app.services.schedule.shapes import write_shapes
from app.services.weeks import resolve_week_start, week_start

logger = logging.getLogger(__name__)

# How much history the drafting context reads. The load model wants ~6 months; the
# volume norm wants its 12-week baseline plus the current week.
_HISTORY_DAYS = 200

# The two blocks of this prompt that state the CONTRACT rather than the coaching.
#
# A session has to be one the app can PLACE and SIZE, and those rules hold whether
# it is being drafted for the first time or rewritten inside an existing plan:
# `plan_validator` applies one gate to both. They are named here so the amendment
# prompt states them too (#996).
#
# Amending inherited that gate without inheriting these instructions, so the model
# wrote sessions a coach would say out loud - "Rest or easy walk", a long run
# windowed Sunday into Monday, a rich `detail` - and the gate rejected each on a
# rule it had never been given. Every rewrite was spent rediscovering one.
PLACING_AND_COMMITTING = """# PLACEMENT

Every session gets a window — the first and last day it may fall on.

- The same day for both pins it to that day. Use this when the day genuinely \
matters (the long run needs a free morning; the runner has a club night).
- A range means it floats: the session must happen, the exact day does not matter.
- The whole week means it can go anywhere.

A window must stay INSIDE one week — it may not run from one week into the next. \
"Saturday or Sunday" is a window; "Sunday or Monday" is not, because those days \
belong to different weeks. Put the session in the week you mean it to count \
towards.

Prefer a window to a pin. A pinned week is rigid and a rigid week is the one that \
gets abandoned after one bad Tuesday. Pin what genuinely needs pinning and let the \
rest float.

A window says WHICH DAYS a session may fall on. It cannot say "the day after \
whatever day that one lands on", because that is not a set of days, it is a \
relationship. Express a relationship as a RULE instead: `rest_day_after` already \
holds the day after a floating long run clear, on whichever day the long run \
actually happens, and it keeps holding it when the runner moves things. Writing \
the same thing again as a rest session forces you to guess the day, and the guess \
that spans Sunday into Monday spans two different weeks and is rejected.

# COMMITMENT

A session is either COMMITTED or a SUGGESTION, and the difference is what the \
runner has signed up to.

- `committed` is the plan. It counts towards their week, and missing it is \
something I respond to. Almost everything I prescribe is committed — that is what \
makes it a plan rather than a menu.
- `suggested` is an offer they can decline with no trace and no follow-up. Use it \
sparingly, for the genuinely optional extra: a bonus mobility session, a spin \
on a day they would otherwise rest.

A week of suggestions is not a plan. If I am not willing to commit to a session, \
I should ask myself whether it belongs in the week at all.

"""

WRITING_A_SESSION = """# WHAT NOT TO DO

- Do not estimate training load, effort scores or TRIMP for a session. Say what the \
session is — discipline, intent, how long or how far — and the app computes what it \
costs from this runner's own history.
- Do not put a distance or duration on a rest day. A rest day is REST. If you \
mean an easy walk or a gentle spin, that is an easy session of that discipline \
(intent `easy`, discipline `walk` or `bike`), not a rest day with a target on it.
- Give every other session the time it will take, `target_duration_s`: that is \
how their whole week adds up, across every activity. A run or a walk also gets \
its distance (or rep structure), because that is how the runner measures it: \
"8 km easy, about 50 minutes", "6 km dog walk, about 70 minutes". Strength, a \
spin, mobility or a class is sized by time alone: "45 minutes". "Strength" on \
its own is not a session anyone can do, and a session with no time, distance or \
rep structure is rejected.
- Write a warm-up and a cool-down as a DISTANCE, never as minutes. \
`warmup_distance_m` and `cooldown_distance_m` are part of the session; the same \
thing said in prose as "10 min easy" is not a distance and counts as nothing.
- A session's distance has to ADD UP. Reps plus their warm-up and cool-down do, \
so leave `target_distance_m` out of a rep session. Nothing else does: give a tempo \
its whole distance in `target_distance_m`, door to door, with the warm-up and \
cool-down as the parts inside it. Anything you do not say in metres counts as \
nothing towards the runner's week.
- Do not plan a week whose running volume is a leap from what this runner \
actually does. Their own recent weeks set a ceiling, stated in kilometres in the \
context below, and a week above it is rejected outright — the same as a week \
whose rules cannot be satisfied. It is a limit, not a target.
- Do not plan sessions in the past.
- Do not invent a race, a goal or an injury the runner has not told you about.
- Do not give medical advice, diagnose, or prescribe treatment. If something in \
their history looks like a medical question, plan conservatively around it and say \
nothing clinical.

"""

_SYSTEM_PROMPT = (
    """You are a running coach writing this runner's training plan: their whole \
week, every activity they train in, with running at its centre.

You are given what you already know about them: their goal, the goals and races \
they have stated, what they have actually been doing, their current condition, what they \
have told you, and the school of training you coach from. Write the plan you would \
write for THIS runner.

# HOW TO PLAN

Coach the runner in front of you, not the median one. Their own recent training is \
the reference for what is normal for them — population defaults are a starting \
point to depart from, not a template to apply. A build that is right for a 60 km/week \
runner can injure a 25 km/week one, and the same is true in reverse: do not prescribe \
a beginner's week to someone who has been training for years.

Plan their whole week. What they do most weeks besides running, the context \
shows by activity, belongs in the plan as committed sessions, sized and placed \
around the running: for a runner who walks six times a week, a plan with one \
walk is a plan for someone else. Keep it easy where it is easy, and keep it off \
the days that need fresh legs.

# THE SEASON AND THE WEEKS

You have already planned this runner's season: your view of every goal, the \
dates you recommended, any challenge rule, and the phases with their targets. \
They are in the context below under THE SEASON, and they are your own decisions, \
so the weeks sit inside them. Under THE WEEKS each week to write is stated plainly: \
its phase and targets, the dated goals it must hold, the challenge threshold it must \
reach, the walking it must keep, and the limits it must stay under. Write real \
sessions for exactly those weeks. Every statement under a week is checked after you \
answer, and a week that misses one is sent back to you with the exact gap.

Where a week is a challenge week, building the hours is the job of that week, race \
week included: easy sessions of the activity that counts most towards it for THIS \
runner (the context gives their shares) are how a race week still holds. Where a \
goal has a day, pin the goal to that day and plan the days around it.

"""
    + PLACING_AND_COMMITTING
    + """# RULES

For a runner whose sessions float, the spacing rules ARE the plan. Say them as \
rules rather than in prose: what needs a rest day after it, what must not fall the \
day before what, how far apart the hard days must be, which days a session needs.

Write rules you actually intend. Every one is enforced — a week that cannot satisfy \
its own rules is rejected and you will be asked to write it again.

One rule set covers the WHOLE block, so it has to hold in the block's unusual \
weeks too, not only its ordinary ones. Race week is where this bites: a race is a \
long session on a fixed day, and a rule set that also demands a weekly long run at \
the weekend and a clear day after every long one leaves that week with no legal \
arrangement. Check the awkward weeks against your own rules before you write them \
down, and let the race week hold the race.

Each rule kind takes specific arguments and is rejected without them. \
`rest_day_after` needs `intent`. `no_intent_day_before` needs BOTH `before_intent` \
and `target_intent`. `min_days_between` needs `intent_a`, `intent_b` and `days`. \
`preferred_days` needs `intent` and `weekdays`. `max_sessions_per_day` needs \
`count`. The tool description carries a worked example of each.

"""
    + WRITING_A_SESSION
    + "Answer only by calling record_training_plan."
)

# #856. Appended when the plan was already settled in conversation. The task then
# is TRANSCRIPTION, not planning: the coaching decisions were made in front of the
# runner and they confirmed those, so a model that re-plans here hands back
# something they never agreed to. Everything the base prompt says about placement,
# commitment, rules and what not to do still binds — the structure has to be
# legal, and the coherence gate is unchanged.
_FROM_CONVERSATION = """

# THIS PLAN IS ALREADY SETTLED

You and this runner worked this block out in conversation, and they have just \
confirmed they want it in their schedule. The transcript is below.

Your job now is to TRANSCRIBE what you both agreed into the tool's structure — \
not to plan it again. Where the conversation named a session, write that session. \
Where it named a week's shape, write the sessions that make that shape. Keep the \
phases, the progression and the race the conversation was built around.

Fill the gaps the conversation left, and only those. A gap is anything it did \
not discuss: which day the Tuesday easy run falls on, and every part of the \
runner's typical week it never mentioned, which stays in the plan as they \
usually do it. What the conversation changed stays as agreed. Use the windows and rules to hold what was \
agreed loosely, loosely, rather than inventing precision the runner never signed \
up to. A loose window still lives inside ONE week: widen it within the week, \
never across the boundary into the next. "The weekend" for a Monday-start runner \
is Saturday to Sunday; Sunday to Monday is two different weeks.

If the conversation covered fewer weeks than THE WEEKS lists, write the \
remainder in the same direction rather than stopping short or changing course. If \
it settled something you would not have chosen, write what was settled: they \
agreed to that plan, not to your second thoughts about it."""


@dataclass
class DraftOutcome:
    ok: bool
    plan_id: Optional[uuid.UUID] = None
    failures: Optional[List[str]] = None
    summary: Optional[str] = None
    # WHY it failed, as one of `store`'s closed categories, decided here where
    # the failure is known rather than re-derived from the prose at the API layer
    # (#859). The runner is owed different advice for a plan that ramps too hard
    # than for a coach that could not be reached, and `failures` is internal text
    # written to be fed back into a rewrite prompt — not something to pattern-match.
    failure_kind: str = store.FAILURE_UNKNOWN


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


def concrete_weeks_for(
    today: date, weeks: int, races: List[Any], *, starts_on: int
) -> int:
    """How many weeks of this plan get real sessions rather than a shape.

    Normally the configured few (`SCHEDULE_CONCRETE_WEEKS`): nobody knows what
    week nine looks like, and pretending to is how a plan stops being believable.

    A race inside the horizon is the exception, and it is not a loosening of that
    rule but the same rule read honestly. The weeks between here and a stated
    race are not weeks nobody can foresee; they are the weeks that decide the
    race, they are already being planned backwards from a fixed date, and the
    runner is going to train every one of them. Leaving them as shape means the
    part of the block the runner cares about most is the part that was never
    written down, which is exactly how a settled 20 km peak long run reached the
    schedule as a weekly total and nothing else (#980).

    Bounded by the contract's own cap either way, so a race five months out does
    not ask for twenty weeks of sessions the coach would be inventing.
    """
    if not races:
        return settings.SCHEDULE_CONCRETE_WEEKS
    horizon_end = week_start(today, starts_on) + timedelta(days=7 * weeks - 1)
    # Only an exact date is planned backwards session by session; an approximate
    # window ("~March") has no fixed day to write the weeks towards (#1042).
    inside = [
        race for race in races
        if race.race_date is not None and race.race_date <= horizon_end
    ]
    if not inside:
        return settings.SCHEDULE_CONCRETE_WEEKS
    furthest = max(race.race_date for race in inside)
    # Inclusive of the race's own week: the runner trains in it and races at the
    # end of it, so it needs sessions like any other.
    span_weeks = (
        (week_start(furthest, starts_on) - week_start(today, starts_on)).days // 7
    ) + 1
    return max(settings.SCHEDULE_CONCRETE_WEEKS, min(span_weeks, MAX_CONCRETE_WEEKS))


def build_draft_context(
    db: Session,
    user: User,
    *,
    today: date,
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
    # From TODAY, not the week start. Asking for the whole current week when it
    # is already Wednesday makes the coach prescribe Monday and Tuesday sessions
    # the runner cannot do — which the validator then rejects as being in the
    # past, so the two instructions contradicted each other and cost a plan.
    parts.append(
        f"PLAN FROM: today, {today.isoformat()}. The current week began "
        f"{first_week.isoformat()} and is already partly gone — plan only the "
        f"days that remain in it, then whole weeks after that. A partial week holds only \
the sessions your own rules let those days carry, so it has fewer than a full \
one. The runner's week "
        f"starts on {'Sunday' if starts_on == 6 else 'Monday'}."
    )
    races = store.list_goal_races(db, user.id, on_or_after=today)

    parts.append("\n## THE RUNNER")
    parts.extend(_profile_lines(user, getattr(user, "profile", None)))

    if races:
        parts.append("\n## THEIR GOALS")
        for race in races:
            suffix = (
                f" (the week beginning {week_start(race.race_date, starts_on).isoformat()})"
                if race.race_date is not None
                else ""
            )
            parts.append(goals.prompt_line(race, today, suffix=suffix))
    else:
        parts.append("\n## THEIR GOALS\nNo goal stated. Plan for general progression.")

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
        # The week by activity, in the units each is trained in (#1044). The
        # all-activity km above hides that a walker's week is mostly walking, so
        # a plan written from it left out the activity they do most.
        by_discipline = weekly_norms_by_discipline(facts, today)
        if by_discipline:
            total_h = sum(n.moving_time_s for n in by_discipline) / 3600
            parts.append(
                f"- Typical week, by activity ({total_h:.1f} h moving in all):"
            )
            for norm in by_discipline:
                if norm.moving_time_s < 360:
                    continue  # under six minutes a week is noise, not a habit
                km = (
                    f", {norm.distance_m / 1000:.1f} km"
                    if norm.distance_m >= 500
                    else ""
                )
                parts.append(
                    f"  - {norm.discipline}: {norm.moving_time_s / 3600:.1f} h over "
                    f"{norm.sessions:.1f} sessions{km}"
                )
            # Said from `hours_ceilings`, the function the gate calls, so the
            # limit stated is the limit enforced (the #859 rule for km below).
            # Rounded DOWN to a tenth: a week under the stated limit must pass.
            concrete_h = math.floor(hours_ceilings(total_h * 3600)[0] / 360) / 10
            parts.append(
                f"- A week above {concrete_h:.1f} h of committed time, every "
                f"activity together, is rejected outright. A limit, not a target."
            )
        # The number that actually bounds a running plan, given explicitly. The
        # all-activity figure above is the one a coach is most likely to misread
        # as running volume — for a runner who walks a lot it is more than double
        # their running — so the running-only norm sits beside it rather than
        # being left to be inferred from a label.
        running_norm = running_norm_weekly_m(facts, today)
        ceilings = volume_ceilings(running_norm)
        if running_norm and ceilings:
            parts.append(
                f"- Typical week, RUNNING ONLY: {running_norm / 1000:.1f} km. This is "
                f"the figure a running plan is built against."
            )
            # The bound the plan will actually be judged against, said out loud
            # (#859). It was enforced silently: every other gate — the rules, the
            # sizing, the rest day, the past — is stated in the prompt, and this
            # one was not, so a block the runner had already settled in
            # conversation could be rejected against a number the coach was never
            # shown. Derived from `volume_ceilings`, the same function the gate
            # calls, so the ceiling said here IS the ceiling enforced there.
            #
            # Framed as a rejection threshold rather than a goal because that is
            # what it is, and a bare "38 km" beside a typical 19 reads to a model
            # as the number to aim at — the North Star's second question.
            parts.append(
                f"- A week above {ceilings[0] / 1000:.0f} km of committed "
                f"running is rejected outright as a jump this runner's history "
                f"cannot support. That is a limit, not a target — most weeks "
                f"should sit near their typical, and a build climbs towards the "
                f"limit rather than starting at it."
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


# How much of the settling conversation the draft reads. Bounded like every other
# transcript this project puts in front of a model, but wider than the continuity
# digests: those carry a flavour of what was discussed, this carries the plan
# itself, and truncating the message that holds the block would silently drop the
# thing the runner confirmed.
_MAX_TRANSCRIPT_TURNS = 16
_MAX_TRANSCRIPT_CHARS = 2500


def _conversation_block(db: Session, user: User, thread_id: Optional[str]) -> str:
    """The settling conversation, owner-scoped, or "" when there is none (#856).

    Owner-scoped for the usual reason and one specific to this path: the thread id
    arrives as a job argument rather than from an authenticated request, so it is
    re-checked against the plan's owner here rather than trusted.
    """
    if not thread_id:
        return ""
    try:
        from app.models.coach_chat_message import CoachChatMessage
        from app.models.thread import Thread

        thread = (
            db.query(Thread)
            .filter(Thread.id == uuid.UUID(str(thread_id)), Thread.user_id == user.id)
            .first()
        )
        if thread is None:
            logger.warning(
                "schedule draft: thread %s is not this runner's; drafting unseeded",
                thread_id,
            )
            return ""
        from app.services.coach.threads import CONVERSATIONAL_ROLES

        rows = (
            db.query(CoachChatMessage)
            .filter(
                CoachChatMessage.thread_id == thread.id,
                # This block is the conversation that settled the plan, and each
                # line is attributed to Runner or You. An action event (#778) is
                # neither, so it is read out of the transcript rather than
                # attributed to whichever side the else-branch happens to name.
                CoachChatMessage.role.in_(CONVERSATIONAL_ROLES),
            )
            .order_by(
                CoachChatMessage.created_at.desc(), CoachChatMessage.id.desc()
            )
            .limit(_MAX_TRANSCRIPT_TURNS)
            .all()
        )
    except Exception:  # noqa: BLE001 — an unreadable transcript is not a failed plan
        logger.exception("schedule draft: could not read thread %s", thread_id)
        return ""

    lines: List[str] = []
    for row in reversed(rows):  # chronological
        text = " ".join((row.content or "").split())
        if not text:
            continue
        if len(text) > _MAX_TRANSCRIPT_CHARS:
            text = text[:_MAX_TRANSCRIPT_CHARS].rstrip() + "…"
        lines.append(f"{'Runner' if row.role == 'user' else 'You'}: {text}")
    if not lines:
        return ""
    return "\n\n## THE CONVERSATION\n\n" + "\n\n".join(lines)


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


def _draft_max_tokens(weeks: int) -> int:
    """Output room for N concrete weeks plus the model's thinking: about 2500 a
    week and 1500 over, clamped. Undersizing is not a soft failure, since a stop
    on `max_tokens` raises by design rather than returning half a plan."""
    return max(6000, min(20000, 2500 * weeks + 1500))


def _season_for(db: Session, user: User, season: Any) -> tuple:
    """(season row or None, its coerced plan or None). `season` may be the row, a
    coerced plan (tests), or None to read the runner's active season."""
    if isinstance(season, SeasonPlan):
        return None, season
    row = season if season is not None else season_store.active_season(db, user.id)
    if row is None:
        return None, None
    return row, season_store.season_plan(row)


def _dated_stand_ins(season_plan: Optional[SeasonPlan], races: List[Any]) -> List[Any]:
    """Goal-like objects carrying the date a week must hold, for
    `concrete_weeks_for`: the season's recommended dates when there is a season (a
    day the coach chose is a day the plan holds), else the runner's stated ones."""
    if season_plan is None:
        return races
    return [
        SimpleNamespace(race_date=view.date)
        for view in season_plan.goals
        if view.kind in DATED_KINDS and view.date is not None
    ]


def _retry_message(context: str, failures: List[str], previous: Optional[dict]) -> str:
    out = [context, "\n## YOUR PREVIOUS ATTEMPT WAS REJECTED"]
    out.extend(f"- {failure}" for failure in failures)
    if previous is not None:
        out.append(
            "\nThe attempt, for reference:\n"
            + json.dumps(previous, separators=(",", ":"), default=str)
        )
    out.append("\nWrite the plan again, fixing every one of these and keeping what was sound.")
    return "\n".join(out)


def _repair(drafted: DraftedPlan, check, frames_by_week: dict) -> tuple:
    """Close the numeric shortfalls the check found, week by week."""
    weeks, notes = repair_weeks(
        drafted.weeks, check, frames_by_week, rules=drafted.rules
    )
    return drafted.model_copy(update={"weeks": weeks}), notes


def _first_try_passed(check, expected: List[date]) -> int:
    if check.plan_level_failure:
        return 0
    return len([week for week in expected if week not in check.failed_weeks])


async def draft_plan(
    db: Session,
    user: User,
    plan: TrainingPlan,
    *,
    today: Optional[date] = None,
    thread_id: Optional[str] = None,
    season: Any = None,
    client: Any = None,
) -> DraftOutcome:
    """Generate, validate, repair and store one plan. One retry, then fail visibly.

    The weeks are written INSIDE the runner's active season (`season`, or the
    active one): the model writes real sessions for the weeks it is given a frame
    for, code writes every later week as a shape, and one check holds each written
    week to every goal at once. A numeric shortfall (the challenge, the usual
    walking) that survives the retry is closed by `repair` where the ceilings
    allow and otherwise stored as a shortfall and shown; any other failure fails
    the draft as it always has.

    `thread_id` (#856) names a conversation in which the runner and the coach
    already settled this block. It changes the TASK, transcribe what was agreed
    rather than plan afresh, and nothing else: the same forced tool, the same
    coercion, the same coherence gate, the same all-or-nothing store.
    """
    today = today or date.today()
    weeks = settings.SCHEDULE_HORIZON_WEEKS
    starts_on = resolve_week_start(getattr(user, "profile", None))

    if turn.over_budget(user.id):
        return DraftOutcome(
            ok=False,
            failures=["over the spend cap for this period"],
            failure_kind=store.FAILURE_OVER_BUDGET,
        )

    facts = fetch_draft_facts(db, user, today)
    season_row, season_plan = _season_for(db, user, season)
    races = store.list_goal_races(db, user.id, on_or_after=today)
    all_goals = store.list_goal_races(db, user.id)
    frames = build_frames(
        season=season_plan, goals=all_goals, facts=facts,
        starts_on=starts_on, today=today, horizon_weeks=weeks,
    )
    concrete = min(
        concrete_weeks_for(
            today, weeks, _dated_stand_ins(season_plan, races), starts_on=starts_on
        ),
        len(frames),
    )
    concrete_frames = frames[:concrete]
    expected = [frame.week_start for frame in concrete_frames]
    frames_by_week = {frame.week_start: frame for frame in concrete_frames}

    context = build_draft_context(db, user, today=today, facts=facts)
    if season_plan is not None:
        context += "\n\n## THE SEASON\n" + "\n".join(describe_season(season_plan, all_goals))
    context += (
        "\n\n## THE WEEKS\nWrite real sessions for exactly these weeks, one entry "
        "each, in order. What is stated under a week is what that week is checked "
        f"against. A week holds at most {MAX_SESSIONS_PER_WEEK} sessions, so reach "
        "a threshold with longer sessions rather than more of them.\n\n"
        + "\n\n".join("\n".join(describe_frame(frame)) for frame in concrete_frames)
    )
    system = _SYSTEM_PROMPT
    transcript = _conversation_block(db, user, thread_id)
    if transcript:
        system = _SYSTEM_PROMPT + _FROM_CONVERSATION
        context = context + transcript
    client = client or turn.build_client(turn.TurnKind.SCHEDULE, user.id)

    load_model = build_load_model(facts, today)
    norm_running = running_norm_weekly_m(facts, today)
    # The goal race, for the volume ceiling only. A race is the runner's own
    # fixed commitment, not a training decision the gate has a view on.
    race_arg = goals.validator_race(races)
    norm_hours = weekly_hours_norm_s(facts, today)

    log = DraftLog(model=client.model, checked=len(expected))
    max_tokens = _draft_max_tokens(len(expected))

    # Two budgets, deliberately separate. A transport blip is not the coach's
    # fault, so it must not consume the one chance to REWRITE a rejected plan —
    # sharing them meant a 429 on the first call left a genuinely fixable plan
    # with no attempt left. Transport errors also never reach the prompt: feeding
    # a provider's exception text back would put its internals into model input
    # to no purpose, since the coach cannot act on them.
    failures: List[str] = []
    previous: Optional[dict] = None
    failure_kind = store.FAILURE_UNKNOWN
    rewrites_left = 2
    transport_retries_left = 1

    def validate(candidate: DraftedPlan):
        return validate_drafted_plan(
            candidate,
            today=today,
            starts_on=starts_on,
            norm_weekly_running_m=norm_running,
            horizon_weeks=weeks,
            race=race_arg,
            norm_weekly_s=norm_hours,
            frames=concrete_frames,
            expected_weeks=expected,
        )

    def kind_of(check) -> str:
        # Structural, not a substring match on the gate's prose: the check
        # reports WHICH kind of rejection fired, so improving the wording of a
        # failure can never silently change what the runner is told.
        return (
            store.FAILURE_TOO_BIG_A_JUMP
            if VOLUME_CEILING in check.codes
            else store.FAILURE_UNKNOWN
        )

    while rewrites_left > 0:
        user_message = context if not failures else _retry_message(context, failures, previous)
        try:
            raw, usage = await client.generate_structured_reasoned(
                system=system,
                user=user_message,
                tool=RECORD_TRAINING_PLAN_TOOL,
                max_tokens=max_tokens,
                effort="high",
                web_search_max_uses=0,
            )
        except (TypeError, AttributeError):
            # A bad kwarg or a missing method is a bug in this code, not a network
            # that misbehaved; the catch below would report it to the runner as
            # "could not be reached" after retries that reached nothing.
            raise
        except Exception as exc:  # transport, timeout, refusal
            logger.warning("schedule draft: generation call failed: %s", exc)
            if transport_retries_left > 0:
                transport_retries_left -= 1
                continue
            return DraftOutcome(
                ok=False,
                failures=["the coach could not be reached"],
                failure_kind=store.FAILURE_UNREACHABLE,
            )

        rewrites_left -= 1
        attempt = attempt_cost(client.model, usage)
        log.attempts.append(attempt)
        first_attempt = len(log.attempts) == 1

        try:
            drafted = DraftedPlan.model_validate(normalise(raw))
        except Exception as exc:
            logger.warning("schedule draft: off-contract plan: %s", exc)
            failures = [f"the plan was not the shape the tool requires: {exc}"]
            attempt.failures = list(failures)
            previous = None
            failure_kind = store.FAILURE_UNKNOWN
            continue

        check = validate(drafted)
        if first_attempt:
            log.first_try_passed = _first_try_passed(check, expected)
        if check.ok:
            _persist(
                db, user, plan, drafted, load_model, model_id=client.model,
                season_row=season_row, frames=frames, concrete=set(expected), log=log,
            )
            return DraftOutcome(ok=True, plan_id=plan.id, summary=drafted.summary)

        logger.info("schedule draft: rejected: %s", check.failures)
        attempt.failures = list(check.failures)

        if rewrites_left == 0 and check.only_numeric:
            # The retry is spent and the plan is coherent, just short of a number.
            # Close what the ceilings allow in code, then store what is left as a
            # shortfall rather than failing a plan whose only fault is arithmetic.
            repaired, notes = _repair(drafted, check, frames_by_week)
            final = validate(repaired)
            if final.ok or final.only_numeric:
                log.repairs = notes
                log.shortfalls = [f.shortfall for f in final.week_failures if f.shortfall]
                logger.warning(
                    "schedule draft: stored with %d repair(s), %d shortfall(s)",
                    len(notes), len(log.shortfalls),
                )
                _persist(
                    db, user, plan, repaired, load_model, model_id=client.model,
                    season_row=season_row, frames=frames, concrete=set(expected), log=log,
                )
                return DraftOutcome(ok=True, plan_id=plan.id, summary=repaired.summary)
            check = final
            attempt.failures = list(final.failures)

        failures = check.failures
        previous = drafted.model_dump(mode="json")
        failure_kind = kind_of(check)

    return DraftOutcome(ok=False, failures=failures, failure_kind=failure_kind)


def _persist(
    db: Session,
    user: User,
    plan: TrainingPlan,
    drafted: DraftedPlan,
    load_model,
    *,
    model_id: str,
    season_row: Any = None,
    frames: Sequence[Any] = (),
    concrete: Optional[set] = None,
    log: Optional[DraftLog] = None,
) -> None:
    """Write the accepted plan and make it the runner's active one.

    The concrete weeks are the model's sessions (repaired where needed); every
    later week is a shape written by code from the season. A concrete week's phase
    is the frame's, so the horizon groups the two halves under one name.
    """
    log = log or DraftLog(model=model_id)
    frames_by_week = {frame.week_start: frame for frame in frames}
    concrete = concrete if concrete is not None else {w.week_start for w in drafted.weeks}
    weeks = [
        week.model_copy(
            update={
                "phase": (
                    phase_label(frames_by_week[week.week_start].phase)
                    if week.week_start in frames_by_week
                    else None
                )
                or week.phase
            }
        )
        for week in drafted.weeks
    ]
    shapes, shape_shortfalls = write_shapes(frames, load_model, concrete)
    log.shortfalls = list(log.shortfalls) + shape_shortfalls

    plan.rules = [rule.model_dump(mode="json") for rule in drafted.rules]
    plan.week_shapes = shapes + store.concrete_week_phases(weeks)
    reach = [w.week_start for w in weeks] + [s["week_start"] for s in shapes]
    reach = [d if isinstance(d, date) else date.fromisoformat(d) for d in reach]
    plan.horizon_end = max(reach) + timedelta(days=6) if reach else None
    # The model the draft ACTUALLY ran on, taken from the client rather than
    # re-read from config — the provenance gap `service._persist_report` still
    # carries and that this path has no reason to inherit.
    plan.model_id = model_id
    plan.season_id = season_row.id if season_row is not None else None
    plan.draft_log = log.model_dump(mode="json")

    for week in weeks:
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


def enqueue_draft(user_id, plan_id, thread_id=None, description=None) -> None:
    """Enqueue the drafting job, decoupled from the request.

    Imported lazily and swallowing enqueue errors, the `enqueue_distillation`
    idiom: the queue dependency stays off the read endpoints' import path, and a
    Redis hiccup leaves a `drafting` row the runner can retry rather than a 500
    on a request that has already written one.

    `thread_id` (#856) is the conversation that settled the plan, when there was
    one; the Schedule screen's own button passes none.
    """
    try:
        from app.core.queue import queue
        from app.jobs.generate_schedule import generate_schedule_job

        queue.enqueue(
            generate_schedule_job,
            str(user_id),
            str(plan_id),
            str(thread_id) if thread_id else None,
            description or None,
            # One job may write the season and then the weeks, so it gets its own
            # ceiling; the stale-draft rule is derived from the same setting.
            job_timeout=settings.SCHEDULE_JOB_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — enqueue is fire-and-forget
        logger.exception("failed to enqueue schedule draft for plan %s", plan_id)
