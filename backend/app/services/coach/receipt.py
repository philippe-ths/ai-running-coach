"""
Deterministic, block-aware per-activity RECEIPT (#296, ADR 0010/0011 delta).

The receipt is the instant post-activity touchpoint that replaces the debounced
LLM opener: it fires on ingest, carries RPE/pain + "done" taps, and CANNOT fall
back because no LLM is on the hot path. Personality comes from voiced phrasings
pre-generated OFFLINE through the runner's Voice (stored on
`coaching_relationship`); this module is the deterministic engine that turns the
block facts into receipt text, with a HOUSE-DEFAULT floor that always works.

This module is pure — no DB, no LLM, no I/O. It owns:

- the block-aware SITUATION taxonomy (first / second / multi);
- the fact-slot contract (`ALLOWED_SLOTS`) the templates fill, which also shapes
  the offline voice-generation prompt so generated and house templates are
  interchangeable;
- the HOUSE-DEFAULT templates (the floor);
- the deterministic fill (`build_receipt`): derive the situation + facts from the
  block membership, pick a variant deterministically, fill the slots.

The block facts are filled deterministically here, so the runner's Voice can only
flex DELIVERY (the phrasing variants), never the facts — the cross-voice invariant
that keeps an untrusted voice from breaching the fact/safety floor (ADR 0013).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

# --- situation taxonomy -------------------------------------------------------
# The block-aware situations, keyed off the subject activity's ordinal position
# in its block. The framing keys on the SUBJECT's ordinal (not the block size),
# so out-of-order sync still frames a 2nd-arriving activity as "your second".
SITUATION_FIRST = "first"   # the first activity of the block (solo so far)
SITUATION_SECOND = "second"  # the second activity, right after a prior one
SITUATION_MULTI = "multi"   # the third+ activity, a multi-activity session

SITUATIONS: tuple[str, ...] = (SITUATION_FIRST, SITUATION_SECOND, SITUATION_MULTI)

# The fact-slot contract: the ONLY placeholders a receipt template (house-default
# or voice-generated) may reference. Filled deterministically from the block, so
# a template can never inject a fact the block does not carry. This list is the
# spec the offline voice-generation prompt is given.
ALLOWED_SLOTS: tuple[str, ...] = ("type", "prev_type", "ordinal", "count", "sequence")


# Strava activity types -> a friendly word the receipt can speak. Unknown types
# fall back to the lowercased raw type (or "activity"), so a new Strava type
# degrades to something sensible rather than crashing.
_FRIENDLY_TYPE: dict[str, str] = {
    "run": "run",
    "trailrun": "trail run",
    "virtualrun": "run",
    "walk": "walk",
    "hike": "hike",
    "ride": "ride",
    "virtualride": "ride",
    "ebikeride": "ride",
    "gravelride": "ride",
    "mountainbikeride": "ride",
    "swim": "swim",
    "weighttraining": "strength session",
    "workout": "workout",
    "yoga": "yoga session",
    "elliptical": "elliptical",
    "rowing": "row",
}

# Position word per ordinal, so "{ordinal}" reads naturally in copy.
_ORDINAL_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}


def friendly_type(raw: Optional[str]) -> str:
    """A human word for a Strava activity type ("Run" -> "run", "EBikeRide" ->
    "ride"). Unknown types degrade to the lowercased raw type, never crash."""
    key = (raw or "").lower()
    return _FRIENDLY_TYPE.get(key, key or "activity")


def _ordinal_word(n: int) -> str:
    return _ORDINAL_WORDS.get(n, f"{n}th")


# --- house-default templates (the floor) --------------------------------------
# Always-available deterministic phrasings, one list per situation. The floor is
# inherently safe: it carries only the deterministic block facts and the "how did
# it feel?" prompt, never a metric reading or a medical claim. Voice-generated
# sets (stored on coaching_relationship) override these per situation; a missing
# situation key falls back here.
HOUSE_DEFAULT_TEMPLATES: dict[str, list[str]] = {
    SITUATION_FIRST: [
        "Got your {type} — nice work. How did it feel?",
        "That {type}'s logged. How did it go?",
    ],
    SITUATION_SECOND: [
        "Second one today, right after the {prev_type} — got it. How did the {type} feel?",
        "Back at it: a {type} after the {prev_type}. How did this one feel?",
    ],
    SITUATION_MULTI: [
        "{count} so far today ({sequence}) — I'm tracking the whole session. "
        "How did the {type} feel?",
        "That makes {count} today: {sequence}. How did this {type} feel?",
    ],
}


@dataclass(frozen=True)
class ReceiptTap:
    """One deterministic tappable affordance on a receipt. `kind` is "rpe"/"pain"
    (writes a CheckIn via the value) or "done" (a control signal, no value).
    These are fixed, not LLM-chosen, so the receipt always carries the same quick
    replies. The exact buckets/copy are UX and tunable."""

    label: str
    kind: str            # "rpe" | "pain" | "done"
    value: Optional[int]  # the CheckIn value for rpe/pain; None for done


# The fixed receipt quick-replies: a coarse RPE scale, one pain flag, and the
# "done" control. Coarse RPE buckets are enough for the band-level RPE-vs-HR read
# the full report does (M6); the in-app form remains the place for fine input.
RECEIPT_TAPS: tuple[ReceiptTap, ...] = (
    ReceiptTap("Easy", "rpe", 3),
    ReceiptTap("Moderate", "rpe", 5),
    ReceiptTap("Hard", "rpe", 7),
    ReceiptTap("Flat out", "rpe", 9),
    ReceiptTap("Felt a niggle", "pain", 4),
    ReceiptTap("Done for today ✓", "done", None),
)


@dataclass(frozen=True)
class ReceiptFacts:
    """The deterministic block facts a receipt template fills. `type`/`prev_type`
    are friendly words; `sequence` is the friendly types in start order joined by
    an arrow; `ordinal` is the subject's position word; `count` is the block size
    at receipt time."""

    type: str
    prev_type: Optional[str]
    ordinal: int
    count: int
    sequence: str


@dataclass(frozen=True)
class Receipt:
    """A rendered receipt: the prose `text`, the `situation` key that produced it,
    and the `facts` used (for logging/audit)."""

    text: str
    situation: str
    facts: ReceiptFacts


class _SafeDict(dict):
    """format_map backing that renders an unknown OR null slot as "", so a template
    referencing a slot the situation does not carry degrades gracefully instead of
    raising into the auto-sent receipt path."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


