"""P1.2 Coaching corpus — the code-resident house library of training thought
(ADR 0014, extending the four-layer pull model of ADR 0008).

This module owns the corpus DOMAIN DATA, with no LLM and no I/O (the same kind of
domain data as the voice presets in `voice.py`, stored the same way):

- An always-present **house core** (`HOUSE_CORE`): the compact set of coaching
  principles loaded into every exchange.
- A small library of **house-original schools** (`SCHOOLS`) — three in P1.2:
  `aerobic-base` (the wired default), `polarized`, `enjoyment-and-consistency`.
  These are house-original lenses, NOT impersonations of named methodologies.
- The resolved bundle type (`Corpus`) the retrieval seam returns, and the
  `DEFAULT_SCHOOL_ID` the substrate is wired to (P1.3 replaces the constant with
  the runner's stance selection).

The corpus is the school of thought the coach GROUNDS ITS JUDGMENT in — knowledge
it reasons over, never instructions it obeys, and never a source of FACT. Its
lower authority (philosophy reweights emphasis/method-framing only, and never
overrides measured data or the safety floor) is enforced outside this module in
three layers: prompt rule 25, validator rule 7 (corpus-is-not-evidence), and the
cross-school invariance eval. The keyed LOOKUP that turns a school id into a
`Corpus` (degrading to house-core-only) lives in `retrieval.py` (`fetch_corpus`),
the single seam through which the rest of the system reads the corpus. This module
never renders prompt text — that is `prompts.py`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from app.schemas.material import DistilledMaterial


# ---------------------------------------------------------------------------
# The entry shapes. Short STRUCTURED fields, not long prose: keyed lexical lookup
# over compact entries reaches RAG-level usefulness with no vector store (research
# Q4 / ADR 0014). Frozen and tuple-typed so the library is read-only at runtime
# and diff-reviewable, exactly like voice.py's preset cast.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HouseCore:
    """The always-present coaching principles — the house's baseline judgment,
    loaded into every exchange regardless of the selected school."""

    principles: Tuple[str, ...]


@dataclass(frozen=True)
class School:
    """One house-original school of training thought.

    `stance` is the one-line summary; `principles` are a few short bullet
    principles; `method_framing` is how the school frames training methods and
    emphasis; `emphasis_hints` is what it foregrounds. All of it reweights the
    coach's emphasis and framing only — never the facts or the safety floor.
    """

    id: str
    name: str
    stance: str
    principles: Tuple[str, ...]
    method_framing: str
    emphasis_hints: Tuple[str, ...]


@dataclass(frozen=True)
class Corpus:
    """The resolved corpus for one exchange: the always-present house core, the
    keyed school, and (P4, #286) the runner's own uploaded materials. `school` is
    None when no school resolves — the house-core-only degradation (the
    `narrative`-degrades-to-null idiom). `user_materials` is the runner's ACTIVE
    distilled materials (the `DistilledMaterial` shape, newest-first, soft-capped),
    empty when the runner has none or the seam was called without a db/user; it
    carries the hardest Authority tiering tier (it beats house philosophy for
    stance) yet is still judgment reference, never fact (validator rule 8) and never
    instructions (prompt rule 28). This is what the `fetch_corpus` seam returns and
    what `_build_corpus_context` shapes into the pack's `corpus` section (the
    materials sub-field emitted only under a user-materials-aware prompt)."""

    house_core: HouseCore
    school: Optional[School]
    user_materials: Tuple[DistilledMaterial, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The house core. Compact, universal, goal-tethered coaching principles — the
# explicit layer P1.2 adds ON TOP of the base prompt's implicit philosophy (ADR
# 0014 §5: P1.2 supplements, it does not extract). These never override measured
# data or the safety floor; they shape how the coach weighs and frames a run.
# ---------------------------------------------------------------------------

HOUSE_CORE = HouseCore(
    principles=(
        "Consistency compounds: the best session is one the runner can repeat next "
        "week, so favour sustainable progress over any single heroic effort.",
        "Most running should be genuinely easy; that easy aerobic volume is where "
        "durability is built, and protecting it is usually the highest-leverage habit.",
        "Progress is gradual: load rises in small steps, never in leaps, and a big "
        "jump in volume or intensity is a risk however good the runner feels.",
        "Adaptation happens during recovery, not during the hard session, so easy "
        "days, sleep, and rest are part of the training, not time off from it.",
        "The runner's goal, experience, and life context lead; training serves the "
        "runner, and advice is always tethered to what they are actually training for.",
        "Felt experience is data too: reconcile how a run felt with what the numbers "
        "show rather than overriding either, and treat a mismatch as signal.",
        "Never trade long-term health or durability for a single session's number; "
        "the long game wins, and the safety floor is never negotiable.",
    ),
)


# ---------------------------------------------------------------------------
# The school library (five house-original lenses: the three from P1.2 plus the
# strength-led + periodization schools authored in P1.3, once stance selection
# surfaced them — ADR 0015). They are deliberately distinct in EMPHASIS so the
# same run reads differently under each (AC4), while the facts and the safety
# floor stay invariant across all of them (AC3).
# ---------------------------------------------------------------------------

SCHOOLS = {
    "aerobic-base": School(
        id="aerobic-base",
        name="Aerobic Base",
        stance=(
            "Durability is built by lots of easy aerobic running; the long game beats "
            "any single sharp session."
        ),
        principles=(
            "The aerobic engine is the foundation — build it patiently with easy "
            "volume before reaching for sharpness.",
            "More easy kilometres, run truly easy, beat fewer hard ones for long-term "
            "gains; mileage is the headline, not the intervals.",
            "Steady mileage and the long run are the main work; quality is a small, "
            "late seasoning on top of a deep base.",
            "Guard easy days fiercely — letting easy runs drift to moderate is the "
            "most common way base-building quietly stalls.",
        ),
        method_framing=(
            "Frame training around weekly easy volume and the long run; treat intensity "
            "as a small, well-earned supplement, and read easy-day discipline and "
            "aerobic durability as the signals that matter most."
        ),
        emphasis_hints=(
            "easy-day discipline",
            "weekly volume and the long run",
            "aerobic durability (HR drift, efficiency) over peak pace",
            "patience with progression",
        ),
    ),
    "polarized": School(
        id="polarized",
        name="Polarized Quality",
        stance=(
            "Keep easy days easy and hard days genuinely hard; the moderate middle is "
            "where progress goes to hide."
        ),
        principles=(
            "Polarise effort: the bulk of running easy, a clear minority hard, and "
            "little time spent in the grey moderate middle.",
            "A small number of well-executed quality sessions — threshold and "
            "intervals — are the sharpening stimulus that drives improvement.",
            "Hard days earn their recovery; the easy days exist precisely to make the "
            "hard ones repeatable and sharp.",
            "Structure the week so the key sessions land fresh and are executed with "
            "intent, rather than scattering moderate effort everywhere.",
        ),
        method_framing=(
            "Frame training around a few well-executed quality sessions and the "
            "hard-easy contrast around them; read the quality of the intervals/"
            "threshold work and the discipline of keeping easy days easy as the "
            "signals that matter."
        ),
        emphasis_hints=(
            "hard-easy polarisation",
            "quality of the key sessions (intervals, threshold)",
            "avoiding the moderate-intensity rut",
            "freshness going into the hard day",
        ),
    ),
    "enjoyment-and-consistency": School(
        id="enjoyment-and-consistency",
        name="Enjoyment and Consistency",
        stance=(
            "The run that happens beats the perfect plan that doesn't; enjoyment is "
            "what keeps the runner lacing up."
        ),
        principles=(
            "Consistency and enjoyment are the engine — a sustainable habit "
            "outperforms an optimal plan the runner abandons.",
            "Let feel lead more often than the watch; flexibility around real life is "
            "what keeps running in the week.",
            "Celebrate showing up — small wins and variety protect the habit far "
            "better than rigid targets do.",
            "Hard efforts are welcome when they feel good and wanted, not because a "
            "schedule demands them.",
        ),
        method_framing=(
            "Frame training around the habit and how running feels week to week; read "
            "consistency, enjoyment, and simply showing up as the primary signals, and "
            "treat structure as a light scaffold the runner can flex around life."
        ),
        emphasis_hints=(
            "consistency and the habit",
            "enjoyment and how the run felt",
            "flexibility around life",
            "small, sustainable wins",
        ),
    ),
    "strength-led": School(
        id="strength-led",
        name="Strength-Led",
        stance=(
            "Running serves the strength goal; conditioning should support the "
            "lifting and the body, not compete with it for recovery."
        ),
        principles=(
            "Running is in service of the primary strength goal — it builds work "
            "capacity and health, it does not become the main event.",
            "Keep most running genuinely easy so it adds aerobic fitness without "
            "stealing the recovery the strength work needs.",
            "Watch the interference cost: hard running and hard lifting compete for "
            "the same recovery, so place and size running around the key sessions.",
            "Durability and consistency beat chasing running PRs; the win is showing "
            "up to lift fresh, not setting a 5k best.",
        ),
        method_framing=(
            "Frame running as conditioning that complements the strength work: read "
            "easy-day discipline, manageable fatigue, and recovery between hard "
            "efforts as the signals that matter, and treat a run that compromises the "
            "lifting as too much, not as progress."
        ),
        emphasis_hints=(
            "running as support for the strength goal",
            "keeping easy running easy to protect recovery",
            "fatigue management across lifting and running",
            "durability over running performance",
        ),
    ),
    "periodization": School(
        id="periodization",
        name="Periodization",
        stance=(
            "Training is organised into phases with distinct purposes; what a run is "
            "for depends on where it sits in the cycle."
        ),
        principles=(
            "Train in phases — base, build, then sharpen and peak — each with a "
            "distinct purpose, rather than doing a bit of everything every week.",
            "Progressive overload plays out across the whole cycle: load rises "
            "through a block, then a deliberate down week consolidates the gains.",
            "The phase dictates the session — a base-phase run and a peak-phase run "
            "have different jobs, so judge a run against what its phase is for.",
            "Recovery weeks and tapers are scheduled work, not lost time; the peak is "
            "earned by the phasing that came before it.",
        ),
        method_framing=(
            "Frame this run by where it sits in the training cycle: read whether it "
            "fits the current phase's purpose and the planned progression, and treat "
            "structure, periodised load, and timely recovery weeks as the signals "
            "that matter most."
        ),
        emphasis_hints=(
            "the current training phase and its purpose",
            "progression across the block, not the single run",
            "scheduled recovery weeks and tapers",
            "structure and periodised load",
        ),
    ),
}


# The school the substrate is wired to in P1.2 (P1.3 replaces this constant with
# the runner's stance selection). Kept in the library so the seam and the pack
# builder share one source of truth.
DEFAULT_SCHOOL_ID = "aerobic-base"
