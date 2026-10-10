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

It does not ask for the weeks beyond the concrete ones either. The model writes
real sessions for the weeks it is given a frame for; the later weeks are written by
code from the season's phases (`frames.py`, `draft.write_shapes`), so no sum in the
plan is a number a model typed.
"""

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.schedule import PlanPreference, SpacingRule

MAX_CONCRETE_WEEKS = 6
# Three a day: a walk, a run and a gym session is a real day for some runners,
# and a high-volume week of everything they do can pass fourteen.
MAX_SESSIONS_PER_WEEK = 21
MAX_RULES = 8
# #1082: a slot offers at most three options, the session and two others, and a
# plan states at most one of each preference kind.
MAX_ALTERNATIVES = 2
MAX_PREFERENCES = 4
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


# How long a session's `detail` note may be. One name, so the schema the model
# reads and the model that validates it cannot drift (#996).
DETAIL_MAX_LENGTH = 400


class DraftedAlternative(BaseModel):
    """Another way to fill the same slot (#1082)."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["easy", "long", "quality", "strength"]
    discipline: Literal["run", "walk", "bike", "strength", "row", "other"]
    title: str = Field(min_length=1, max_length=120)
    detail: Optional[str] = Field(default=None, max_length=DETAIL_MAX_LENGTH)
    target_distance_m: Optional[float] = Field(default=None, ge=0, le=200_000)
    target_duration_s: Optional[int] = Field(default=None, ge=0, le=86_400)

    @model_validator(mode="after")
    def _sized(self) -> "DraftedAlternative":
        # An option nobody can size is one the runner cannot weigh against the
        # session it stands in for, nor the challenge count.
        if not self.target_distance_m and not self.target_duration_s:
            raise ValueError("an alternative states how far or how long it is")
        return self


class DraftedSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_start: date
    window_end: date
    intent: Literal["rest", "easy", "long", "quality", "strength"]
    discipline: Literal["run", "walk", "bike", "strength", "row", "other"]
    commitment: Literal["committed", "suggested"] = "committed"
    title: str = Field(min_length=1, max_length=120)
    detail: Optional[str] = Field(default=None, max_length=DETAIL_MAX_LENGTH)
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
    # #1082: other ways to fill this slot. The session itself is the recommended
    # option; these are what the runner may do instead, one of them, not all.
    alternatives: List[DraftedAlternative] = Field(
        default_factory=list, max_length=MAX_ALTERNATIVES
    )

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
        if self.alternatives:
            # A choice belongs to a slot the runner has agreed to: a rest day has
            # nothing to swap, and a suggestion is already optional.
            if self.intent == "rest" or self.commitment != "committed":
                raise ValueError("alternatives belong to a committed, non-rest session")
            # An option may not be harder than the session it stands in for: an
            # easy run's alternative cannot be intervals.
            hard = {"quality", "long"}
            for alt in self.alternatives:
                if alt.intent in hard and alt.intent != self.intent:
                    raise ValueError(
                        f"alternative {alt.title!r} is a {alt.intent} session in "
                        f"place of a {self.intent} one"
                    )
        return self

    def alternatives_json(self) -> Optional[List[Dict[str, Any]]]:
        """The alternatives as stored on the row, or None when there are none."""
        if not self.alternatives:
            return None
        return [alt.model_dump(mode="json", exclude_none=True) for alt in self.alternatives]

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


class DraftedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: List[SpacingRule] = Field(default_factory=list, max_length=MAX_RULES)
    # #1082: how to arrange the flexible week among the legal ones. Optional, and
    # never a reason to reject a plan: it ranks, it does not forbid.
    preferences: List[PlanPreference] = Field(
        default_factory=list, max_length=MAX_PREFERENCES
    )
    weeks: List[DraftedWeek] = Field(default_factory=list, max_length=MAX_CONCRETE_WEEKS)
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
    # The cap is stated here because it is ENFORCED here: the model wrote a rich
    # note for one session and the whole amendment was thrown out on
    # `String should have at most 400 characters`, a limit the schema had never
    # mentioned (#996). A field the model cannot see the edge of is one it walks
    # off. Kept in step with `DraftedSession.detail` by test.
    "detail": {
        "type": "string",
        "maxLength": DETAIL_MAX_LENGTH,
        "description": (
            "One short note on how to run it, if it needs one. At most "
            f"{DETAIL_MAX_LENGTH} characters, so a sentence or two, not a briefing."
        ),
    },
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
    "target_duration_s": {
        "type": "integer",
        "description": (
            "The session's time in seconds. Every session states it, 0 on a "
            "rest day."
        ),
    },
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
    "alternatives": {
        "type": "array",
        "maxItems": MAX_ALTERNATIVES,
        "description": (
            "Other ways to fill THIS slot, when the runner could equally do "
            "something else instead, e.g. an easy run or an easy bike. The "
            "session itself is the recommended option. The slot is still ONE "
            "activity: the runner does one option, never all of them. List only "
            "options you would accept in its place on the same day, at the same "
            "or lower effort; never a quality or long session in place of an "
            "easy one. Each states its own intent, discipline, title and how "
            "far or how long. Leave it out when there is no real choice; most "
            "sessions have none."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "discipline", "title"],
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["easy", "long", "quality", "strength"],
                },
                "discipline": {
                    "type": "string",
                    "enum": ["run", "walk", "bike", "strength", "row", "other"],
                },
                "title": {"type": "string"},
                "detail": {"type": "string", "maxLength": DETAIL_MAX_LENGTH},
                "target_distance_m": {"type": "number"},
                "target_duration_s": {"type": "integer"},
            },
        },
    },
}



RECORD_TRAINING_PLAN_TOOL = {
    "name": "record_training_plan",
    "description": (
        "Record the training plan you have decided on. This is the only way to "
        "return your answer. Give concrete sessions for every week the context "
        "lists under THE WEEKS. Do not estimate training load for a session: "
        "say what the session IS (discipline, intent, how long or how far) and the "
        "app computes what it costs from this runner's own history."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["rules", "weeks"],
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
            "preferences": {
                "type": "array",
                "maxItems": MAX_PREFERENCES,
                "description": (
                    "How you want each flexible week arranged. Unlike rules these "
                    "forbid nothing: among the arrangements the rules allow, the app "
                    "recommends the one that best follows them, and tells the runner "
                    "which preference a day's order comes from. Give each kind at "
                    "most once, and only the ones you mean:\n"
                    "- spread_hard_days: keep quality and long sessions on days apart.\n"
                    "- easy_day_before_long: the day before the long run holds only "
                    "easy sessions or rest.\n"
                    "- strength_after_run: put strength on a day that has a run, "
                    "after the run.\n"
                    "- spread_repeats: put repeats of the same session on different "
                    "days."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "spread_hard_days",
                                "easy_day_before_long",
                                "strength_after_run",
                                "spread_repeats",
                            ],
                        }
                    },
                },
            },
            "weeks": {
                "type": "array",
                "description": "The weeks listed under THE WEEKS, as concrete sessions.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["week_start", "sessions"],
                    "properties": {
                        "week_start": {"type": "string"},
                        "sessions": {
                            "type": "array",
                            "description": (
                                f"At most {MAX_SESSIONS_PER_WEEK} sessions in a week: "
                                "a longer session beats a second short one on the "
                                "same day."
                            ),
                            "maxItems": MAX_SESSIONS_PER_WEEK,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "window_start",
                                    "window_end",
                                    "intent",
                                    "discipline",
                                    "title",
                                    "target_duration_s",
                                ],
                                "properties": SESSION_PROPERTIES,
                            },
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
