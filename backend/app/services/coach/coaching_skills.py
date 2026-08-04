"""Coaching skills — house-authored procedures for hard kinds of request (ADR 0029).

A `Coaching skill` is a named procedure for one KIND of request: when it applies,
what to check first, the shape the answer takes, and the discipline that binds it.

**A skill says how to conduct a turn, never what is true about training.** Any
training belief belongs in `corpus.py`, and a procedure defers to it — so the
runner's selected school still governs the SUBSTANCE of an answer a skill merely
shaped. That keeps ADR 0020's axes parallel: the corpus is what the coach
believes, voice is how it sounds, stance is what it foregrounds, and a skill is
how it runs a multi-step request safely.

Two properties this module exists to hold:

- **Progressive disclosure.** The thread prompt carries only each skill's name and
  one-line "use when"; the model calls `load_coaching_skill` for the procedure it
  needs. The tenth skill costs nothing on the nine turns that do not use it.
- **House-authored and code-resident.** A skill is instructions to the coach, so
  it lives in code and is never runner-supplied. Runner-supplied procedure is
  `User materials`, which ADR 0017 contains as reference-never-instructions;
  a runner-editable skill would walk around that containment through a side door.

A skill can never widen what the coach may write. It cannot invent a proposed
action (that set is server-minted and fixed, ADR 0027) and it cannot lower the
safety floor — the deterministic validator runs over a skilled turn's reply
exactly as it runs over any other.

**Adding one.** Every skill records WHY it exists in `earned_by`, and that field
is expected to be honest: an observed failure with the request that produced it,
or, where the house wanted coverage of a request kind before one appeared, that.
The point is that a later reader can tell the difference and retire a skill that
never earned its keep. `make coach-review` pulls real chats into `docs/audit/`,
and driving a candidate request live before writing the procedure is the cheapest
way to find out what actually needs fixing — two of the four skills here were
written that way, and the probe changed what they say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class CoachingSkill:
    """One named procedure. `use_when` is the only part that costs prompt on a
    turn that does not use it, so it reads as a trigger and nothing more."""

    name: str
    use_when: str
    procedure: str
    # The observed failure this skill was written for (ADR 0029). Not sent to the
    # model — provenance for the humans deciding whether it still earns its place.
    earned_by: str


PLAN_THE_WEEK = CoachingSkill(
    name="plan_the_week",
    use_when=(
        "the runner asks what to do next — plan my week, what should I run "
        "tomorrow, how do I build toward a race"
    ),
    procedure="""PROCEDURE — planning what the runner does next.

Read their current load before you write a single session. You have their
readiness (fitness, fatigue, form) and their recent weeks in front of you; if
either is missing or stale, fetch it. A plan written without it is a guess.

Then, in this order:

1. Say what their present state is, in one or two lines, before proposing
   anything. A runner who is deep in fatigue needs to hear that first — it is
   the reason for the plan that follows.
2. Anchor to what they have actually been doing. Their recent weekly volume and
   session count are the base you build from; the step up from it is small.
   Never write a week that jumps their volume, and never let a plan quietly
   become a bigger week than the one they just finished when they are tired.
3. Check what is already settled between you — a phase you agreed, a race they
   are pointed at, a constraint they told you about. Continue that plan rather
   than issuing a new one over the top of it.
4. Give them the week as a short list of sessions: day, what it is, roughly how
   far or how long, and the effort in words. Enough to act on, not a training
   science essay.
5. Close by naming the one thing that would change it.

What the sessions should BE — how much easy running, where quality belongs, how
a week is shaped — is not this procedure's call. Take that from the coaching
corpus and the runner's stance; they carry the school of thought, and this
procedure only orders the turn.

WHEN THEY MENTION A SYMPTOM.

A runner will often hand you the symptom and the training question together:
"my knee's been sore for two weeks, what should next week look like?" Both halves
deserve an answer, and each has its own lane.

- The symptom is not yours. Do not name it, guess at it, or reason about what is
  causing it — not even hedged as "sounds like" or "could be". Say plainly that
  it is worth having someone qualified look at, and move on. One sentence.
- The training question is still yours. Answer it. Plan the week the way you
  would plan any week for a runner who is carrying something and has told you so:
  conservative, easy, nothing that ramps, and framed by effort rather than by
  what their body is doing.
- Then hand them the decision in their own terms: what you would do differently
  if it eases, and what would make you say stop rather than adjust.

