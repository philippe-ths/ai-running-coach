"""M2 — the memory writer is enqueued only under a memory-aware prompt + the switch.

`_fire_learning_loop` fires the background memory update pass only when the active
prompt carries `PromptFeature.MEMORY` (i.e. coach_message_v13 once M3 registers it)
AND `COACH_MEMORY_ENABLED` is on. Under v12 / flag off there is no enqueue and no
write — so merging M2 changes nothing in prod (prod runs v12).
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from app.core.config import settings
from app.services.coach import service
from app.services.coach import prompt_features
from app.services.coach.prompt_features import PromptFeature

_MEM_PROMPT = "coach_message_mem_test"


def _fire(prompt_id):
    service._fire_learning_loop(
        None, SimpleNamespace(user_id=uuid.uuid4()), None, prompt_id
    )


def test_no_enqueue_under_the_live_v12_prompt(monkeypatch):
    spy = Mock()
    monkeypatch.setattr(service, "enqueue_memory_update", spy)
    _fire("coach_message_v12")
    spy.assert_not_called()


def test_enqueue_under_a_memory_aware_prompt(monkeypatch):
    monkeypatch.setitem(
        prompt_features.PROMPT_FEATURES, _MEM_PROMPT, frozenset({PromptFeature.MEMORY})
    )
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", True)
    spy = Mock()
    monkeypatch.setattr(service, "enqueue_memory_update", spy)

    _fire(_MEM_PROMPT)

    spy.assert_called_once()


def test_kill_switch_off_suppresses_enqueue_even_under_a_memory_prompt(monkeypatch):
    monkeypatch.setitem(
        prompt_features.PROMPT_FEATURES, _MEM_PROMPT, frozenset({PromptFeature.MEMORY})
    )
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", False)
    spy = Mock()
    monkeypatch.setattr(service, "enqueue_memory_update", spy)

    _fire(_MEM_PROMPT)

    spy.assert_not_called()
