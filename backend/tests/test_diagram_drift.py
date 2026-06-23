"""CI guard: the ai-flow-graph data-flow diagram must not silently drift from the code.

The diagram topology (docs/diagrams/flow-nodes.js) is hand-authored, so it desyncs as the
context pack and the DerivedMetric model evolve. Twice a real data path reached the LLM with
no node in the graph (stream_view, then block / user_materials / efficiency_analysis), caught
only by eye. This test runs the deterministic drift guard so the build — not a human — catches
the next desync.

The guard (docs/diagrams/check_diagram_drift.py) checks the two high-value, mechanisable drift
classes: every CoachContextPack section must have a p_* node, and every DerivedMetric column
must be covered by the generator's _DM_FIELDS and the FATE_DERIVED map. It does NOT check edge
correctness (which still relies on the periodic audit).
"""
import sys
from pathlib import Path

_DIAGRAMS = Path(__file__).resolve().parents[2] / "docs" / "diagrams"
sys.path.insert(0, str(_DIAGRAMS))

from check_diagram_drift import check_drift  # noqa: E402


def test_flow_graph_diagram_in_sync_with_code():
    problems = check_drift()
    assert not problems, (
        "ai-flow-graph diagram has drifted from the code. Update flow-nodes.js / "
        "generate_flow_nodes_data.py (or the guard's allowlist if intentional):\n\n"
        + "\n".join(f"  - {p}" for p in problems)
    )
