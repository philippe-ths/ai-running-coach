"""The plan the coach returns: forced tool, strict coercion (#830).

Containment, not detection — the ADR 0017 spine the material distiller
established, applied to a second generative surface. The model has no free-form
channel here: it answers by calling one tool, and the tool's output is coerced
through strict Pydantic (`extra="forbid"`, every field bounded) before anything
reaches a column. An off-shape or rogue-key answer fails the draft rather than
storing something off-contract.

What the tool deliberately does NOT ask for
-------------------------------------------
`target_effort_score`. The model chooses what to do — discipline, intent, how
long, how far — and `effort.py` computes what that costs from the runner's own
history. See that module for why; in short, a load number an LLM estimated would
be a guess drawn as a bar the runner reads as fact.

It also does not ask for `discipline_mix`/`intent_mix` on the sketched weeks.
Those are shares of a load total, so they are derived from the session counts the
model DOES give and the same load model. One number, one owner.
"""

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.schedule import SpacingRule

MAX_CONCRETE_WEEKS = 6
MAX_SKETCH_WEEKS = 26
MAX_SESSIONS_PER_WEEK = 14
MAX_RULES = 8
SUMMARY_MAX_CHARS = 2000


def normalise(raw: dict) -> dict:
    """Trim what is cosmetic before strict coercion runs.

    Only the summary, and only by truncation. Everything structural is left
    exactly as the model produced it, because quietly repairing a session or a
    rule would mean storing something the coach did not actually say.
    """
    if not isinstance(raw, dict):
        return raw
    summary = raw.get("summary")
    if isinstance(summary, str) and len(summary) > SUMMARY_MAX_CHARS:
        raw = {**raw, "summary": summary[:SUMMARY_MAX_CHARS].rstrip()}

    # `rep_distance_m: 0` is how a model spells "these reps are timed, not
    # measured" — a 3 x 8 minute session has no rep distance. Read as absent
    # rather than rejected: it is not a fact being repaired, it is "no distance
    # given" written a different way, and a whole twelve-week plan should not be
    # thrown away over it. `rest_s: 0` is left alone, because zero rest is a
    # real instruction.
    for week in raw.get("weeks") or []:
        for session in (week or {}).get("sessions") or []:
            if isinstance(session, dict) and session.get("rep_distance_m") == 0:
                session.pop("rep_distance_m")
    return raw


class DraftedSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_start: date
    window_end: date
    intent: Literal["rest", "easy", "long", "quality", "strength"]
    discipline: Literal["run", "walk", "bike", "strength", "row", "other"]
    commitment: Literal["committed", "suggested"] = "committed"
    title: str = Field(min_length=1, max_length=120)
    detail: Optional[str] = Field(default=None, max_length=400)
    target_distance_m: Optional[float] = Field(default=None, ge=0, le=200_000)
    target_duration_s: Optional[int] = Field(default=None, ge=0, le=86_400)
    reps_planned: Optional[int] = Field(default=None, ge=1, le=60)
    rep_distance_m: Optional[float] = Field(default=None, gt=0, le=50_000)
    rest_s: Optional[float] = Field(default=None, ge=0, le=3_600)
    # Distances, not durations (#876). A warm-up written as "10 min easy" is only
    # a distance once multiplied by a pace nobody stated, so the session it
    # belongs to could not be added up; asked for in metres it simply adds.
    warmup_distance_m: Optional[float] = Field(default=None, gt=0, le=50_000)
    cooldown_distance_m: Optional[float] = Field(default=None, gt=0, le=50_000)

    @model_validator(mode="after")
    def _validate_shape(self) -> "DraftedSession":
        if self.window_start > self.window_end:
            raise ValueError("window_start is after window_end")
        # Rep structure is guarded symmetrically: on the intent, and on the count.
        # Guarding only `reps_planned` let `rest_s` ride an easy run, and let a
        # quality session give a rest interval with no count — which `structure()`
        # then dropped silently, because it keys off the count. An instruction the
        # coach wrote and nothing stored is worse than a rejected plan.
        has_reps = self.reps_planned is not None
        has_rep_args = self.rep_distance_m is not None or self.rest_s is not None
        # The warm-up and cool-down do NOT ride with the reps (#878). They tied to
        # the rep count when they arrived, which made a tempo run — a quality
        # session with a warm-up and no reps, and the commonest one in coaching —
        # impossible to write down. A live draft was refused on exactly that and,
        # with only two attempts, the whole plan died for one session.
        #
        # The rep ARGUMENTS keep the count requirement, because they describe
        # reps: "400s off 60" with no count is an instruction `structure()` would
        # drop silently, and an instruction the coach wrote and nothing stored is
        # worse than a rejected plan. The edges describe the SESSION, which exists
        # whether or not it is built around an interval block.
        has_edges = (
            self.warmup_distance_m is not None or self.cooldown_distance_m is not None
        )
        if (has_reps or has_rep_args) and self.intent != "quality":
            raise ValueError("rep structure belongs to a quality session")
        # The edges are not rep structure, and #878 already said why: they
        # "describe the SESSION, which exists whether or not it is built around
        # an interval block". The guard nonetheless kept them tied to `quality`,
        # which made a LONG run impossible to write with a warm-up. Race week is
        # where that bites: a goal race is a long session, the coach gives it a
        # warm-up, and the whole block is rejected over one field.
        #
        # So `long` joins `quality`, and nothing else does. An easy run's warm-up
        # is its own first kilometre, and stating it separately is noise dressed
        # as structure; strength has no distance to warm up over; and a rest day
        # with a distance on it is not a rest day, whichever field carries it.
        if has_edges and self.intent not in ("quality", "long"):
            raise ValueError(
                "a warm-up or cool-down belongs to a quality session or a long run"
            )
        if has_rep_args and not has_reps:
            raise ValueError("rep_distance_m and rest_s need reps_planned")
        return self

    def structure(self) -> Optional[Dict[str, float]]:
        """How this session is built, or None when it is not built out of parts.

        It carries the `{reps_planned, rep_distance_m, rest_s}` shape
        `workout_matching` expects, and since #878 it may also carry only the
        edges: a tempo run has a warm-up and no reps, and keying this off the rep
        count dropped that warm-up on the floor. Whoever reads this reads through
        `.get`, so a shape with no count is invisible to a reader that wants one
        rather than misread by it — with one place that has to say so out loud,
        `_extract_planned_workout`, because the interval matcher scores whatever
        it is handed and would grade a tempo as a botched rep session.
        """
        shape: Dict[str, float] = {}
        if self.reps_planned is not None:
            shape["reps_planned"] = self.reps_planned
        if self.rep_distance_m is not None:
            shape["rep_distance_m"] = self.rep_distance_m
        if self.rest_s is not None:
            shape["rest_s"] = self.rest_s
        if self.warmup_distance_m is not None:
            shape["warmup_distance_m"] = self.warmup_distance_m
        if self.cooldown_distance_m is not None:
            shape["cooldown_distance_m"] = self.cooldown_distance_m
        return shape or None


class DraftedWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    phase: Optional[str] = Field(default=None, max_length=60)
    sessions: List[DraftedSession] = Field(
        default_factory=list, max_length=MAX_SESSIONS_PER_WEEK
    )


class SketchedWeek(BaseModel):
    """A week of the horizon given as shape rather than sessions.

    Counts, not shares: "four runs and two gym sessions" is something a coach
    decides, whereas "run is 71% of the week's load" is an arithmetic consequence
    the app works out.

    A shape has to say enough to be BUILT FROM later, not merely drawn (#981).
    The runner arrives at these weeks, and when they do the sketch is what the
    concrete sessions are written from; a week that recorded only a total and a
    phase name gives that later pass nothing to honour, so it plans afresh and
    the block the runner agreed quietly becomes a different one.

    Two fields carry that, and only two, because two are what a runner and a
    coach actually settle about a week that is still weeks away (#980). A live
    block agreed "13.5 -> 15.5 -> 18 -> 20 km, peak on 6 Sep" stored as four
    weekly totals, and the 20 km, the whole point of the build, was not
    recoverable from what was written down.
    """

    model_config = ConfigDict(extra="forbid")

    week_start: date
    phase: Optional[str] = Field(default=None, max_length=60)
    target_running_distance_m: Optional[float] = Field(default=None, ge=0, le=500_000)
    # The backbone of a distance build. Stated separately from the week's total
    # because the two say different things: 37 km built around a 20 km long run
    # and 37 km built around four 9 km runs are the same total and different
    # weeks, and for a runner building towards a race distance that difference is
    # the one that matters most.
    long_run_distance_m: Optional[float] = Field(default=None, ge=0, le=200_000)
    # What the week's hard session is FOR, in a coach's words ("race-pace tempo",
    # "cruise intervals"), never a prescription. A sketch that named reps and
    # paces would be a concrete session wearing a sketch's clothes, and the
    # runner would read a promise into a week nobody has written yet.
    quality_focus: Optional[str] = Field(default=None, max_length=80)
    sessions_by_discipline: Dict[str, int] = Field(default_factory=dict)
    intent_counts: Dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_counts(self) -> "SketchedWeek":
        allowed = {"run", "walk", "bike", "strength", "row", "other"}
        for key, value in self.sessions_by_discipline.items():
            if key not in allowed:
                raise ValueError(f"unknown discipline {key!r}")
            if value < 0 or value > MAX_SESSIONS_PER_WEEK:
                raise ValueError(f"implausible session count for {key!r}")
        intents = {"rest", "easy", "long", "quality", "strength"}
        for key, value in self.intent_counts.items():
            if key not in intents:
                raise ValueError(f"unknown intent {key!r}")
            if value < 0 or value > MAX_SESSIONS_PER_WEEK:
                raise ValueError(f"implausible session count for {key!r}")
        return self


class DraftedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: List[SpacingRule] = Field(default_factory=list, max_length=MAX_RULES)
    weeks: List[DraftedWeek] = Field(default_factory=list, max_length=MAX_CONCRETE_WEEKS)
    sketch_weeks: List[SketchedWeek] = Field(
        default_factory=list, max_length=MAX_SKETCH_WEEKS
    )
    # Generous, and TRUNCATED rather than rejected (see `normalise`). A live run
    # threw an entire valid twelve-week plan away because the blurb explaining it
    # ran past 600 characters — the substance was fine and the prose was long.
    # Structural fields stay strict; a cosmetic one must never cost a plan.
    summary: Optional[str] = Field(default=None, max_length=SUMMARY_MAX_CHARS)


# One declaration of what a SESSION may state, shared by the drafting tool and
# the amendment tool (#981). A session written by an amendment is held to
# exactly the contract a drafted one is; two copies of this schema would drift,
# and the drift would be a coach allowed to write something through one door
# that the other rejects.
SESSION_PROPERTIES: Dict[str, Any] = {
    "window_start": {
        "type": "string",
        "description": (
            "First day this session may fall on. Same "
            "as window_end pins it to that day."
        ),
    },
    "window_end": {
        "type": "string",
        "description": (
            "Last day it may fall on. Widen the window "
            "when the day genuinely does not matter; "
            "the whole week means any day. The window "
            "must stay inside ONE week — Saturday to "
            "Sunday is fine, Sunday to Monday is not."
        ),
    },
    "intent": {
        "type": "string",
        "enum": [
            "rest",
            "easy",
            "long",
            "quality",
            "strength",
        ],
    },
    "discipline": {
        "type": "string",
        "enum": [
            "run",
            "walk",
            "bike",
            "strength",
            "row",
            "other",
        ],
    },
    "commitment": {
        "type": "string",
        "enum": ["committed", "suggested"],
        "description": (
            "`committed` is the plan — it counts "
            "towards the week and missing it "
            "matters. `suggested` is an optional "
            "extra the runner can decline with no "
            "trace. Default to committed; a week "
            "of suggestions is not a plan."
        ),
    },
    "title": {"type": "string"},
    "detail": {"type": "string"},
    "target_distance_m": {
        "type": "number",
        "description": (
            "The WHOLE session in metres, door "
            "to door, warm-up and cool-down "
            "included. Leave it out only when "
            "the reps below already add up to "
            "the session."
        ),
    },
    "target_duration_s": {"type": "integer"},
    "reps_planned": {"type": "integer"},
    "rep_distance_m": {"type": "number"},
    "rest_s": {"type": "number"},
    "warmup_distance_m": {
        "type": "number",
        "description": (
            "The warm-up in METRES, not "
            "minutes, so the session adds up "
            "instead of being inferred from a "
            "pace nobody stated."
        ),
    },
    "cooldown_distance_m": {
        "type": "number",
        "description": (
            "The cool-down in METRES, not "
            "minutes, for the same reason."
        ),
    },
}