So: "That's worth getting looked at by a physio rather than guessing at — I'm not
the one to work out what it is. What I'd do with the week either way: keep it to
three easy runs, hold the long run where it was rather than extending it, and
skip the quality session. If it settles, we pick the build back up next week; if
running on it is getting worse rather than better, that's the day to stop rather
than adjust."

Not: "Two weeks of pain worse the day after sounds like it could be patellar
tendinopathy — here's a plan to work around it." Naming it is the one thing you
must not do, and it is the thing this request will tempt you into.""",
    earned_by=(
        "2026-08-04, thread turn on seeded production data: 'my right knee has "
        "been sore for about two weeks... what should next week look like? i want "
        "to keep building toward the half'. The coach fetched the runner's "
        "training summary and history, then had its whole reply withheld by "
        "validator rule 5 and replaced with the canned medical redirect — so the "
        "runner got a referral and NOTHING about their training. The floor was "
        "right; the reply was avoidable."
    ),
)


COMPARE_SESSIONS = CoachingSkill(
    name="compare_sessions",
    use_when=(
        "the runner asks how two sessions stack up — was that better than last "
        "week, how did today compare to the same route in June"
    ),
    procedure="""PROCEDURE — comparing two sessions.

Work out WHICH two sessions first. Resolve them from their training history by
the days the runner named, and check your reading of the calendar before you
speak: naming the wrong day makes everything after it wrong.

If the ask is genuinely ambiguous — several sessions on the day they mentioned —
do not stall on it. Take the most likely pair (the two that are actually
comparable, or the most recent), say which two you picked in a few words, and
answer. If you must ask, ask ONE question and answer as much as you can
alongside it. A runner who asked a simple question should not get a menu back.

Then pull the full detail of BOTH sessions. A comparison drawn from a list of
distances and paces is a guess: the differences that matter — heart rate at the
same pace, drift, cadence, how each was structured — only exist in the detail.

Answer in this order:

1. The comparison itself, in a sentence. Which was the stronger run, or what
   changed between them.
2. The two or three numbers that carry it, side by side. Not every field you
   fetched.
3. What was NOT alike. Heat, hills, how far into a hard week each landed,
   whether one was a workout and the other a plod. Name it before it can be
   mistaken for progress or decline — the analysis already flags the ones it
   detected, so use them rather than guessing at a confounder.

If the two are not really comparable, say so plainly and compare what can be
compared, rather than producing a difference that means nothing.

What the difference MEANS for their training — whether an improvement is the
kind worth chasing, whether a slower run matters — comes from your coaching
corpus and this runner's goal, not from the size of the gap.""",
    earned_by=(
        "2026-08-04 probe, seeded production data: 'how did saturday's run "
        "compare to the one on the 1st?'. The coach returned a multi-part "
        "clarifying question instead of a comparison, mislabelled a weekday "
        "('Saturday's run … do you mean the 9km on Monday the 3rd'), and never "
        "fetched the detail of either session."
    ),
)


EXPLAIN_A_METRIC = CoachingSkill(
    name="explain_a_metric",
    use_when=(
        "the runner asks what a number means or whether theirs is any good — "
        "what is HR drift, is my cadence bad, what counts as good efficiency"
    ),
    procedure="""PROCEDURE — explaining a metric.

There are two questions in this one: what the number IS, and what THEIRS says.
Answer both, in that order, and keep them apart.

1. What it is, in one or two plain sentences. What it measures and why a coach
   looks at it. No chain of definitions, no formula unless they asked for it.

2. What theirs says — read against THIS runner, not a textbook. "Yours has sat
   between 4 and 6% on easy runs since June, so today's 6% is ordinary for you"
   is worth more than any published band, because the bands are drawn from a
   population this runner may be nothing like.

   Go and GET the figures. A metric like drift, cadence or efficiency lives in a
   session's detail, not in a list of distances and paces — so pull the detail
   of a handful of comparable sessions and read today's number against them.
   Never tell the runner you could look it up if they want: they already asked.
   Reach for a general range only when they genuinely have no comparable history,
   and say that is what you are doing.

Before you call a number good or bad, apply what the analysis already knows
about that run: heat, hills, and the other confounders it flags exist precisely
because they inflate or flatter a number. A drift figure the analysis has
already discounted for a hot day is not evidence of fatigue, and saying it is
contradicts the report you wrote them.

Finish by telling them what would actually change the number, or what you would
watch it for. A metric is worth explaining because it is actionable; if it is
not, say that too.

Never turn a metric into a verdict about the runner. It is one measurement of
one run, and it never overrides how they are actually training or how they
feel.""",
    earned_by=(
        "2026-08-04 probe, seeded production data: 'what does hr drift actually "
        "mean and is mine bad?'. The coach explained it well but graded the "
        "runner against a published population band ('<5% very controlled, "
        "5-10% normal') rather than their own history, and called a 6.0% drift "
        "'solid' on the very run whose report had flagged that figure as "
        "inflated by 25C heat. The median, not this runner (north-star.md)."
    ),
)


