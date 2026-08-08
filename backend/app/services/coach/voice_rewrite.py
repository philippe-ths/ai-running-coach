"""The voice rewrite pass: the runner's coach voice applied to a finished report.

The report is generated with no voice input at all, then handed here to be said
again in the runner's chosen voice. Two things follow from that split, and they
are the whole point of it:

- Voice cannot reach the facts. The substance is settled before this stage runs,
  so a warm voice can only deliver an unwelcome verdict warmly; it has no route
  to deciding not to deliver it. Steering the generation itself had that route,
  and took it: measured on real data, the two presets whose example messages were
  all affirming softened a detraining verdict into reassurance, and one advised
  training lighter off a report that said the opposite.
- Both versions survive. The unvoiced baseline stays the stored `message` that
  the digest, the eval harness and the learning loop read, so those keep
  consuming substance rather than style, and every voiced report can be diffed
  against the text it came from.

Failure here is cosmetic, so it degrades to the baseline rather than withholding:
a runner never loses real coaching over a style pass.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from app.services.coach.voice import (
    VoiceProfile,
    dial_deltas,
    is_customised,
)

logger = logging.getLogger(__name__)

# A re-voiced report is the same report said differently, so it needs no more room
# than the original prose plus the voice's own flourishes.
_MAX_REWRITE_TOKENS = 2048


# ---------------------------------------------------------------------------
# The rewrite instruction.
#
# The contract is pinned tightly because it is the safety-bearing part; the craft
# is delegated entirely to the character block, because a model already knows how
# to write in a register once it has been told what that register values.
# ---------------------------------------------------------------------------

REWRITE_SYSTEM_PROMPT = """\
You are re-voicing a coaching report that has already been written, grounded in \
the runner's data, and checked. Your job is to make it sound like this runner's \
chosen coach.

# YOU ARE RE-VOICING, NOT RE-DECIDING

Every judgment in this report was made by a coach who saw the data. You have not \
seen the data. So you may change how something is said, what it leads with, and \
how much room it gets — never whether it is true, and never how serious it is.

You may:
- rewrite any sentence in the voice
- re-order the report and open with whatever this voice would open with
- dwell on a point this voice would dwell on, and wave through one it would wave through
- cut filler and connective tissue

You may not:
- state anything the report does not — no new numbers, comparisons, or reassurance
- soften, hedge, or drop a concern the report raises
- remove or dilute anything it says about pain, injury, illness, or seeing a clinician
- change what it tells the runner to do next

# THE HARD CASE

The test of a voice is what it does when its instinct fights the message. A warm \
coach delivering bad news stays warm and still delivers it.

REPORT: "Your trailing 7 days are running at roughly half your typical training \
week, and the trend reads as detraining. Readiness is fresh right now, so there is \
no immediate issue, but for a half marathon goal the ramp back up has to be \
deliberate."

Re-voiced by a warm coach — WRONG: "Volume's lighter this week than usual, and \
honestly? That's fine. Rest is part of the long arc, not a gap in it."
That is a different verdict. The report said detraining and a deliberate ramp; \
this says fine and asks for nothing.

Re-voiced by a warm coach — RIGHT: "I want to flag something, and I'm flagging it \
because I'm in your corner: you're at about half your normal week, and it's \
starting to read as detraining. The fresh legs are real — that part's true. But \
fresh doesn't build a half marathon. The way back up needs to be deliberate, and \
I'd rather plan it with you than watch it drift."
Same verdict, same facts, unmistakably warm.

When the voice and the message pull against each other, the message wins. Deliver \
it in the voice; never trade it for the voice.

