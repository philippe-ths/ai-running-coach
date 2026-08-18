"""The voice probe harness is itself checked (#828).

`run_self_test` drives the harness with scripted rewrite outcomes -- no
database, no API key, no network -- and asserts every verdict it should reach.
This file puts it in the regression suite so a change that quietly stops the
harness reporting a failure is a red build, not something discovered the next
time somebody tunes a voice.

The `make voice-probe-selftest` target runs the same function through the CLI.
"""

from __future__ import annotations

import pytest

from app.services.coach import voice_probe
from app.services.coach.voice import PRESETS


def test_the_harness_self_test_passes():
    """This carries the weight of the file. `run_self_test` pins, with live
    cases, that the harness surfaces a gate rejection, that its own cross-check
    still catches a fabricated figure the gate let through, and that its policy
    binding matches the one `service.py` uses for a real rewrite -- substitute
    the voiced text into the WHOLE report, and inherit violations the baseline
    already carried. That last pair is the defect the first run over real data
    found: a bare `CoachMessageReport(message=voiced)` is charged with rule 1,
    which reads a tail the rewrite never touches, so every voice came back
    falsely rejected."""
    ok, detail = voice_probe.run_self_test()
    assert ok, detail


def test_the_recorded_hard_cases_cover_the_three_situations():
    """The set exists to be the situations that broke voices before, so it must
    keep saying which those are and why each case is in it."""
    situations = {case.situation for case in voice_probe.HARD_CASES}
    assert len(situations) == len(voice_probe.HARD_CASES) == 3
    for case in voice_probe.HARD_CASES:
        assert case.report_id, f"{case.key} names no stored baseline"
        assert len(case.earned_by.split()) >= 20, (
            f"{case.key} does not say why it is in the set; a case that cannot "
            "justify itself cannot be retired on evidence either"
        )


def test_every_character_resolves_without_a_database():
    """A probe must be able to run a named character with no relationship row,
    which is what makes it cheap enough to use while tuning."""
    voices = voice_probe.all_named_voices()
    assert set(voices) == set(PRESETS)
    for name, voice in voices.items():
        assert not voice.is_default, f"{name} resolved to the default voice"
        assert voice.preset is not None


def test_an_unknown_voice_is_refused_by_name():
    with pytest.raises(voice_probe.ProbeError) as exc:
        voice_probe.resolve_named_voice("the_narrator")
    assert "the_narrator" in str(exc.value)
