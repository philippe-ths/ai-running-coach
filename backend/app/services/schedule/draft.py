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
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.schemas.season import DraftLog, SeasonPlan
from app.services.coach import turn
from app.services.coach.llm import ReasonedCallFailed
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
from app.services.schedule.effort import build_load_model
from app.services.schedule.frames import (
    DATED_KINDS,
    build_frames,
    describe_frame,
    describe_season,
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
from app.services.schedule.runner_rules import runner_rules
from app.services.schedule.run_log import attempt_cost, output_failure
from app.services.weeks import resolve_week_start, week_start

# Re-imported so every name other modules and tests import from here still resolves (#1100).
from app.services.schedule.draft_facts import (
    _conversation_block,
    _memory_lines,
    _profile_lines,
    fetch_draft_facts,
)
from app.services.schedule.draft_persist import _persist
from app.services.schedule.draft_prompt import (
    _FROM_CONVERSATION,
    _SYSTEM_PROMPT,
    PLACING_AND_COMMITTING,
    WRITING_A_SESSION,
)

logger = logging.getLogger(__name__)


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


def _repair(
    drafted: DraftedPlan, check, frames_by_week: dict, own_rules: Sequence[Any] = ()
) -> tuple:
    """Close the numeric shortfalls the check found, week by week."""
    weeks, notes = repair_weeks(
        drafted.weeks, check, frames_by_week, rules=list(drafted.rules) + list(own_rules)
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
    # The runner's own rules (#1080) are not the coach's to write, so they are
    # held over the draft here rather than appearing in `drafted.rules`.
    own_rules = runner_rules(getattr(user, "profile", None))

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
            runner_rules=own_rules,
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
        except ReasonedCallFailed as exc:
            # The call ran and was billed but gave no usable answer (cut off, or
            # no tool call). That is the coach's OUTPUT failing, not the network,
            # so it spends a rewrite, is logged with its cost, and tells the coach
            # what went wrong instead of reporting it as unreachable.
            logger.warning("schedule draft: unusable answer: %s", exc)
            rewrites_left -= 1
            attempt = attempt_cost(client.model, exc.usage)
            log.attempts.append(attempt)
            failures = [output_failure(exc, RECORD_TRAINING_PLAN_TOOL["name"])]
            attempt.failures = list(failures)
            previous = None
            failure_kind = store.FAILURE_UNKNOWN
            continue
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
                usual_week=season_plan is None,
            )
            return DraftOutcome(ok=True, plan_id=plan.id, summary=drafted.summary)

        logger.info("schedule draft: rejected: %s", check.failures)
        attempt.failures = list(check.failures)

        if rewrites_left == 0 and check.only_numeric:
            # The retry is spent and the plan is coherent, just short of a number.
            # Close what the ceilings allow in code, then store what is left as a
            # shortfall rather than failing a plan whose only fault is arithmetic.
            repaired, notes = _repair(drafted, check, frames_by_week, own_rules)
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
                    usual_week=season_plan is None,
                )
                return DraftOutcome(ok=True, plan_id=plan.id, summary=repaired.summary)
            check = final
            attempt.failures = list(final.failures)

        failures = check.failures
        previous = drafted.model_dump(mode="json")
        failure_kind = kind_of(check)

    return DraftOutcome(ok=False, failures=failures, failure_kind=failure_kind)


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