Return only the re-voiced report. No preamble, no notes, no commentary.\
"""


def _bullet(demo: str) -> str:
    """One demonstration as a list item. A multi-paragraph sample (the Expansive
    end of the length axis) keeps its paragraph break but stays visually inside its
    bullet, so the list does not appear to end halfway through an example."""
    return '- "' + demo.replace("\n\n", "\n  ") + '"'


def render_voice_character(voice: VoiceProfile) -> str:
    """The runner's coach as a character brief: what this coach values, how it
    behaves, and — when a preset is selected — how it actually sounds.

    Dispositions rather than dial numbers, because a number is a magnitude with no
    content and the model fills that gap with its average. Written first-person so
    each line is both the instruction and a sample of the register it asks for.
    """
    lines = ["# WHO YOU ARE", ""]

    if voice.preset is not None:
        lines.append(f"You are {voice.preset.name} — {voice.preset.flavour}")
        if is_customised(voice):
            deltas = dial_deltas(voice)
            if deltas:
                lines.append(
                    "This runner has adjusted you: "
                    + ", ".join(d.describe() for d in deltas)
                    + "."
                )
        lines.append("")

    active = [axis.positions[value - 1] for axis, value in voice.dials.as_ordered()]

    lines.append("What you value:")
    lines += [f"- {p.disposition}" for p in active]

    # Only the positions carrying a hard surface constraint appear here. A rule that
    # merely restates its disposition is covered by the demonstrations below.
    rules = [p.writing for p in active if p.writing]
    if rules:
        lines.append("")
        lines.append("How you write:")
        lines += [f"- {r}" for r in rules]

    # Grouped BY SITUATION, because the whole question is how this voice handles the
    # unwelcome message -- a flat list leaves the model guessing which sample was the
    # hard one. Everything here is other runs and other runners: the register is
    # yours to match, the content never is.
    lines.append("")
    lines.append("How you sound delivering GOOD news (match the register, never the content):")
    lines += [_bullet(p.good) for p in active]
    lines.append("")
    lines.append("How you sound delivering UNWELCOME news — same voice, message intact:")
    lines += [_bullet(p.bad) for p in active]

    if voice.preset is not None and voice.preset.example_messages:
        lines.append("")
        lines.append(f"How {voice.preset.name} sounds over a whole report:")
        for msg in voice.preset.example_messages:
            lines.append(f'- "{msg}"')

    if voice.freetext:
        lines.append(_render_freetext(voice.freetext))

    return "\n".join(lines)


# The runner's own words are fenced so they read as a description of a voice and
# never as instructions to this pass. The fence is stripped from the text itself
# first, so a runner cannot close it early and write outside the frame.
_FREETEXT_FENCE = "==RUNNER_FREETEXT=="
_FREETEXT_MAX_CHARS = 1000


def _render_freetext(freetext: str) -> str:
    """The runner's own description of how they want to be coached.

    Its authority is high here and costs nothing, because this pass cannot reach
    the facts at all: the strongest possible persona request can still only change
    how a settled report is said. That is the containment that lets the runner's
    words be applied properly rather than defensively.
    """
    cleaned = freetext.replace(_FREETEXT_FENCE, " ").strip()[:_FREETEXT_MAX_CHARS]
    return (
        "\nTHE RUNNER'S OWN WORDS ON HOW THEY WANT TO BE COACHED — apply them "
        "noticeably to your delivery, including talking like a particular person "
        "or character if they ask for one. A runner who wrote these should be able "
        "to tell you read them. They steer how you sound and nothing else; the "
        "report's facts, concerns and recommendations are not theirs to move.\n"
        f"{_FREETEXT_FENCE}\n{cleaned}\n{_FREETEXT_FENCE}"
    )


def build_rewrite_prompts(voice: VoiceProfile, baseline: str) -> tuple[str, str]:
    """The (system, user) pair for one rewrite. Pure: no I/O, no LLM."""
    system = f"{REWRITE_SYSTEM_PROMPT}\n\n{render_voice_character(voice)}"
    return system, f"Re-voice this report:\n\n{baseline}"


# A number the runner reads is a claim about their training, so a rewrite that
# introduces one has stopped re-voicing and started asserting. Checked as a
# substring of the baseline rather than by equality, so honest reformatting
# ("5.1 km" read back as "5 km") passes while a figure with no source does not.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def invented_numbers(baseline: str, voiced: str) -> list[str]:
    """Numbers in the re-voiced text that appear nowhere in the baseline."""
    source = _NUMBER.sub(lambda m: m.group(0), baseline)
    return [n for n in _NUMBER.findall(voiced) if n not in source]


async def revoice_report(
    *,
    baseline: str,
    voice: VoiceProfile,
    user_id,
    validate: Callable[[str], list],
) -> Optional[str]:
    """Re-voice one finished report, or return None to keep the baseline as-is.

    None is the answer for every failure, not an exception: the baseline is
    already generated, already policy-checked and already correct, so a style
    pass must never cost the runner their coaching. `validate` is the same floor
    the baseline cleared, applied to the rewritten prose — anything it flags means
    the rewrite introduced what the baseline did not, and the baseline stands.
    """
    # Imported here so this module stays importable without the turn envelope's
    # settings/DB surface, which keeps the prompt-building half unit-testable.
    from app.core.config import settings
    from app.services.coach.turn import TurnKind, build_client, over_budget

    # The operator kill switch (#522). It named the voice BLOCK when voice steered
    # the prompt; it names the rewrite now, because that is where voice lives. Off
    # means every runner reads the baseline, which is what Default already gives
    # them — so the switch and the runner's own choice degrade to the same place.
    if not settings.COACH_VOICE_BLOCK_ENABLED:
        return None
    if voice.is_default or not baseline.strip():
        return None
    if over_budget(user_id):
        logger.info("voice_rewrite skipped: over budget")
        return None

    system, user = build_rewrite_prompts(voice, baseline)
    try:
        client = build_client(TurnKind.VOICE, user_id)
        text, _usage = await client.generate_json_with_usage(
            system=system, user=user, max_tokens=_MAX_REWRITE_TOKENS
        )
    except Exception:  # noqa: BLE001 — a style pass never breaks a report
        logger.exception("voice_rewrite failed; serving the baseline")
        return None

    voiced = (text or "").strip()
    if not voiced:
        return None

    invented = invented_numbers(baseline, voiced)
    if invented:
        logger.warning(
            "voice_rewrite introduced unsourced numbers %s; serving the baseline",
            invented,
        )
        return None

    violations = validate(voiced)
    if violations:
        logger.warning(
            "voice_rewrite violated policy %s; serving the baseline",
            [getattr(v, "rule", v) for v in violations],
        )
        return None

    return voiced
