"""M7 adherence learning loop: did the runner act on the LAST report's advice?

For each `next_step` in the most recent prior report, label the runner's
subsequent comparable activity acted-on / ignored / contradicted, from the
already-stored re-derived `DerivedMetric` — zero extra runner effort, the
"gets better over time" loop. Advisory and auditable, never a compliance score.

Design (deliberately conservative, mirroring the M5 "deterministic-only with
documented blind spots" discipline):

- A next_step is free text. We map it to one of a small set of RECOGNISED
  THEMES by keyword; an unrecognised or multi-intent (matches >1 theme) step
  ABSTAINS (no outcome). Adding a theme is a localised change to `_THEMES`.
- "Comparable" is per-theme: the subsequent run must be a fair test of the
  advice (e.g. easy-discipline only judges runs the runner MEANT as easy, read
  from `user_intent`, so a scheduled workout is never mislabelled a failure).
- A low-confidence subsequent metric is NOISE and is dropped before judging, so
  a verdict never fires on an unreliable read.
- Comparability is judged on intent/structure, the verdict on the executed
  effort/duration axes, so the two never circularly depend on each other.
- Explicit pushback on the prior report (a CheckIn note or chat message saying
  the advice was off) flips the implicit label to "disputed": explicit beats
  noisy implicit. Pushback only matters when an implicit label exists.

This module is pure — all inputs are plain data gathered by the caller
(`context.py`), the same split M6 `perceived_effort` uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from app.schemas.coach_context import AdherenceContext, NextStepOutcome

# A window theme ("did you add quality / a long run at all?") only calls a miss
# "ignored" once the runner has had a real chance to act. A report is generated
# per activity, so the window between two reports is usually a single run; firing
# "ignored" on one easy day after the advice would be a false accusation (the
# runner has not reached the session yet). Below this many comparable runs in the
# window, a window theme abstains instead of asserting "ignored". (The
# easy-discipline theme needs no such gate: it judges the NEXT comparable run,
# which is itself a fair, immediate test of the advice.)
_MIN_OPPORTUNITY_RUNS = 3

# Negation / caution markers. A next_step that tells the runner to HOLD BACK from
# a theme ("be careful not to add intensity", "hold off on extending the long
# run") matches the same keywords as the positive advice; labelling the runner
# "acted_on" for doing the very thing they were warned against would invert the
# coaching truth. Deterministic negation parsing is unreliable, so any negation
# marker makes the step abstain (the safe direction; recall loss only).
_NEGATION_WORDS = re.compile(
    r"\b(not|never|avoid|resist|refrain|cannot|without|don'?t|doesn'?t|didn'?t|won'?t|can'?t)\b"
)
_NEGATION_PHRASES = ("hold off", "be careful", "no need", "stay away")


@dataclass(frozen=True)
class CandidateActivity:
    """Minimal pre-gathered facts about one subsequent activity, enough to judge
    adherence. Plain data so the module stays pure and fixture-testable."""

    date: str  # ISO start_date
    effort: Optional[str]  # recovery|easy|moderate|tempo|hard
    duration_class: Optional[str]  # standard|long
    structure: Optional[str]  # continuous|intervals
    is_race: Optional[bool]
    confidence: Optional[str]  # low|medium|high
    user_intent: Optional[str]


# Effort bands the easy-discipline verdict reads as "kept easy" vs "went hard".
# Moderate (and anything else known) lands in between -> "ignored" (drifted up
# but not a workout).
_EASY_EFFORTS = {"recovery", "easy"}
_HARD_EFFORTS = {"tempo", "hard"}


def _intent_is_easy(intent: Optional[str]) -> bool:
    text = (intent or "").lower()
    return "easy" in text or "recovery" in text


# Intent labels that declare a deliberate hard/structured session. A run the
# runner explicitly meant as one of these is never judged against easy-discipline
# advice (it was never meant to be easy).
_WORKOUT_INTENT_TOKENS = (
    "tempo", "interval", "race", "hill", "workout", "threshold", "fartlek",
    "speed", "sprint", "hard",
)


def _intent_is_workout(intent: Optional[str]) -> bool:
    text = (intent or "").lower()
    return any(tok in text for tok in _WORKOUT_INTENT_TOKENS)


def _comparable_easy(c: CandidateActivity) -> bool:
    """Is this subsequent run a fair test of "keep your easy running easy"?

    Excludes runs that were clearly deliberate hard efforts (a race, a detected
    interval session, or a declared workout intent) so a scheduled session is
    never counted as a failed easy run. Includes easy/recovery-labelled runs AND
    unlabelled continuous-style runs (this runner labels no intent, so requiring
    an explicit easy label would make the signal never fire); the verdict caps
    what it will assert on an unlabelled run (see `_verdict_easy_discipline`).
    Requires a known effort so there is something to judge."""
    return (
        bool(c.effort)
        and not c.is_race
        and (c.structure or "").lower() != "intervals"
        and not _intent_is_workout(c.user_intent)
    )


# Verdict label = (label, basis, comparable_activity_date). A verdict function
# returns None to abstain (no fair call to make), which build_adherence drops.
_Verdict = Tuple[str, str, Optional[str]]


def _verdict_easy_discipline(comparables: List[CandidateActivity]) -> Optional[_Verdict]:
    """Judge the NEXT comparable run after the advice: did effort stay easy?

    The strong "contradicted" verdict (ran the opposite of easy) is asserted only
    when the run was EXPLICITLY meant easy (the runner labelled the intent); on an
    unlabelled run we cannot rule out a deliberate workout, so a hard read is
    softened to "ignored" rather than accuse the runner of doing the opposite."""
    first = comparables[0]
    effort = (first.effort or "").lower()
    day = first.date[:10]
    explicit_easy = _intent_is_easy(first.user_intent)
    if effort in _EASY_EFFORTS:
        return "acted_on", f"the next comparable run ({day}) stayed in the {effort} band", first.date
    if effort in _HARD_EFFORTS:
        if explicit_easy:
            return "contradicted", f"the next run the runner meant easy ({day}) was run at {effort} effort", first.date
        return "ignored", f"the next comparable run ({day}) came out at {effort} effort, harder than the easy work discussed", first.date
    return "ignored", f"the next comparable run ({day}) drifted to {effort or 'unknown'} effort", first.date


def _verdict_add_quality(comparables: List[CandidateActivity]) -> Optional[_Verdict]:
    """Did ANY subsequent run add a quality stimulus (tempo/hard, intervals, race)?
    Abstains rather than calling it "ignored" until the runner has had enough
    chances (see _MIN_OPPORTUNITY_RUNS)."""
    for c in comparables:
        if (
            (c.effort or "").lower() in _HARD_EFFORTS
            or (c.structure or "").lower() == "intervals"
            or c.is_race
        ):
            descriptor = "intervals" if (c.structure or "").lower() == "intervals" else (
                "a race" if c.is_race else f"{c.effort} effort"
            )
            return "acted_on", f"a quality session followed on {c.date[:10]} ({descriptor})", c.date
    if len(comparables) < _MIN_OPPORTUNITY_RUNS:
        return None  # not enough opportunity yet to call it ignored
    return "ignored", "no tempo, interval, or race session followed in the runs since", None


def _verdict_add_long_run(comparables: List[CandidateActivity]) -> Optional[_Verdict]:
    """Did ANY subsequent run register as a long run? Abstains rather than calling
    it "ignored" until the runner has had enough chances."""
    for c in comparables:
        if (c.duration_class or "").lower() == "long":
            return "acted_on", f"a long run followed on {c.date[:10]}", c.date
    if len(comparables) < _MIN_OPPORTUNITY_RUNS:
        return None
    return "ignored", "no long run followed in the runs since", None


@dataclass(frozen=True)
class _Theme:
    name: str
    keywords: Tuple[str, ...]
    comparable: Callable[[CandidateActivity], bool]
    verdict: Callable[[List[CandidateActivity]], Optional[_Verdict]]


# Recognised next_step themes (v1). Keyword phrases are matched against the
# lowercased action+details+why. Phrases are multi-word where a bare word would
# over-match (e.g. "easy" alone appears in praise of any easy run).
_THEMES: Tuple[_Theme, ...] = (
    _Theme(
        name="easy_discipline",
        keywords=(
            "keep it easy", "keep them easy", "keep your easy", "easy runs easy",
            "easy run easy", "keep easy", "stay easy", "run easy", "run it easy",
            "easier", "slow down", "slower", "back off", "dial back",
            "dial it back", "reduce intensity", "reduce the intensity",
            "more recovery", "recovery pace", "zone 2", "aerobic base",
            "keep aerobic", "easy effort",
            # Real-data phrasings (this coach leans heavily on easy/recovery advice):
            "easy recovery", "recovery run", "recovery day", "recovery activit",
            "recovery session", "recovery walk", "active recovery", "easy-paced",
            "easy pace", "easy session", "easy day", "easy walk", "true easy",
            "conversational", "gentle movement", "comfortable conversational",
        ),
        comparable=_comparable_easy,
        verdict=_verdict_easy_discipline,
    ),
    _Theme(
        name="add_quality",
        # Additive phrasing ONLY ("add/do/introduce a workout"): a bare workout
        # noun ("tempo run", "interval session") also appears in advice ABOUT
        # executing or measuring a workout ("pace discipline in tempo runs",
        # "lap button for interval sessions"), which is not advice to DO one.
        keywords=(
            "add a tempo", "add tempo", "add a workout", "add intervals",
            "add an interval", "add a interval", "add speed", "add a threshold",
            "add a quality", "add some intensity", "add intensity",
            "introduce intensity", "introduce a workout", "speed work",
            "speedwork", "strides", "fartlek", "quality session",
            "tempo session", "interval workout", "do a tempo", "do intervals",
        ),
        comparable=lambda c: True,  # every later run is a chance to have added quality
        verdict=_verdict_add_quality,
    ),
    _Theme(
        name="add_long_run",
        keywords=(
            "long run", "longer run", "go longer", "extend your long",
            "build the long", "increase your long", "add distance",
            "increase distance", "more volume", "add volume", "extend the long",
        ),
        comparable=lambda c: True,
        verdict=_verdict_add_long_run,
    ),
)


def _has_negation(text: str) -> bool:
    """True if the step tells the runner to HOLD BACK from the matched theme."""
    if _NEGATION_WORDS.search(text):
        return True
    return any(phrase in text for phrase in _NEGATION_PHRASES)


def _classify(step: dict) -> Optional[_Theme]:
    """Map a next_step to its theme, or None when unrecognised, multi-intent, or
    a negation/caution (which would otherwise invert the verdict).

    Classifies on `action` + `details` only — the `why` field is the rationale,
    not the action, and a rationale mentioning another theme must not re-theme
    the step (e.g. "Focus on form, so you can add a tempo later")."""
    text = " ".join(str(step.get(k) or "") for k in ("action", "details")).lower()
    if _has_negation(text):
        return None
    matched = [t for t in _THEMES if any(kw in text for kw in t.keywords)]
    return matched[0] if len(matched) == 1 else None


def build_adherence(
    *,
    prior_report_date: Optional[str],
    prior_next_steps: List[dict],
    candidates: List[CandidateActivity],
    pushback: bool,
) -> AdherenceContext:
    """Assemble the adherence section. Pure; all inputs are already gathered.

    `candidates` are the subsequent activities (oldest-first) between the source
    report and this run. Degrades to empty `outcomes` (the coach then says
    nothing about adherence) when there is no prior report, no recognised step,
    or no comparable non-noisy run to judge against.
    """
    # Noise gate: a low-confidence subsequent read is not trustworthy ground for
    # a verdict, so it is dropped before any judging.
    clean = [c for c in candidates if (c.confidence or "").lower() != "low"]

    outcomes: List[NextStepOutcome] = []
    for step in prior_next_steps:
        theme = _classify(step)
        if theme is None:
            continue
        comparables = [c for c in clean if theme.comparable(c)]
        if not comparables:
            continue  # no comparable run yet -> abstain
        verdict = theme.verdict(comparables)
        if verdict is None:
            continue  # the theme abstained (e.g. too few chances to call it ignored)
        label, basis, comparable_date = verdict
        overridden = False
        if pushback:
            # Explicit beats noisy implicit: the runner pushed back on the prior
            # report, so mark every outcome from it "disputed". The prompt treats
            # disputed as settled and says nothing about it, so a false-positive
            # pushback detection only SUPPRESSES an adherence note (the safe
            # direction), never fabricates a "you were wrong" exchange. The
            # implicit read is preserved in the basis for audit/eval only.
            basis = f"runner pushed back on this advice; implicit read was '{label}'. {basis}"
            label = "disputed"
            overridden = True
        outcomes.append(
            NextStepOutcome(
                prior_action=str(step.get("action") or "").strip(),
                theme=theme.name,
                label=label,
                comparable_activity_date=comparable_date,
                basis=basis,
                overridden=overridden,
            )
        )

    return AdherenceContext(
        prior_report_date=prior_report_date if outcomes else None,
        outcomes=outcomes,
    )
