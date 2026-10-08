"""The coach plans the season (#1064).

One model call with extended thinking and web search reads every goal the runner
wrote and returns an OPINION: what each goal is, when to do it, which real events
to enter, how a challenge is expressed as a rule, and the phase timeline. Code
then checks it (`season_check`) and stores it only if it holds.

The division is the point. The coach owns opinion; code owns arithmetic and
constraints. So the context carries what the runner does now and how fast a ramp
could reach a level as FACTS (`challenge`), the checks hold only absurdity
ceilings and the contradictions a plan cannot survive, and a season that fails
them is never activated: one retry with every failure listed, then a failed row
and nothing changed.

Everything the model returns is untrusted input in the way a web page is: it is
strict-coerced through `SeasonPlan` before it can reach a prompt or a screen
(ADR 0017's containment spine), and an event's name and link are single-line.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, List, Optional, Sequence, Union

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.season import Season
from app.models.user import User
from app.schemas.season import DraftAttempt, DraftLog, SeasonPlan
from app.services.coach import budget, turn
from app.services.coach.retrieval import fetch_corpus
from app.services.coach.stance import resolve_stance
from app.services.readiness import build_readiness
from app.services.schedule import challenge, goals as goal_text
from app.services.schedule import season_store, store
from app.services.schedule.draft import (
    _conversation_block,
    _memory_lines,
    _profile_lines,
    fetch_draft_facts,
)
from app.services.schedule.norms import (
    weekly_actuals,
    weekly_norms_by_discipline,
    zone_shares,
)
from app.services.schedule.plan_validator import (
    MAX_WEEKLY_MULTIPLE,
)
from app.services.schedule.season_check import check_season
from app.services.schedule.season_prompt import (
    FROM_CONVERSATION,
    RECORD_SEASON_TOOL,
    SYSTEM_PROMPT,
)
from app.services.weeks import resolve_week_start, week_start

logger = logging.getLogger(__name__)

__all__ = ["RECORD_SEASON_TOOL", "SeasonOutcome", "build_season_context", "generate_season"]

# Room for the thinking, up to 20 goals' views and 40 phases.
MAX_TOKENS = 24000
WEB_SEARCH_MAX_USES = 5

# The goal-agnostic metrics a coach might reasonably build a challenge on. The
# levels are shown for each so the coach can weigh them whichever it picks.
_CANDIDATES = (
    ("Time at zone 2 or above, any activity", challenge.MetricSpec("zone_time_s", (), 2)),
    ("Total moving time, any activity", challenge.MetricSpec("time_s")),
    ("Running distance", challenge.MetricSpec("distance_m", ("run",))),
    ("Walking distance", challenge.MetricSpec("distance_m", ("walk",))),
    ("Sessions of any kind", challenge.MetricSpec("sessions")),
)
# Round figures a runner might name, per metric; the first few above the current
# level are shown. Hours for the time metrics, kilometres for distance.
_LADDERS = {
    "zone_time_s": [2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20],
    "time_s": [2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20],
    "distance_m": [5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100],
    "sessions": list(range(2, 21)),
}


@dataclass
class SeasonOutcome:
    ok: bool
    season_id: Optional[uuid.UUID] = None
    failures: List[str] = field(default_factory=list)
    message: Optional[str] = None


def _line(text: Any) -> str:
    return " ".join(str(text).split())


def _day(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def _amount(metric: str, value: float) -> str:
    if metric in ("zone_time_s", "time_s"):
        return f"{value / 3600:.1f} h"
    if metric == "distance_m":
        return f"{value / 1000:.1f} km"
    return f"{value:.1f}"


def _ladder_target(metric: str, figure: float) -> float:
    """A round figure in the metric's own unit."""
    if metric in ("zone_time_s", "time_s"):
        return figure * 3600.0
    if metric == "distance_m":
        return figure * 1000.0
    return float(figure)