RECORD_TRAINING_PLAN_TOOL = {
    "name": "record_training_plan",
    "description": (
        "Record the training plan you have decided on. This is the only way to "
        "return your answer. Give concrete sessions for the near weeks and shape "
        "only for the weeks beyond. Do not estimate training load for a session — "
        "say what the session IS (discipline, intent, how long or how far) and the "
        "app computes what it costs from this runner's own history."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["rules", "weeks", "sketch_weeks"],
        "properties": {
            "rules": {
                "type": "array",
                "description": (
                    "The spacing rules this week must satisfy. These are the actual "
                    "content of a flexible plan — say them here rather than in prose. "
                    "Every rule needs a runner-readable `label` PLUS the exact "
                    "arguments its kind requires. A rule missing its arguments is "
                    "rejected and the whole plan with it. The five kinds and what "
                    "each one needs:\n"
                    "- rest_day_after: `intent`. Nothing may fall the day after a "
                    "session of that intent. "
                    '{"kind":"rest_day_after","intent":"long",'
                    '"label":"A full rest day after the long run"}\n'
                    "- no_intent_day_before: BOTH `before_intent` and "
                    "`target_intent`. No before_intent session on the day preceding "
                    "a target_intent one. "
                    '{"kind":"no_intent_day_before","before_intent":"quality",'
                    '"target_intent":"long",'
                    '"label":"No quality run the day before the long run"}\n'
                    "- min_days_between: `intent_a`, `intent_b` and `days`. "
                    '{"kind":"min_days_between","intent_a":"quality",'
                    '"intent_b":"quality","days":3,'
                    '"label":"At least three days between hard sessions"}\n'
                    "- preferred_days: `intent` and `weekdays` (0=Mon..6=Sun). "
                    '{"kind":"preferred_days","intent":"long","weekdays":[5,6],'
                    '"label":"Long run needs a free morning - Sat or Sun"}\n'
                    "- max_sessions_per_day: `count`. "
                    '{"kind":"max_sessions_per_day","count":2,'
                    '"label":"At most two sessions in a day"}'
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "label"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "rest_day_after",
                                "no_intent_day_before",
                                "min_days_between",
                                "preferred_days",
                                "max_sessions_per_day",
                            ],
                        },
                        "label": {"type": "string"},
                        "intent": {"type": "string"},
                        "before_intent": {"type": "string"},
                        "target_intent": {"type": "string"},
                        "intent_a": {"type": "string"},
                        "intent_b": {"type": "string"},
                        "days": {"type": "integer"},
                        "weekdays": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "0 = Monday .. 6 = Sunday.",
                        },
                        "count": {"type": "integer"},
                    },
                },
            },
            "weeks": {
                "type": "array",
                "description": "The near weeks, as concrete sessions.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["week_start", "sessions"],
                    "properties": {
                        "week_start": {"type": "string"},
                        "phase": {"type": "string"},
                        "sessions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "window_start",
                                    "window_end",
                                    "intent",
                                    "discipline",
                                    "title",
                                ],
                                "properties": SESSION_PROPERTIES,
                            },
                        },
                    },
                },
            },
            "sketch_weeks": {
                "type": "array",
                "description": (
                    "The weeks beyond, as shape only: the phase, the running "
                    "distance you are aiming at, how far the long run goes, what "
                    "the week's hard session is for, and how many sessions of "
                    "each kind. The runner reaches these weeks and they are then "
                    "written into real sessions from what you say here, so say "
                    "enough that the build you intend survives: a weekly total "
                    "alone cannot tell anyone whether the week was built around a "
                    "20 km long run or four 9 km ones."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["week_start"],
                    "properties": {
                        "week_start": {"type": "string"},
                        "phase": {
                            "type": "string",
                            "description": (
                                "The BLOCK this week belongs to — Base, Build, "
                                "Peak, Taper, Recovery. Weeks in the same block "
                                "share the SAME name, so the horizon can group "
                                "them under one heading. Do not number them "
                                "individually ('Base — Week 3'): a name unique to "
                                "one week groups nothing and turns the horizon "
                                "into a label per row."
                            ),
                        },
                        "target_running_distance_m": {"type": "number"},
                        "long_run_distance_m": {
                            "type": "number",
                            "description": (
                                "How far the long run goes in this week, in "
                                "metres. The backbone of a distance build, and "
                                "the one number a runner remembers about a week "
                                "that is still weeks away. Leave it out only for "
                                "a week that genuinely holds no long run."
                            ),
                        },
                        "quality_focus": {
                            "type": "string",
                            "description": (
                                "What the week's hard session is FOR, in a few "
                                "words: 'race-pace tempo', 'cruise intervals', "
                                "'hill strength'. Not a prescription. Reps, "
                                "paces and rest belong to a concrete session, and "
                                "stating them here would promise a week nobody "
                                "has written yet."
                            ),
                        },
                        "sessions_by_discipline": {
                            "type": "object",
                            "additionalProperties": {"type": "integer"},
                        },
                        "intent_counts": {
                            "type": "object",
                            "additionalProperties": {"type": "integer"},
                        },
                    },
                },
            },
            "summary": {
                "type": "string",
                "description": "One short paragraph: what this block is for.",
            },
        },
    },
}
