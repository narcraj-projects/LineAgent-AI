"""Opt-in LIVE tests; these never substitute the fallback for Neo4j results."""

import os
from pathlib import Path
from collections.abc import Iterator

import pytest

from scripts.generate_data import generate
from src.adapters.neo4j_graph import Neo4jGraphAdapter
from src.adapters.neo4j_settings import load_neo4j_settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_graph() -> Iterator[Neo4jGraphAdapter]:
    if os.environ.get("LINEMEDIC_RUN_LIVE_GRAPH_TESTS") != "1":
        pytest.skip("Live graph seeding not requested; set LINEMEDIC_RUN_LIVE_GRAPH_TESTS=1 on a dedicated synthetic database.")
    root = Path(__file__).resolve().parents[1]
    try:
        settings = load_neo4j_settings(root / ".env")
    except Exception:
        pytest.skip("Neo4j credentials absent or invalid; no live graph test performed.")
    graph = Neo4jGraphAdapter.connect(settings)  # Configured connectivity failure is a FAIL.
    try:
        data, _ = generate()
        first = graph.seed(data)
        assert graph.seed(data) == first
        yield graph
    finally:
        graph.close()


@pytest.mark.parametrize("alarm_code", ["SYN-ALM-001", "SYN-ALM-020"])
def test_live_alarm_code(live_graph: Neo4jGraphAdapter, alarm_code: str) -> None:
    truth = next(t for t in generate()[1].alarm_truth if t.alarm_code == alarm_code)
    paths = live_graph.alarm_paths(alarm_code, truth.equipment_id)
    assert {p.fix_id for p in paths} == {truth.best_fix_id, *truth.decoy_fix_ids}
    assert all(p.status.provider == "neo4j" and p.status.mode == "live" for p in paths)
    best = next(p for p in paths if p.fix_id == truth.best_fix_id)
    assert best.part_numbers == truth.expected_part_numbers
    assert best.paths and best.manuals


def test_live_fix_history(live_graph: Neo4jGraphAdapter) -> None:
    data, _ = generate()
    history = live_graph.fix_history("SYN-EQ-001", "SYN-FIX-001")
    expected = tuple(w for w in data.work_orders if w.equipment_id == history.equipment_id and w.fix_id == history.fix_id)
    assert history.work_orders == expected
    assert history.outcomes.success_rate == sum(w.outcome == "worked" for w in expected) / len(expected)
