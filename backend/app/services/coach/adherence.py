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
    # True when the pipeline's discount-signals stage fired on this run (heat,
    # hills, or stimulant use inflated its HR drift). A confounded run is the
    # runner managing fatigue, not a fair "opportunity" to have added a hard or
    # long session, so the window themes exclude it (see _is_rest_or_recovery).
    # Defaulted so existing call sites and fixtures stay valid.
    discount_signals_fired: Optional[bool] = None


# Effort bands the easy-discipline verdict reads as "kept easy" vs "went hard".
# Moderate (and anything else known) lands in between -> "ignored" (drifted up
# but not a workout).
_EASY_EFFORTS = {"recovery", "easy"}
_HARD_EFFORTS = {"tempo", "hard"}


def _intent_is_easy(intent: Optional[str]) -> bool:
    text = (intent or "").lower()
    return "easy" in text or "recovery" in text


# Effort band and intent tokens that mark a run as a DELIBERATE rest / recovery
# day rather than a training opportunity. A recovery-band effort is the in-data
# proxy for a deliberate easy/rest day; a recovery/rest intent is the runner
# saying so explicitly.
_REST_EFFORTS = {"recovery"}
_REST_INTENT_TOKENS = ("recovery", "rest")


def _intent_is_rest(intent: Optional[str]) -> bool:
    text = (intent or "").lower()
    return any(tok in text for tok in _REST_INTENT_TOKENS)


def _is_rest_or_recovery(c: CandidateActivity) -> bool:
    """Is this comparable run a deliberate rest/recovery day or a confounded run,
    rather than a genuine OPPORTUNITY to have added a quality or long session?

    Mirrors the v13 directional discipline (prompts.py): "a deliberate rest day
    or a fired discount signal is not non-compliance." A recovery-band effort, a
    recovery/rest intent, or a fired discount signal (heat/hills/stimulant) all
    mean the runner was managing fatigue, so the run must not count toward the
    opportunity quorum that lets a window theme call the advice "ignored" (#579).
    The strong "acted_on" read is unaffected: a quality session on such a day
    still counts (those days are only excluded from the negative verdict)."""
    if (c.effort or "").lower() in _REST_EFFORTS:
        return True
    if c.discount_signals_fired:
        return True
    return _intent_is_rest(c.user_intent)


def _enough_opportunity(comparables: List[CandidateActivity]) -> bool:
    """True once the runner has had `_MIN_OPPORTUNITY_RUNS` genuine opportunity
    runs in the window to have acted on a window theme. Deliberate rest/recovery
    and confounded runs are excluded (#579): a window with no real opportunity
    (e.g. only rest/recovery days) must ABSTAIN rather than accuse the runner of
    ignoring advice it was correct to skip."""
    opportunities = [c for c in comparables if not _is_rest_or_recovery(c)]
    return len(opportunities) >= _MIN_OPPORTUNITY_RUNS


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
    if not _enough_opportunity(comparables):
        return None  # not enough genuine opportunity (rest/recovery days excluded)
    return "ignored", "no tempo, interval, or race session followed in the runs since", None


def _verdict_add_long_run(comparables: List[CandidateActivity]) -> Optional[_Verdict]:
    """Did ANY subsequent run register as a long run? Abstains rather than calling
    it "ignored" until the runner has had enough chances."""
    for c in comparables:
        if (c.duration_class or "").lower() == "long":
            return "acted_on", f"a long run followed on {c.date[:10]}", c.date
    if not _enough_opportunity(comparables):
        return None  # not enough genuine opportunity (rest/recovery days excluded)
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
            # Real-data additive phrasings the coach uses to ADD a quality
            # session (verb-led "add/plan/adding/introduce a quality|tempo run").
            # All single-intent "do a workout" requests; none are about
            # executing/capturing a workout (lap button, pace discipline), which
            # stay unrecognised by design.
            "adding a quality", "add a quality run", "quality running session",
            "add one quality", "plan your next quality", "plan next quality",
            "plan your next tempo", "plan a quality", "plan a tempo",
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


def _themes_matching(text: str) -> List[_Theme]:
    return [t for t in _THEMES if any(kw in text for kw in t.keywords)]


def _classify(step: dict) -> Optional[_Theme]:
    """Map a next_step to its theme, or None when unrecognised, multi-intent, or
    a negation/caution (which would otherwise invert the verdict).

    Classifies on `action` + `details` only — the `why` field is the rationale,
    not the action, and a rationale mentioning another theme must not re-theme
    the step (e.g. "Focus on form, so you can add a tempo later").

    The `action` is the imperative (the actual instruction); `details` is the
    elaboration. Real coach advice routinely names a SECOND theme incidentally in
    the details — "Plan your next quality session, focusing on easy recovery
    rides in between" is unambiguously add_quality, but its details mention
    "easy recovery", so scoring the combined text matched two themes and the step
    abstained. So classify the ACTION first: when the imperative alone resolves to
    exactly one theme, trust it. Only when the action is theme-silent do we widen
    to action+details (so steps whose instruction lives in the details still
    classify), keeping the multi-intent abstain there. The negation guard runs on
    the full text either way, so a caution buried in the details still abstains."""
    action_text = str(step.get("action") or "").lower()
    full_text = " ".join(str(step.get(k) or "") for k in ("action", "details")).lower()
    if _has_negation(full_text):
        return None
    action_matched = _themes_matching(action_text)
    if len(action_matched) == 1:
        return action_matched[0]
    if len(action_matched) > 1:
        return None  # the imperative itself is multi-intent -> abstain
    # Action is theme-silent: fall back to the full text (the instruction may
    # live in the details), keeping the multi-intent abstain.
    full_matched = _themes_matching(full_text)
    return full_matched[0] if len(full_matched) == 1 else None


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
