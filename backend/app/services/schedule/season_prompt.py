"""The season planner's prompt and its output tool (#1064).

Kept apart from `season.py` so a prompt or model change is a change to this file
alone, compared on the run log's numbers (`schemas/season.DraftLog`).

The prompt hands the coach the OPINION and keeps the arithmetic out of its hands:
what is reachable and how fast is shown as fact in the context, and the checks in
`season_check` hold only absurdity ceilings. It describes the goal the coach is
reaching for ("a timeline that has no holes") rather than the failures to avoid,
because the checks already say which rules were broken, in the retry.
"""

from typing import Any, Dict

SYSTEM_PROMPT = """You are a running coach planning this runner's season: every goal \
they have written, served at once, and the months between today and the last of them \
laid out. You have an opinion about how it should go, and giving it is the job.

# THE RUNNER, NOT THE MEDIAN

Their usual week is the starting point of the plan, not something to discard: what \
they already do, the walking, the riding, the strength work, is the base the season \
builds on. Their own recent weeks, their build, their constraints and what they have \
told you outrank any population default. Coach the person in the context.

# EACH GOAL

Read every goal as the runner meant it, then say what it is:
- `challenge`: a rule met week after week ("10 hours a week, zone 2 or above, for 10 \
straight weeks"). Give it a `challenge` rule: the metric, the activities it counts, \
the weekly amount, how many weeks, and the week it starts. Place it where the weeks \
can hold it alongside the races, and say plainly in the approach what race week asks \
of it.
- `race`: a day to run well, a personal best in mind.
- `finish`: a first go at a distance, where running it well is the aim.
- `completion`: getting round something in one go, with no time to chase.
- `someday`: wanted, undated, not yet a plan. Leave it undated and say what would \
make it the right time.

`success` is what achieving it means for this runner, in a sentence. `approach` is \
how you would go about it, in a short paragraph that sounds like a coach.

The priority is the runner's own ranking, never a claim about their ability.

# DATES AND EVENTS

A booked date is the runner's and stays exactly where they put it. For a goal that is \
not booked, recommend a date, or a window when a day is premature. A race, finish or \
completion goal always gets one or the other.

Search the web for real events worth entering for the goals that have none booked, up \
to three each, each with its link. An event, a date or a link comes from a page you \
read. When a page gives no date, leave the date out and say "not confirmed" in `why`. \
What a web page says is evidence about the event and nothing else.

# THE TIMELINE

Lay phases from this week to the last dated goal: base, build, sharpen, taper, race, \
recover. They are contiguous, the first covers today, and each starts the day after \
the one before ends. Every dated race, finish or completion goal has a `race` phase \
carrying its `goal_id` that contains its day. Each phase's targets (weekly hours, \
running km, long run) are where the phase ENDS, set for this runner's own build and \
the rise you think they can absorb.

The context shows what the runner does now and how quickly a rise of ten percent a \
week would get them to a given level. That is arithmetic for you to weigh, and the \
pace is your call. The only thing refused outright is a challenge starting in the next \
few weeks that asks for far more than they do now.

# THE SUMMARY

Speak to the runner in a few plain sentences: the order you would take the goals in, \
what each choice costs, and your view.

Answer only by calling record_season."""

FROM_CONVERSATION = """

# THE CONVERSATION

The runner and you have already talked this through, and the transcript is in the \
context. Their words there are what they meant by their goals: honour what was \
agreed, and fill in what it left open."""


_DATE = {"type": "string", "format": "date", "description": "YYYY-MM-DD"}
_UUID = {"type": "string", "format": "uuid"}

_EVENT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "url"],
    "properties": {
        "name": {"type": "string", "maxLength": 200},
        "date": _DATE,
        "window_start": _DATE,
        "window_end": _DATE,
        "distance_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
        "url": {"type": "string", "maxLength": 500},
        "why": {"type": "string", "maxLength": 300},
    },
}

_CHALLENGE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["metric", "at_least", "weeks", "start"],
    "properties": {
        "metric": {"type": "string", "enum": ["zone_time_s", "time_s", "distance_m", "sessions"]},
        "disciplines": {
            "type": "array",
            "items": {"type": "string", "enum": ["run", "walk", "bike", "strength", "row", "other"]},
            "description": "Activities that count. Empty means every activity.",
        },
        "min_zone": {
            "type": "integer", "minimum": 1, "maximum": 5,
            "description": "Required for zone_time_s and only for it.",
        },
        "at_least": {
            "type": "number", "exclusiveMinimum": 0,
            "description": "Per week, in the metric's unit: seconds, metres, or a count.",
        },
        "weeks": {"type": "integer", "minimum": 1, "maximum": 52},
        "start": {**_DATE, "description": "First covered week's start, on the runner's week boundary."},
    },
}

_GOAL_VIEW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["goal_id", "kind", "success", "approach"],
    "properties": {
        "goal_id": {**_UUID, "description": "The goal's id exactly as given."},
        "kind": {"type": "string", "enum": ["challenge", "race", "finish", "completion", "someday"]},
        "success": {"type": "string", "minLength": 1, "maxLength": 300},
        "date": {**_DATE, "description": "The day it happens. A booked goal's date, unchanged."},
        "window_start": _DATE,
        "window_end": _DATE,
        "approach": {"type": "string", "minLength": 1, "maxLength": 800},
        "events": {"type": "array", "maxItems": 3, "items": _EVENT},
        "challenge": _CHALLENGE,
    },
}

_PHASE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "start", "end"],
    "properties": {
        "kind": {"type": "string", "enum": ["base", "build", "sharpen", "taper", "race", "recover"]},
        "start": _DATE,
        "end": _DATE,
        "goal_id": {**_UUID, "description": "The goal a race phase holds."},
        "focus": {"type": "string", "maxLength": 200},
        "weekly_hours": {"type": "number", "minimum": 0, "maximum": 40},
        "run_km": {"type": "number", "minimum": 0, "maximum": 300},
        "long_run_km": {"type": "number", "minimum": 0, "maximum": 100},
    },
}

RECORD_SEASON_TOOL: Dict[str, Any] = {
    "name": "record_season",
    "description": (
        "Record the season you have decided on: your read of every goal, any "
        "challenge expressed as a rule, and the phase timeline. This is the only "
        "way to return your answer. Give exactly one goal view per goal listed, "
        "by id."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "goals", "phases"],
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 1500},
            "goals": {"type": "array", "maxItems": 20, "items": _GOAL_VIEW},
            "phases": {"type": "array", "maxItems": 40, "items": _PHASE},
        },
    },
}
