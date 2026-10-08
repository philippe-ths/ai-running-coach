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
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.services.coach.voice import (
    VoiceProfile,
    dial_deltas,
    is_customised,
)

logger = logging.getLogger(__name__)

# Room for both halves of the answer: the findings list, then the report composed
# from it. A report is a few hundred words, so this is a ceiling, not a target; an
# answer cut off before its report closes is refused rather than served half-said.
_MAX_REWRITE_TOKENS = 3000


# ---------------------------------------------------------------------------
# The rewrite instruction.
#
# The contract is pinned tightly because it is the safety-bearing part; the craft
# is delegated entirely to the character block, because a model already knows how
# to write in a register once it has been told what that register values.
#
# The pass COMPOSES from findings rather than editing prose (#1050). Handed a
# finished report to "re-voice", every character kept that report's opening,
# order, paragraphing and length and changed its adjectives, so six presets read
# as one coach. Listing the substance first and writing from the list makes the
# baseline a source of facts instead of a template, and the list is what lets a
# terse character be terse without dropping a concern on the way.
# ---------------------------------------------------------------------------

REWRITE_SYSTEM_PROMPT = """\
You are this runner's coach, writing their report in your own voice. The \
substance is already settled: a coach who saw the runner's data wrote the report \
you are given, and it has been checked. You have not seen the data. You decide \
how the report is said; the report decides what is said.

# FIRST, LIST THE FINDINGS

Read the report and list what it says, one plain line each, in no voice at all:
- VERDICT: each judgment it makes about the run, the week, or the trend
- CONCERN: each risk or worry, at the seriousness the report gives it
- SAFETY: anything about pain, injury, illness, or seeing a clinician
- NEXT: what it tells the runner to do, and when
- ASK: each question it puts to the runner
- DETAIL: each supporting fact, with its figures written as the report writes them

# THEN, WRITE THE REPORT FROM THE FINDINGS

Write the report you would write if these findings were your own. The report you \
were given is where the facts come from, not a template: its opening, its order, \
its paragraphs, its length and its phrasing were one coach's choices, and you are \
a different coach. Make your own, the way your brief says you build a report.

Every VERDICT, CONCERN, SAFETY, NEXT and ASK line belongs in your report at the \
weight the report gave it, and every ASK is still asked as a question. DETAIL is \
yours to choose from: keep the figures you would lean on and leave the rest. A \
length limit in your brief is met by leaving DETAIL out, never by leaving out one \
of the others.

Say nothing the findings do not: no new numbers, comparisons, causes, or \
reassurance.

# THE HARD CASE

The test of a voice is what it does when its instinct fights the message. A warm \
coach delivering bad news stays warm and still delivers it.

REPORT: "Your trailing 7 days are running at roughly half your typical training \
week, and the trend reads as detraining. Readiness is fresh right now, so there is \
no immediate issue, but for a half marathon goal the ramp back up has to be \
deliberate."

<findings>
- VERDICT: the trailing 7 days are roughly half the typical training week
- VERDICT: the trend reads as detraining
- VERDICT: readiness is fresh, so there is no immediate issue
- NEXT: the ramp back up toward the half marathon goal has to be deliberate
</findings>

Written by a warm coach, WRONG: "Volume's lighter this week than usual, and \
honestly? That's fine. Rest is part of the long arc, not a gap in it."
The detraining verdict and the deliberate ramp are gone, and "fine" is a verdict \
the report never gave.

Written by a warm coach, RIGHT: "Your legs are fresh, and that part is real: \
nothing is wrong today. Here is the part you won't love, and I'd rather you heard \
it from me. You're at about half your normal week, and it is starting to read as \
detraining. Fresh doesn't build a half marathon. Let's plan the way back up \
deliberately, together, instead of letting it drift."
Every finding is there, in a different order from the report, and it is \
unmistakably warm.

When your voice and a finding pull against each other, the finding wins. Deliver \
it in your voice; never trade it for your voice.

Answer in exactly this form, with nothing outside it:
<findings>
one line per finding
</findings>
<report>
the report, in your voice
</report>\
"""


def _bullet(demo: str) -> str:
    """One demonstration as a list item. A multi-paragraph sample (the Expansive
    end of the length axis) keeps its paragraph break but stays visually inside its
    bullet, so the list does not appear to end halfway through an example."""
    return '- "' + demo.replace("\n\n", "\n  ") + '"'


