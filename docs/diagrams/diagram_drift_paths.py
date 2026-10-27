"""The files the diagram drift guard (check_diagram_drift.py) reads.
"""
from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parents[1] / "backend"
_FLOW_NODES = _HERE / "flow-nodes.js"
_CHAT_NODES = _HERE / "coach-chat-nodes.js"
_GENERATOR = _HERE / "generate_flow_nodes_data.py"
_CHAT_GENERATOR = _HERE / "generate_chat_flow_data.py"
_PACK_SHAPE = _HERE / "pack-shape.json"
_ENV_EXAMPLE = _BACKEND / ".env.example"
_THREAD_TURN = _BACKEND / "app" / "services" / "coach" / "thread_turn.py"
_SCREEN_CONTEXT = _BACKEND / "app" / "services" / "coach" / "screen_context.py"

# The generators whose calls into backend/app must still bind (check 5). Both build their
# capture by importing the real pipeline, so both rot the same way; generate_example_chats.py
# is excluded because it drives the live API rather than importing the backend.
_GENERATORS = (_GENERATOR, _CHAT_GENERATOR)
