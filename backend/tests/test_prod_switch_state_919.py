"""#919: what production actually runs is stated once, and cannot drift silently.

`project-context.md` is loaded into every session, so a wrong sentence in it is
not a documentation nicety — it is a false premise handed to whoever reasons
about the coach next. It said the `COACH_*_ENABLED` switches "all default True =
current behaviour", which is wrong twice over: two of them default False in code,
and production runs eleven of them OFF. A plan for the report eval was built on
that sentence and had to be corrected mid-flight (#655), and the same trap was
waiting for anyone reasoning about continuity, longitudinal or user materials.

The fix could not be a pointer alone. A reader of the context should learn what
the coach actually receives without opening a config file, which means the fact
has to be stated where they are — and a stated fact is a copy, and a copy drifts.
So it is stated AND pinned: this file recomputes the effective production state
the way the app itself resolves it, and fails when the prose disagrees.

`backend/.env.example`'s prod-parity block is the source of truth, the same block
`make diagram-check` already pins both diagrams against, so nothing here
introduces a second authority. It is checked against the deployed environment by
hand rather than by CI (CI has no Railway credentials, and should not); it was
last verified equal on 2026-08-20, against both the `web` and `worker` services.

The EFFECTIVE state is what matters, not the declared one: a switch the block
does not declare runs at its code default, and two of those defaults are False.
Reading only the declared `false` lines would report nine and miss two inputs the
coach genuinely does not get.
"""

import re
from pathlib import Path

from app.core.config import Settings

_REPO = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO / "backend" / ".env.example"
_PROJECT_CONTEXT = _REPO / "project-context.md"

# The sentence in project-context.md this file guards. Matched by its opening
# words rather than by line number, so reordering the document cannot silently
# disable the check.
_CLAIM_PREFIX = "Eleven coach inputs are OFF in the deployed environment:"


def _switch_names() -> list[str]:
    return sorted(
        n
        for n in Settings.model_fields
        if n.startswith("COACH_") and n.endswith("_ENABLED")
    )


def _declared_in_env_example() -> dict[str, bool]:
    text = _ENV_EXAMPLE.read_text()
    return {
        k: v == "true"
        for k, v in re.findall(
            r"^(COACH_[A-Z0-9_]+_ENABLED)=(true|false)", text, re.M
        )
    }


def _effective_prod_state() -> dict[str, bool]:
    """How the running app resolves each switch: the declared value, else the
    code default. This is the one place the two sources are combined."""
    declared = _declared_in_env_example()
    return {
        name: declared.get(name, bool(Settings.model_fields[name].default))
        for name in _switch_names()
    }


def _claimed_off() -> list[str]:
    """The switches project-context.md says are off in production."""
    text = _PROJECT_CONTEXT.read_text()
    start = text.index(_CLAIM_PREFIX) + len(_CLAIM_PREFIX)
    sentence = text[start : text.index(".", start)]
    return sorted(re.findall(r"`(COACH_[A-Z0-9_]+_ENABLED)`", sentence))


def test_the_context_names_exactly_the_inputs_production_has_switched_off():
    """The load-bearing check: the prose and the config agree, or the build fails."""
    actual_off = sorted(n for n, on in _effective_prod_state().items() if not on)
    assert _claimed_off() == actual_off, (
        "project-context.md's list of coach inputs that are OFF in production "
        "no longer matches backend/.env.example plus the code defaults.\n"
        f"  context says: {_claimed_off()}\n"
        f"  actually off: {actual_off}\n"
        "Update the sentence beginning "
        f"'{_CLAIM_PREFIX}' — a wrong list here becomes a wrong premise in the "
        "next session, which is the failure #919 records."
    )


def test_the_count_in_the_prose_matches_the_list_in_the_prose():
    """A list that grows while the number in front of it does not is the drift
    a reader is least likely to notice, because the number is what they read."""
    stated = len(_claimed_off())
    assert f"{_number_word(stated)} coach inputs are OFF" in _PROJECT_CONTEXT.read_text(), (
        f"project-context.md lists {stated} switched-off coach inputs but does "
        "not say so in words directly before the list"
    )


def test_the_two_switches_that_default_off_are_still_the_ones_named():
    """The context tells a reader that an undeclared switch runs at its code
    default and names the two that are False. That claim is only useful while it
    is true, and a new switch defaulting False would quietly falsify it."""
    default_off = sorted(
        n for n in _switch_names() if not bool(Settings.model_fields[n].default)
    )
    assert default_off == [
        "COACH_ADHERENCE_ENABLED",
        "COACH_PRIOR_REPORTS_ENABLED",
    ], (
        f"the COACH_*_ENABLED switches defaulting False are now {default_off}; "
        "project-context.md names them explicitly so that 'absent' never reads "
        "as 'on', and that sentence needs updating with them"
    )


def test_every_switch_the_env_example_declares_is_a_real_setting():
    """A typo in the prod-parity block would silently declare nothing at all —
    the deployed service would run the code default while the block, the
    diagrams and this guard all agreed about a name that does not exist."""
    unknown = sorted(set(_declared_in_env_example()) - set(_switch_names()))
    assert not unknown, (
        f"backend/.env.example declares {unknown}, which is not a "
        "COACH_*_ENABLED setting; it configures nothing and misleads every "
        "reader of the prod-parity block"
    )


def _number_word(n: int) -> str:
    words = {
        9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen",
        14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen",
    }
    return words.get(n, str(n))
