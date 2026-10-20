"""The chat tools that read the training plan and personal bests (#973, #1068).

Split out of `query_tools.py` (#1102); `query_tools` re-imports both.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.services.schedule import goals


def get_training_plan(db: Session, owner_user_id, *, today: Optional[date] = None) -> dict:
    """The runner's whole training block, week by week (#973).

    The coach receives this week of the plan in its baseline and nothing beyond,
    which is right for most turns and wrong for the one question a runner with a
    race asks most: what happens between here and the race. Asked that, the coach
    reached for training history, because no tool it held could return a plan, and
    then answered from the only thing in front of it. A live conversation produced
    "when you step up to the 16.5 km run on Aug 31" about a week the plan had
    written no sessions for at all.

    Built from `build_horizon`, the SAME builder behind the runner's own Schedule
    screen, so what the runner reads and what the coach reads are one answer
    rather than two that agree until they do not. It is a tool rather than more
    baseline because the block is irrelevant to most turns and the baseline is
    paid for on every one.

    Every week says which of two things it is. A `planned` week holds real
    sessions the runner has agreed to; a `sketched` week is shape, and shape is
    not a promise. Handing both over unlabelled is the same failure one level up:
    the coach would name a distance and a day for a week nobody has written, and
    it would sound exactly as confident as the truth.
    """
    from app.models.user import User
    from app.services.schedule.horizon import build_horizon

    today = today or date.today()
    user = db.query(User).filter(User.id == owner_user_id).first()
    if user is None:
        return {"error": "not_found"}

    horizon = build_horizon(db, user, today=today)
    if not horizon.has_plan:
        # A real answer, not an error: free mode is a destination. Saying it
        # plainly is what stops the coach reading an empty result as a fetch
        # failure and telling the runner to check another app.
        return {
            "has_plan": False,
            "week_count": 0,
            "note": (
                "This runner has no active training plan. They train without one, "
                "which is a supported way to use the app, and you can offer to "
                "write them one if the conversation reaches it."
            ),
        }

    weeks = []
    for week in horizon.weeks:
        if week.coverage == "beyond_plan":
            # Past the plan's own reach. Listing it would invite the coach to
            # describe a week the plan never claimed.
            continue
        entry: Dict[str, Any] = {
            "week_start": week.week_start.isoformat(),
            "is_this_week": week.is_current,
            "written": week.coverage == "planned",
            "phase": week.phase,
        }
        # `is not None`, not truthiness. A WRITTEN week holding no running is a
        # real answer (a cross-training week), and dropping the key entirely
        # would read as "unknown" rather than "none" to the one reader that
        # cannot ask a follow-up question.
        if week.running_distance_m is not None:
            entry["running_km"] = round(week.running_distance_m / 1000, 1)
        if week.walking_distance_m is not None:
            entry["walking_km"] = round(week.walking_distance_m / 1000, 1)
        if week.duration_s is not None:
            entry["hours_all_activities"] = round(week.duration_s / 3600, 1)
        if week.long_run_distance_m is not None:
            entry["long_run_km"] = round(week.long_run_distance_m / 1000, 1)
        if week.long_run_duration_s is not None:
            entry["long_run_minutes"] = round(week.long_run_duration_s / 60)
        if week.quality_focus:
            entry["quality_focus"] = week.quality_focus
        if week.challenges:
            from app.services.schedule.coach_view import challenge_line

            entry["challenges"] = [challenge_line(c) for c in week.challenges]
        if week.coverage == "empty":
            entry["note"] = "the plan says nothing about this week"
        elif week.coverage == "sketched":
            entry["note"] = (
                "shape only: agreed in outline, no sessions written yet"
            )
        weeks.append(entry)

    # Each goal states how exact its date is (#1042), so an approximate month
    # on the horizon is never read as a booked race.
    races = [goals.for_coach(race, today) for race in horizon.races]
    written = [w for w in weeks if w["written"]]
    from app.services.schedule.coach_view import season_section

    return {
        "has_plan": True,
        **season_section(db, user, today),
        **(
            {"plan_is_still_short_of": horizon.shortfalls}
            if horizon.shortfalls
            else {}
        ),
        "today": today.isoformat(),
        "week_count": len(weeks),
        "weeks": weeks,
        "races": races,
        # Two different facts, and the coach was previously given only the first
        # (#981). A plan can run to October while telling the runner what to do
        # only until the end of this month.
        #
        # Both are WEEK STARTS and say so in their names. The baseline states the
        # same two facts as DAYS (`runs_through`, `sessions_written_through`), and
        # a coach holding "2026-10-05" and "2026-10-11" for what sounds like one
        # question will eventually pick the wrong one to say out loud.
        "plan_covers_through_week_starting": weeks[-1]["week_start"] if weeks else None,
        "sessions_written_through_week_starting": (
            written[-1]["week_start"] if written else None
        ),
        "how_to_read": (
            "A week marked written holds real sessions this runner has agreed to. "
            "A week marked shape only is a direction you both settled, not a "
            "prescription: say what it is FOR, and never name a session, a day or "
            "a distance in it as though it were written. If the runner needs one "
            "of those weeks written out, offer amend_plan."
        ),
    }


def get_personal_bests(db: Session, owner_user_id) -> dict:
    """The runner's PBs at the distances runners race (#1068), each labelled with
    how far it can be trusted; see `app.services.personal_bests`."""
    from app.services.personal_bests import for_runner

    return for_runner(db, owner_user_id)
