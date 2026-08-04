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

**Adding one.** Each skill earns its place from an OBSERVED failure, not an
imagined taxonomy of requests. `make coach-review` pulls real chats into
`docs/audit/` for exactly this. Record the failure in the skill's `earned_by`.
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


SKILLS: Tuple[CoachingSkill, ...] = (PLAN_THE_WEEK,)

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