def derive_facts(member_types: Sequence[str], subject_index: int) -> tuple[str, ReceiptFacts]:
    """Derive the situation key and the fact slots from the block's ordered member
    types and the subject activity's index within them.

    `member_types` is the block's members' RAW Strava types in start order;
    `subject_index` is the subject's 0-based position. The situation keys off the
    subject's 1-based ordinal: 1 -> first, 2 -> second, >=3 -> multi.
    """
    if not member_types:
        raise ValueError("a receipt needs at least the subject activity")
    if not (0 <= subject_index < len(member_types)):
        raise IndexError("subject_index out of range for the block members")

    friendly = [friendly_type(t) for t in member_types]
    ordinal = subject_index + 1
    facts = ReceiptFacts(
        type=friendly[subject_index],
        prev_type=friendly[subject_index - 1] if subject_index > 0 else None,
        ordinal=ordinal,
        count=len(member_types),
        sequence=" → ".join(friendly),
    )
    if ordinal == 1:
        situation = SITUATION_FIRST
    elif ordinal == 2:
        situation = SITUATION_SECOND
    else:
        situation = SITUATION_MULTI
    return situation, facts


def fill_template(template: str, facts: ReceiptFacts) -> str:
    """Fill a template's fact slots. Robust against a missing/null slot (renders
    "") so a stored voiced template can never crash the receipt send."""
    slots: Mapping[str, object] = {
        "type": facts.type,
        "prev_type": facts.prev_type or "",
        "ordinal": _ordinal_word(facts.ordinal),
        "count": facts.count,
        "sequence": facts.sequence,
    }
    return template.format_map(_SafeDict(slots)).strip()


def choose_variant(variants: Sequence[str], seed: int) -> str:
    """Pick a variant deterministically by a per-activity seed, so the same
    activity always renders the same receipt (idempotent re-sends) while different
    activities vary. Empty list is a programming error upstream."""
    return variants[seed % len(variants)]


def _variants_for(situation: str, templates: Optional[Mapping[str, Sequence[str]]]) -> Sequence[str]:
    """The voiced variants for a situation, else the house-default floor. A voiced
    set that is missing the situation, empty, or malformed falls through to the
    floor, so a partial generated set never leaves a situation without copy."""
    if templates:
        variants = templates.get(situation)
        if isinstance(variants, (list, tuple)) and any(
            isinstance(v, str) and v.strip() for v in variants
        ):
            return [v for v in variants if isinstance(v, str) and v.strip()]
    return HOUSE_DEFAULT_TEMPLATES[situation]


def build_receipt(
    member_types: Sequence[str],
    subject_index: int,
    *,
    seed: int,
    templates: Optional[Mapping[str, Sequence[str]]] = None,
) -> Receipt:
    """Build the deterministic receipt for the subject activity in its block.

    `member_types`: the block members' raw Strava types in start order.
    `subject_index`: the subject's 0-based index in that list.
    `seed`: a stable per-activity int (the strava_activity_id) selecting a variant.
    `templates`: the runner's voiced template set (`{situation: [variants]}`), or
    None to use the house-default floor. Any situation absent from a voiced set
    falls back to the floor.
    """
    situation, facts = derive_facts(member_types, subject_index)
    variant = choose_variant(_variants_for(situation, templates), seed)
    return Receipt(text=fill_template(variant, facts), situation=situation, facts=facts)