RACE_READINESS = CoachingSkill(
    name="race_readiness",
    use_when=(
        "the runner asks about a race they have coming up — am I ready, can I "
        "hit this time, what should the last weeks look like"
    ),
    procedure="""PROCEDURE — reading a runner against a race.

Establish what you are answering against. The distance, how long until race day,
and what they want from it — finishing, a particular time, or racing it. If the
goal is not stated and you cannot infer it from what they have told you before,
ask that one thing; the answer changes everything downstream.

Then, in this order:

1. Their record against the DEMAND of the distance. Not just total volume: the
   longest they have gone recently, how often they run, and whether they have
   done anything at the effort the race asks for.
2. Their state right now — current load and how the last few weeks have gone.
   A runner with the fitness and no freshness is a different answer from a
   runner still building.
3. The plain answer to the question they asked, before any plan. If they are
   ready, say so. If the honest answer is "for the distance yes, for that time
   no", say that — a runner deserves to know which of the two they are.
4. The shape of the weeks remaining: what builds, what the last week does. Keep
   it to the shape; they can ask for the detail.

If something would gate the whole thing — a symptom they have mentioned, a load
spike, a gap in the long runs — name it as the gating item rather than burying
it in the plan. Where that thing is a symptom, it is not yours to name or reason
about: say it needs a professional's eyes, and answer the training question
around it.

How to build toward a race, how long to taper, what the last hard session should
be — take that from your coaching corpus, not from a template you improvise
here.""",
    earned_by=(
        "Authored on the owner's instruction (2026-08-04) to cover the request "
        "kinds ADR 0029 names, NOT an observed failure: the 'half marathon in 5 "
        "weeks, am I ready?' probe against seeded production data was already "
        "answered well (record vs demand, load, a gating symptom referred not "
        "named, a week-by-week shape). This procedure holds that standard rather "
        "than repairing a defect."
    ),
)


SKILLS: Tuple[CoachingSkill, ...] = (
    PLAN_THE_WEEK,
    COMPARE_SESSIONS,
    EXPLAIN_A_METRIC,
    RACE_READINESS,
)

_BY_NAME: Dict[str, CoachingSkill] = {skill.name: skill for skill in SKILLS}


LOAD_SKILL_TOOL: Dict[str, Any] = {
    "name": "load_coaching_skill",
    "description": (
        "Load the house procedure for a kind of request before you answer it. "
        "Call this in the SAME round as any data you are fetching — it costs you "
        "nothing and it does not use up a lookup. The procedure tells you how to "
        "run the turn; what is true about training still comes from your coaching "
        "corpus."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": [skill.name for skill in SKILLS],
            }
        },
        "required": ["name"],
    },
}


def render_catalogue() -> str:
    """The name + trigger of every skill — the only part carried on every turn."""
    if not SKILLS:
        return ""
    lines = [f"- {skill.name}: {skill.use_when}." for skill in SKILLS]
    return (
        "\nYOUR SKILLS — PROCEDURES FOR HARD REQUESTS:\n"
        "Some requests are worth running to a procedure rather than improvising. "
        "When one of these fits what the runner is asking, load it with "
        "load_coaching_skill before you answer, in the same round as any data you "
        "are fetching:\n" + "\n".join(lines) + "\n"
    )


def load_skill(name: str) -> Optional[CoachingSkill]:
    return _BY_NAME.get((name or "").strip())


def skill_tool_result(name: str) -> dict:
    """What the model reads back from a load. An unknown name is not an error it
    should apologise for — it simply has no procedure and coaches as usual."""
    skill = load_skill(name)
    if skill is None:
        return {
            "ok": False,
            "error": "unknown_skill",
            "available": [s.name for s in SKILLS],
        }
    return {"ok": True, "name": skill.name, "procedure": skill.procedure}
