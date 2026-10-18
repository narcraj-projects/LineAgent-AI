"""Shared graph contract and real in-memory fallback; no cloud calls here."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from scripts.generate_data import generate
from src import graph as graph_service
from src.adapters.base import AdapterUnavailable
from src.adapters.contracts import GraphAdapter
from src.adapters.neo4j_graph import Neo4jGraphAdapter
from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.graph import audit_rates, select_graph
from src.graph_models import FixHistory, GraphCounts, GraphStatus
from src.schemas import DiagnosisRequest


@pytest.fixture
def local_graph() -> NetworkXGraphAdapter:
    graph = NetworkXGraphAdapter()
    graph.seed(generate()[0])
    return graph


@pytest.mark.parametrize("alarm_code", ["SYN-ALM-001", "SYN-ALM-020"])
def test_alarm_paths_and_citations(local_graph: NetworkXGraphAdapter, alarm_code: str) -> None:
    data, benchmark = generate()
    truth = next(t for t in benchmark.alarm_truth if t.alarm_code == alarm_code)
    paths = local_graph.alarm_paths(alarm_code, truth.equipment_id)
    assert isinstance(local_graph, GraphAdapter)
    assert len(paths) == 2
    assert {p.fix_id for p in paths} == {truth.best_fix_id, *truth.decoy_fix_ids}
    best = next(p for p in paths if p.fix_id == truth.best_fix_id)
    assert best.paths == ((next(a.alarm_id for a in data.alarms if a.code == alarm_code), truth.failure_mode_id, truth.best_fix_id, truth.expected_part_numbers[0]),)
    assert len(best.history.work_orders) == 2
    assert all(p.status.provider == "networkx" and p.status.mode == "fallback" for p in paths)
    evidence = local_graph.retrieve(DiagnosisRequest(query="Synthetic graph query", alarm_code=alarm_code, equipment_id=truth.equipment_id))
    ids = [e.source_id for e in evidence]
    assert len(ids) == len(set(ids))
    assert set(truth.source_ids) <= set(ids)
    assert all(e.provider == "networkx:fallback" and e.path and e.text for e in evidence)


def test_fix_history_and_sql_reference_counts(local_graph: NetworkXGraphAdapter) -> None:
    data, _ = generate()
    history = local_graph.fix_history("SYN-EQ-001", "SYN-FIX-001")
    expected = [w for w in data.work_orders if w.equipment_id == history.equipment_id and w.fix_id == history.fix_id]
    assert tuple(expected) == history.work_orders
    assert history.outcomes.worked == sum(w.outcome == "worked" for w in expected)
    assert history.outcomes.success_rate == history.outcomes.worked / len(expected)
    assert audit_rates(local_graph, data) == 40


def test_reseeding_is_idempotent_and_rates_do_not_inflate(local_graph: NetworkXGraphAdapter) -> None:
    before = local_graph.fix_history("SYN-EQ-001", "SYN-FIX-001")
    data, _ = generate()
    assert local_graph.seed(data) == GraphCounts(nodes=173, relationships=290)
    assert local_graph.seed(data) == GraphCounts(nodes=173, relationships=290)
    assert local_graph.fix_history("SYN-EQ-001", "SYN-FIX-001") == before


def test_unknowns_and_wrong_equipment_do_not_leak_evidence(local_graph: NetworkXGraphAdapter) -> None:
    assert local_graph.alarm_paths("SYN-ALM-999") == ()
    assert local_graph.alarm_paths("SYN-ALM-001", "SYN-EQ-010") == ()
    assert local_graph.alarm_paths("' MATCH (n) DETACH DELETE n //") == ()
    assert local_graph.retrieve(DiagnosisRequest(query="Unsupported free text")) == ()
    empty = local_graph.fix_history("SYN-EQ-010", "SYN-FIX-001")
    assert empty.work_orders == () and empty.outcomes.success_rate is None


def test_history_rejects_duplicates_and_wrong_equipment(local_graph: NetworkXGraphAdapter) -> None:
    order = local_graph.fix_history("SYN-EQ-001", "SYN-FIX-001").work_orders[0]
    with pytest.raises(ValidationError, match="Duplicate"):
        FixHistory(equipment_id=order.equipment_id, fix_id=order.fix_id, work_orders=(order, order))
    with pytest.raises(ValidationError, match="mismatch"):
        FixHistory(equipment_id="SYN-EQ-010", fix_id=order.fix_id, work_orders=(order,))


def test_sql_audit_rejects_incorrect_graph_counts(local_graph: NetworkXGraphAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_graph, "fix_history", lambda eq, fix: FixHistory(equipment_id=eq, fix_id=fix))
    with pytest.raises(ValueError, match="disagrees"):
        audit_rates(local_graph, generate()[0])


def test_modes_are_visible_and_consistent() -> None:
    with pytest.raises(ValidationError, match="disagree"):
        GraphStatus(provider="networkx", mode="live", reason="Invalid test fixture")
    graph = select_graph("networkx")
    assert graph.status.mode == "fallback"
    assert graph.counts().nodes == 0  # Selection never seeds automatically.


def test_missing_configuration_automatically_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(path: Path | None) -> None:
        raise ValueError("A hypothetical secret must never appear in status")

    monkeypatch.setattr(graph_service, "load_neo4j_settings", missing)
    graph = select_graph("auto")
    assert graph.status.provider == "networkx"
    assert "hypothetical secret" not in graph.status.reason
    assert graph.seed(generate()[0]).nodes == 173
    with pytest.raises(AdapterUnavailable, match="explicit live") as exc:
        select_graph("neo4j")
    assert "hypothetical secret" not in str(exc.value)


def test_connection_failure_automatically_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_service, "load_neo4j_settings", lambda path: object())

    def unavailable(settings: object) -> None:
        raise AdapterUnavailable("Unit-test simulated connection failure")

    monkeypatch.setattr(Neo4jGraphAdapter, "connect", unavailable)
    assert select_graph().status.mode == "fallback"
    with pytest.raises(AdapterUnavailable):
        select_graph("neo4j")


def test_auto_preserves_live_adapter_when_connection_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # A test sentinel checks selection only; it is not a claimed provider result.
    sentinel = MagicMock(spec=GraphAdapter)
    monkeypatch.setattr(graph_service, "load_neo4j_settings", lambda path: object())
    monkeypatch.setattr(Neo4jGraphAdapter, "connect", lambda settings: sentinel)
    assert select_graph("auto") is sentinel
    assert select_graph("neo4j") is sentinel


def test_local_mode_does_not_load_credentials_or_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(path: Path | None) -> None:
        raise AssertionError("Local mode must not attempt provider configuration")

    monkeypatch.setattr(graph_service, "load_neo4j_settings", forbidden)
    assert isinstance(select_graph("networkx"), GraphAdapter)
    with pytest.raises(ValueError, match="Unknown"):
        select_graph("invalid")


def test_adapters_are_isolated_and_close_clears_only_local_state(local_graph: NetworkXGraphAdapter) -> None:
    other = NetworkXGraphAdapter()
    other.seed(generate()[0])
    local_graph.close()
    assert local_graph.counts().nodes == 0
    assert other.counts().nodes == 173