def _goal_lines(race: Any, today: date, starts_on: int) -> List[str]:
    when = goal_text.when_text(race, today)
    if race.race_date is not None and not race.booked:
        when += ": a date the runner gave but has not booked"
    lines = [
        f"- id: {race.id}",
        f'  name: "{_line(race.name)}"',
        f"  priority: {race.priority} (the runner's own ranking, never a claim about ability)",
        f"  when: {when}",
        f"  booked: {'yes, the date is fixed' if race.booked else 'no'}",
    ]
    if race.distance_m:
        lines.append(f"  distance: {race.distance_m / 1000:g} km")
    if race.target_time_s:
        lines.append(f"  target time: {goal_text.format_duration(race.target_time_s)}")
    if race.notes:
        lines.append(f'  in their words: "{_line(race.notes)}"')
    return lines


def current_levels_text(
    facts: Sequence[Any], today: date, starts_on: int
) -> List[str]:
    """What the runner does now, per candidate metric, and where a rise of
    `CHALLENGE_RAMP` a week would take them. Facts to weigh, never a limit."""
    lines = [
        f"- A weekly rise of {challenge.CHALLENGE_RAMP:.0%} is shown below as a reference "
        "for how fast a level can be reached from here. The pace is your call."
    ]
    for label, spec in _CANDIDATES:
        level = challenge.current_level(spec, facts, today, starts_on)
        if spec.metric == "sessions":
            now = f"{level:.1f} a week now"
        else:
            now = f"{_amount(spec.metric, level)} a week now"
        steps = []
        shown = 0
        for figure in _LADDERS[spec.metric]:
            target = _ladder_target(spec.metric, figure)
            if target <= level:
                continue
            start = challenge.earliest_start(spec, level, today, starts_on, at_least=target)
            unit = {"zone_time_s": "h", "time_s": "h", "distance_m": "km"}.get(spec.metric, "")
            steps.append(f"{figure:g}{(' ' + unit) if unit else ''} from the week of {_day(start)}")
            shown += 1
            if shown == 4:
                break
        tail = ""
        if steps:
            tail = f". At a {challenge.CHALLENGE_RAMP:.0%} weekly rise: " + ", ".join(steps)
        lines.append(f"- {label}: {now}{tail}")
    lines.append(
        f"- The code refuses a challenge that starts within {settings.SCHEDULE_CONCRETE_WEEKS} weeks "
        f"and asks for more than {MAX_WEEKLY_MULTIPLE:g}x the current level. A later start is "
        f"your judgment: the weeks before it are yours to build."
    )
    return lines