def render_voice_character(voice: VoiceProfile) -> str:
    """The runner's coach as a character brief: what this coach values, how it
    behaves, how it sounds sentence by sentence, and -- when a preset is selected
    -- how it builds a whole report.

    Dispositions rather than dial numbers, because a number is a magnitude with no
    content and the model fills that gap with its average. Written first-person so
    each line is both the instruction and a sample of the register it asks for.

    A preset contributes its `report_shape`, not its example messages. Those are
    whole reports about other runs, and a whole report is the one sample a model
    will reuse: measured, their stock lines surfaced in reports about runs they
    were never written for. A shape says how the report is built and holds no
    sentence to lift.
    """
    lines = ["# WHO YOU ARE", ""]

    if voice.preset is not None:
        lines.append(f"You are {voice.preset.name}: {voice.preset.flavour}")
        lines.append(f"How you build a report: {voice.preset.report_shape}")
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
    lines.append(
        "How you sound delivering GOOD news (match the register, never the content; "
        "every figure below belongs to somebody else's run, and reusing one is the "
        "single most common way a report in your voice gets thrown away):"
    )
    lines += [_bullet(p.good) for p in active]
    lines.append("")
    lines.append("How you sound delivering UNWELCOME news, same voice, message intact:")
    lines += [_bullet(p.bad) for p in active]

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
        "\nTHE RUNNER'S OWN WORDS ON HOW THEY WANT TO BE COACHED: apply them "
        "noticeably to your delivery, including talking like a particular person "
        "or character if they ask for one. A runner who wrote these should be able "
        "to tell you read them. They steer how you sound and nothing else; the "
        "report's facts, concerns and recommendations are not theirs to move.\n"
        f"{_FREETEXT_FENCE}\n{cleaned}\n{_FREETEXT_FENCE}"
    )


# The opener is a two-line first reaction, not a report, and a character brief that
# says how a whole report is built would otherwise grow it into one.
_OPENER_NOTE = (
    "\n\nThis is a short first reaction to the run, not the full report: keep it "
    "about as long as the text you are given, whatever your brief says about length."
)


def build_rewrite_prompts(
    voice: VoiceProfile, baseline: str, *, is_opener: bool = False
) -> tuple[str, str]:
    """The (system, user) pair for one rewrite. Pure: no I/O, no LLM."""
    system = f"{REWRITE_SYSTEM_PROMPT}\n\n{render_voice_character(voice)}"
    if is_opener:
        system += _OPENER_NOTE
    return system, f"The report:\n\n{baseline}"


_REPORT_AFTER_FINDINGS = re.compile(r"\s*<report>(.*)</report>\s*", re.DOTALL)
_TAG = re.compile(r"</?(?:findings|report)>", re.IGNORECASE)
_FINDING_LINE = re.compile(
    r"^\s*-?\s*(?:VERDICT|CONCERN|SAFETY|NEXT|ASK|DETAIL)\s*:", re.MULTILINE
)


def extract_report(raw: str) -> Optional[str]:
    """The report half of the model's answer, or None when there is no clean one.

    The findings list is working material and must never reach the runner, so
    only an answer in exactly the asked-for form is accepted: one findings block,
    then one report block and nothing after it. Anything else (no tags, a
    truncated report, a second block, a findings line or a tag left inside the
    report) is refused, because each is a way for the list, or half a report, to
    be served as coaching.
    """
    head, sep, tail = raw.partition("</findings>")
    if not sep or not head.lstrip().startswith("<findings>"):
        return None
    match = _REPORT_AFTER_FINDINGS.fullmatch(tail)
    if match is None:
        return None
    report = match.group(1).strip()
    if _TAG.search(report) or _FINDING_LINE.search(report):
        return None
    return report


# A number the runner reads is a claim about their training, so a rewrite that
# introduces one has stopped re-voicing and started asserting. Checked as a
# substring of the baseline rather than by equality, so honest reformatting
# ("5.1 km" read back as "5 km") passes while a figure with no source does not.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def invented_numbers(baseline: str, voiced: str) -> list[str]:
    """Numbers in the re-voiced text that appear nowhere in the baseline."""
    source = _NUMBER.sub(lambda m: m.group(0), baseline)
    return [n for n in _NUMBER.findall(voiced) if n not in source]


# Composing from findings gives a voice licence to leave things out, which is what
# lets a terse character be terse. Two of the things it may never leave out can be
# checked without understanding the prose: the questions the report put to the
# runner (the reply options under the report answer them), and a referral to a
# clinician. Both checks are deliberately coarse. Questions are COUNTED, so a
# voice that drops the runner's question and adds a rhetorical one of its own
# still passes; a referral is a clinician noun in a sentence with a referral verb,
# so a voice that keeps the same clinician noun in a sentence that no longer
# refers still passes. They catch the plain drop, which is the common failure.
_QUESTION = re.compile(r"\?+")
_CLINICIAN = re.compile(
    r"\b(physio\w*|clinicians?|doctors?|gp|physicians?|specialists?|podiatrists?"
    r"|sports medicine|(?:health|medical) professionals?)\b",
    re.IGNORECASE,
)
_REFERRAL_VERB = re.compile(
    r"\b(?:see|seen|seeing|assess\w*|check\w*|look(?:ed)? at|book\w*|visit\w*"
    r"|consult\w*|refer\w*|get it|have it|had it)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.?!]+[.?!]*")


def _noun(word: str) -> str:
    word = word.lower()
    return "physio" if word.startswith("physio") else word.rstrip("s")


def _referrals(text: str) -> set[str]:
    """The clinician nouns this text refers the runner to, one sentence at a time."""
    found = set()
    for sentence in _SENTENCE.findall(text):
        if _REFERRAL_VERB.search(sentence):
            found |= {_noun(m.group(1)) for m in _CLINICIAN.finditer(sentence)}
    return found


