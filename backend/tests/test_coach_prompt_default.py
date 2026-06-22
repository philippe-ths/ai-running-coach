"""#424 — the active coach prompt default must be feature-bearing, and an inert
active prompt must surface loudly at startup instead of silently disabling the
whole prompt-gated coaching surface (voice/corpus/stance/training-load/
user-materials/volume).
"""

import logging

from app.core.config import settings
from app.core.observability import warn_if_coach_prompt_inert
from app.services.coach.prompt_features import (
    RUNNER_FACING_FEATURES,
    carries_runner_facing_features,
    features_for,
)


def test_default_prompt_is_feature_bearing():
    # A deploy that forgets the COACH_PROMPT_ID env override must still land on a
    # prompt that activates the runner-facing coaching features, not a legacy id.
    assert settings.COACH_PROMPT_ID == "coach_message_v8"
    assert carries_runner_facing_features(settings.COACH_PROMPT_ID)


def test_carries_runner_facing_features_predicate():
    # Feature-bearing message prompts pass.
    for pid in ("coach_message_v3", "coach_message_v8", "coach_message_v9"):
        assert carries_runner_facing_features(pid), pid
    # Legacy, two-stage-only, unknown, and None all read as inert.
    for pid in ("coach_report_v10", "coach_report_v1", "coach_message_v1",
                "coach_message_v2", "unknown_prompt_xyz", None):
        assert not carries_runner_facing_features(pid), pid


def test_runner_facing_set_excludes_two_stage():
    # The cadence shape (two-stage) is an operational choice, not the runner-visible
    # coaching surface #424 guards; a two-stage-only prompt must not read as
    # feature-bearing.
    assert not (features_for("coach_message_v2") & RUNNER_FACING_FEATURES)


def test_inert_prompt_warns_at_startup(monkeypatch, caplog):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")
    with caplog.at_level(logging.WARNING):
        warn_if_coach_prompt_inert()
    records = [r for r in caplog.records if "coach_prompt_inert" in r.getMessage()]
    assert records, "expected a loud warning for an inert active prompt"
    assert records[0].levelno == logging.WARNING
    assert getattr(records[0], "coach_prompt_id", None) == "coach_report_v10"


def test_feature_bearing_prompt_does_not_warn(monkeypatch, caplog):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v8")
    with caplog.at_level(logging.WARNING):
        warn_if_coach_prompt_inert()
    assert not [r for r in caplog.records if "coach_prompt_inert" in r.getMessage()]