def build_season_context(
    db: Session,
    user: User,
    *,
    today: date,
    goals: Sequence[Any],
    facts: Optional[List[Any]] = None,
) -> str:
    """What a coach needs to plan this runner's season, and nothing it does not.

    Every upcoming goal appears as the runner wrote it, with its id (the answer
    is keyed by it). The numbers are the runner's own, from their stream.
    """
    starts_on = resolve_week_start(getattr(user, "profile", None))
    if facts is None:
        facts = fetch_draft_facts(db, user, today)
    this_week = week_start(today, starts_on)
    boundary = "Sunday" if starts_on == 6 else "Monday"

    parts: List[str] = [
        f"TODAY: {today.isoformat()} ({today:%A})",
        f"The runner's weeks begin on {boundary}. The current week began "
        f"{this_week.isoformat()}. Challenge starts and every date you give are plain "
        "calendar dates; a challenge must start on a week's first day.",
        "\n## THE RUNNER",
        *_profile_lines(user, getattr(user, "profile", None)),
        "\n## THEIR GOALS (every one needs a view, by id)",
    ]
    for race in goals:
        parts.extend(_goal_lines(race, today, starts_on))

    parts.append("\n## THEIR USUAL WEEK (last 12 weeks, every activity)")
    by_discipline = weekly_norms_by_discipline(facts, today)
    shares = zone_shares(facts, today, 2)
    if by_discipline:
        total_h = sum(n.moving_time_s for n in by_discipline) / 3600
        parts.append(f"- {total_h:.1f} h moving in all")
        for norm in by_discipline:
            if norm.moving_time_s < 360:
                continue
            km = f", {norm.distance_m / 1000:.1f} km" if norm.distance_m >= 500 else ""
            share = (
                f", {shares[norm.discipline]:.0%} of it at zone 2 or above"
                if norm.discipline in shares
                else ""
            )
            parts.append(
                f"- {norm.discipline}: {norm.moving_time_s / 3600:.1f} h over "
                f"{norm.sessions:.1f} sessions{km}{share}"
            )
        parts.append(
            "- Zone shares come from this runner's own heart-rate data; an activity "
            "with too little of it counts as no zone time, and so does any activity "
            "it is missing for."
        )
    else:
        parts.append("- Not enough history to say what is usual. Plan conservatively and say so.")

    parts.append("\n## WHAT THEY DO NOW, AND WHAT A RISE COULD REACH")
    parts.extend(current_levels_text(facts, today, starts_on))

    parts.append("\n## THE LAST FOUR COMPLETE WEEKS")
    for n in range(4, 0, -1):
        start = this_week - timedelta(weeks=n)
        actual = weekly_actuals(facts, start, starts_on)
        parts.append(
            f"- Week of {_day(start)}: {actual.total_time_s / 3600:.1f} h, "
            f"{actual.zone_time_s(2) / 3600:.1f} h at zone 2 or above (measured), "
            f"run {actual.distance_m.get('run', 0) / 1000:.1f} km, "
            f"walk {actual.distance_m.get('walk', 0) / 1000:.1f} km, "
            f"{actual.sessions} sessions"
        )

    readiness = build_readiness(db, user.id, today)
    if readiness is not None:
        parts.append("\n## THEIR CURRENT CONDITION")
        parts.append(
            f"- Fitness {readiness.fitness:.0f}, fatigue {readiness.fatigue:.0f}, "
            f"form {readiness.form:.0f} ({readiness.condition}). Load balances, not "
            "intensity verdicts and not a diagnosis."
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
        if getattr(school, "stance", None):
            parts.append(f"- {school.stance}")
        for principle in (getattr(school, "principles", None) or [])[:5]:
            parts.append(f"- {principle}")

    return "\n".join(parts)


def _clean(payload: Any) -> Any:
    """Drop the nulls and empty dates a model emits for an absent optional.

    An absent optional is the contract; a null or "" in its place is the model's
    way of saying so, and failing a whole season over it would cost the one retry.
    """
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            value = _clean(value)
            if value is None:
                continue
            if value == "" and key in {"date", "window_start", "window_end", "goal_id"}:
                continue
            out[key] = value
        return out
    if isinstance(payload, list):
        return [_clean(item) for item in payload]
    return payload


def _attempt_cost(model: str, usage: Any) -> DraftAttempt:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    searches = int(getattr(usage, "web_search_requests", 0) or 0)
    return DraftAttempt(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(
            budget.cost_usd(
                model, input_tokens, output_tokens, web_search_requests=searches
            ),
            4,
        ),
    )


def _retry_message(context: str, failures: List[str], previous: Optional[dict]) -> str:
    out = [context, "\n## YOUR PREVIOUS ATTEMPT WAS REJECTED"]
    out.extend(f"- {failure}" for failure in failures)
    if previous is not None:
        out.append("\nThe attempt, for reference:\n" + json.dumps(previous, separators=(",", ":")))
    out.append("\nWrite the season again, fixing every one of these and keeping what was sound.")
    return "\n".join(out)


async def generate_season(
    db: Session,
    user: User,
    season: Union[Season, uuid.UUID],
    *,
    thread_id: Optional[str] = None,
    today: Optional[date] = None,
    client: Optional[Any] = None,
) -> SeasonOutcome:
    """Generate, check and store one season. One retry, then fail visibly.

    `season` is the drafting row or its id. `client` is a seam for tests;
    production builds the metered SCHEDULE client.
    """
    if not isinstance(season, Season):
        season = db.get(Season, season)
    today = today or date.today()
    starts_on = resolve_week_start(getattr(user, "profile", None))

    if turn.over_budget(user.id):
        return _fail(db, season, season_store.OVER_BUDGET_MESSAGE, ["over the spend cap"])

    upcoming = store.list_goal_races(db, user.id, on_or_after=today)
    if not upcoming:
        return _fail(db, season, season_store.NO_GOALS_MESSAGE, ["no goals"])

    facts = fetch_draft_facts(db, user, today)
    context = build_season_context(db, user, today=today, goals=upcoming, facts=facts)
    system = SYSTEM_PROMPT
    transcript = _conversation_block(db, user, thread_id)
    if transcript:
        system += FROM_CONVERSATION
        context += transcript
    client = client or turn.build_client(turn.TurnKind.SCHEDULE, user.id)

    def level_for(rule) -> float:
        return challenge.current_level(rule, facts, today, starts_on)

    log = DraftLog(model=client.model, checked=1)
    failures: List[str] = []
    previous: Optional[dict] = None
    rewrites_left = 2
    transport_retries_left = 1

    while rewrites_left > 0:
        user_message = context if not failures else _retry_message(context, failures, previous)
        try:
            raw, usage = await client.generate_structured_reasoned(
                system=system,
                user=user_message,
                tool=RECORD_SEASON_TOOL,
                max_tokens=MAX_TOKENS,
                effort="high",
                web_search_max_uses=WEB_SEARCH_MAX_USES,
            )
        except Exception as exc:  # noqa: BLE001 - transport, timeout, refusal
            logger.warning("season: generation call failed: %s", exc)
            if transport_retries_left > 0:
                transport_retries_left -= 1
                continue
            return _fail(
                db, season, season_store.UNREACHABLE_MESSAGE,
                ["the coach could not be reached"], log=log,
            )

        rewrites_left -= 1
        attempt = _attempt_cost(client.model, usage)
        log.attempts.append(attempt)

        try:
            plan = SeasonPlan.model_validate(_clean(raw))
        except Exception as exc:  # noqa: BLE001 - off-contract is a failure to retry
            logger.warning("season: off-contract answer: %s", exc)
            failures = [f"the season was not the shape the tool requires: {exc}"]
            attempt.failures = list(failures)
            previous = None
            continue

        found = check_season(plan, upcoming, today, starts_on, level_for)
        if found:
            logger.info("season: rejected: %s", found)
            failures = found
            attempt.failures = list(found)
            previous = plan.model_dump(mode="json")
            continue

        log.first_try_passed = 1 if len(log.attempts) == 1 else 0
        season.plan = plan.model_dump(mode="json")
        season.goals_fingerprint = season_store.goals_fingerprint(upcoming)
        season.model_id = client.model
        season.draft_log = log.model_dump(mode="json")
        season_store.activate_season(db, season)
        return SeasonOutcome(ok=True, season_id=season.id)

    return _fail(db, season, season_store.FAILURE_MESSAGE, failures, log=log)


def _fail(
    db: Session,
    season: Season,
    message: str,
    failures: List[str],
    *,
    log: Optional[DraftLog] = None,
) -> SeasonOutcome:
    if log is not None:
        season.draft_log = log.model_dump(mode="json")
    season_store.fail_season(db, season, message)
    return SeasonOutcome(ok=False, season_id=season.id, failures=failures, message=message)


def enqueue_season(user_id, season_id, thread_id=None) -> None:
    """Enqueue the season job, decoupled from the request.

    Imported lazily and swallowing enqueue errors (the `enqueue_draft` idiom): a
    Redis hiccup leaves a `drafting` row the staleness rule will expire rather
    than a 500 on a request that has already written one.
    """
    try:
        from app.core.queue import queue
        from app.jobs.generate_season import generate_season_job

        queue.enqueue(
            generate_season_job,
            str(user_id),
            str(season_id),
            str(thread_id) if thread_id else None,
            # Explicit, so the staleness rule's bound (this plus a margin) is the
            # job's real ceiling and not whatever the queue default drifts to.
            job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - enqueue is fire-and-forget
        logger.exception("failed to enqueue season %s", season_id)