def dropped_substance(baseline: str, voiced: str) -> list[str]:
    """What the voiced text left out that the baseline carried, by kind."""
    dropped = []
    if len(_QUESTION.findall(voiced)) < len(_QUESTION.findall(baseline)):
        dropped.append("question")
    referred = _referrals(baseline)
    if referred:
        named = {_noun(m.group(1)) for m in _CLINICIAN.finditer(voiced)}
        if not _referrals(voiced) and not (referred & named):
            dropped.append("clinician")
    return dropped


@dataclass(frozen=True)
class RewriteOutcome:
    """What the rewrite produced, and why, so a degrade leaves a trace.

    `text` is None whenever the baseline stands. `reason` says which of the many
    ways that happens actually happened -- some healthy (the runner is on Default),
    some defects (the model invented a figure) -- and they are indistinguishable at
    the stored report without it. It is carried onto the report's meta so the
    question "did this runner read the baseline by choice or by failure?" is
    answerable later, without log access.

    `duration_ms` is how long the model call took, set whenever one was made and
    None when the pass returned before calling. It rides the same meta for the
    same reason the reason does: how much a rewrite COSTS in wall-clock is the
    open question about extending this pass to the conversational turn, where
    latency is felt, and it has only ever been asserted rather than measured.
    A log line could not answer it -- production drops the body of every log
    record that carries a logger name -- so it is stored, not logged.

    `rejected_text` is the rewrite a mechanical check REFUSED, kept so a human
    tuning the voices can read what actually tripped the gate (#826). Nothing
    serves it and nothing stores it: a refused rewrite is not a report, and the
    runner reads the baseline exactly as before. Without it a rejection is a
    reason string with no body, and "is this check right, or is this character
    over the line?" is unanswerable -- which matters because a rejected rewrite
    silently costs the runner their voice.
    """

    text: Optional[str]
    reason: str
    duration_ms: Optional[int] = None
    rejected_text: Optional[str] = None


APPLIED = "applied"


async def revoice_report(
    *,
    baseline: str,
    voice: VoiceProfile,
    user_id,
    validate: Callable[[str], list],
    is_opener: bool = False,
) -> RewriteOutcome:
    """Re-voice one finished report, or report why the baseline stands instead.

    Every failure resolves to the baseline rather than an exception: it is already
    generated, already policy-checked and already correct, so a style pass must
    never cost the runner their coaching. `validate` is the same floor the baseline
    cleared, applied to the rewritten prose — anything it flags means the rewrite
    introduced what the baseline did not, and the baseline stands.
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
        return RewriteOutcome(None, "switched_off")
    if voice.is_default:
        return RewriteOutcome(None, "default_voice")
    if not baseline.strip():
        return RewriteOutcome(None, "no_baseline")
    if over_budget(user_id):
        logger.info("voice_rewrite skipped: over budget")
        return RewriteOutcome(None, "over_budget")

    system, user = build_rewrite_prompts(voice, baseline, is_opener=is_opener)
    started = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        client = build_client(TurnKind.VOICE, user_id)
        text, _usage = await client.generate_json_with_usage(
            system=system, user=user, max_tokens=_MAX_REWRITE_TOKENS
        )
    except Exception:  # noqa: BLE001 — a style pass never breaks a report
        logger.exception("voice_rewrite failed; serving the baseline")
        return RewriteOutcome(None, "transport_error", _elapsed_ms())

    elapsed = _elapsed_ms()

    raw = (text or "").strip()
    if not raw:
        return RewriteOutcome(None, "empty_rewrite", elapsed)
    voiced = extract_report(raw)
    if voiced is None:
        logger.warning("voice_rewrite returned no whole report; serving the baseline")
        return RewriteOutcome(None, "unparsed_rewrite", elapsed, rejected_text=raw)
    if not voiced:
        return RewriteOutcome(None, "empty_rewrite", elapsed)

    dropped = dropped_substance(baseline, voiced)
    if dropped:
        logger.warning("voice_rewrite dropped %s; serving the baseline", dropped)
        return RewriteOutcome(
            None, f"dropped:{','.join(dropped)}", elapsed, rejected_text=voiced
        )

    invented = invented_numbers(baseline, voiced)
    if invented:
        logger.warning(
            "voice_rewrite introduced unsourced numbers %s; serving the baseline",
            invented,
        )
        return RewriteOutcome(
            None,
            f"invented_numbers:{','.join(invented[:3])}",
            elapsed,
            rejected_text=voiced,
        )

    violations = validate(voiced)
    if violations:
        rules = [str(getattr(v, "rule", v)) for v in violations]
        logger.warning(
            "voice_rewrite violated policy %s; serving the baseline", rules
        )
        return RewriteOutcome(
            None, f"policy:{','.join(rules[:3])}", elapsed, rejected_text=voiced
        )

    return RewriteOutcome(voiced, APPLIED, elapsed)
