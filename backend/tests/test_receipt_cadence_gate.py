"""The #296 receipt-cadence gate (`is_receipt_cadence`).

The cadence is gated by the COACH_RECEIPT_CADENCE flag AND a two-stage
(message-family) prompt, orthogonal to the prompt CONTENT. These pin that the
flag and the prompt id roll back independently and that the cadence is inert
under a single-shot prompt (no fuller mode to fire).
"""

import pytest

from app.core.config import settings
from app.services.coach.service import is_receipt_cadence, is_two_stage_prompt


@pytest.fixture
def cadence_on(monkeypatch):
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", True)


def test_off_by_default_even_under_two_stage_prompt(monkeypatch):
    monkeypatch.setattr(settings, "COACH_RECEIPT_CADENCE", False)
    assert is_receipt_cadence("coach_message_v6") is False


def test_on_under_two_stage_prompt(cadence_on):
    assert is_two_stage_prompt("coach_message_v6") is True
    assert is_receipt_cadence("coach_message_v6") is True


def test_inert_under_single_shot_structured_prompt(cadence_on):
    # No fuller mode exists for the structured family, so the flag must not engage.
    assert is_receipt_cadence("coach_report_v10") is False


def test_inert_under_single_shot_message_prompt(cadence_on):
    # coach_message_v1 is message-family but single-shot (not two-stage), so it has
    # no opener/fuller split — the receipt cadence must stay inert.
    assert is_receipt_cadence("coach_message_v1") is False
