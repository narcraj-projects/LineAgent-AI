"""Neo4j transport-unit tests use mocks; they do not verify live Cypher semantics."""

import ssl
from unittest.mock import MagicMock

import certifi
import pytest
from pydantic import SecretStr

from scripts.generate_data import generate
from src.adapters.base import AdapterUnavailable
from src.adapters.cypher import CONSTRAINTS, TEMPLATES
from src.adapters.graph_common import NAMESPACE
from src.adapters.neo4j_graph import Neo4jGraphAdapter
from src.adapters.neo4j_settings import Neo4jSettings
from src.adapters.networkx_graph import NetworkXGraphAdapter


def test_queries_are_parameterized_and_non_allowlisted_queries_rejected() -> None:
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    tx = MagicMock()
    session.execute_read.side_effect = lambda fn: fn(tx)
    tx.run.return_value.data.return_value = []
    adapter = Neo4jGraphAdapter(driver, "synthetic")
    injected = "SYN-ALM-001' MATCH (n) DETACH DELETE n //"
    assert adapter.alarm_paths(injected) == ()
    query, parameters = tx.run.call_args.args
    assert query == TEMPLATES["alarm_paths"] and injected not in query
    assert parameters["alarm_code"] == injected and parameters["namespace"] == NAMESPACE
    with pytest.raises(ValueError, match="allowlisted"):
        adapter._read("MATCH (n) RETURN n", {})
    assert "count(DISTINCT w)" in query


def test_seed_uses_constraints_and_merge_batches_not_raw_values() -> None:
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    tx = MagicMock()
    session.execute_write.side_effect = lambda fn: fn(tx)

    def merged(query: str, **kwargs: object) -> MagicMock:
        result = MagicMock()
        assert "MERGE" in query and "$rows" in query
        assert kwargs["namespace"] == NAMESPACE
        result.single.return_value = {"merged": len(kwargs["rows"])}
        return result

    tx.run.side_effect = merged
    adapter = Neo4jGraphAdapter(driver, "synthetic")
    # Counts are irrelevant to this transport-only assertion.
    adapter.counts = MagicMock()
    adapter.seed(generate()[0])
    assert len(CONSTRAINTS) == 10
    assert [call.args[0] for call in session.run.call_args_list] == list(CONSTRAINTS)
    assert all("IF NOT EXISTS" in query for query in CONSTRAINTS)
    assert tx.run.call_count == 16  # Eight node groups and eight relation types.
    assert sum(len(c.kwargs["rows"]) for c in tx.run.call_args_list) == 173 + 290


def test_driver_error_is_sanitized_and_never_switches_backend() -> None:
    driver = MagicMock()
    driver.session.side_effect = RuntimeError("fixture-password-never-print")
    adapter = Neo4jGraphAdapter(driver, "synthetic")
    with pytest.raises(AdapterUnavailable) as exc:
        adapter.fix_history("SYN-EQ-001", "SYN-FIX-001")
    assert "fixture-password" not in str(exc.value)
    assert adapter.status.mode == "live"  # Failure surfaces; no silent mid-read fallback.


def test_feedback_merge_is_parameterized_and_idempotent_transport() -> None:
    from src.feedback_models import new_feedback

    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    tx = MagicMock()
    session.execute_write.side_effect = lambda fn: fn(tx)
    tx.run.return_value.single.return_value = {"merged": 1}
    event = new_feedback(generate()[0], "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", "worked")
    adapter = Neo4jGraphAdapter(driver, "synthetic")
    adapter.upsert_work_order(event.work_order)
    adapter.upsert_work_order(event.work_order)
    query = tx.run.call_args.args[0]
    params = tx.run.call_args.kwargs
    assert query == TEMPLATES["upsert_work_order"]
    assert event.work_order.work_order_id not in query
    assert params["work_order_id"] == event.work_order.work_order_id
    assert params["payload_json"] == event.work_order.model_dump_json()
    assert "MERGE (w)-[:ON]->(e)" in query and "MERGE (w)-[:APPLIED]->(f)" in query
    tx.run.return_value.single.return_value = {"merged": 0}
    with pytest.raises(AdapterUnavailable):
        adapter.upsert_work_order(event.work_order)


def test_connection_keeps_tls_and_hostname_checks_with_local_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path
    from neo4j import GraphDatabase

    driver = MagicMock()
    driver.session.return_value.__enter__.return_value.run.return_value.single.return_value = {"ok": 1}
    factory = MagicMock(return_value=driver)
    monkeypatch.setattr(GraphDatabase, "driver", factory)
    settings = Neo4jSettings(uri=SecretStr("neo4j+s://synthetic.invalid"), username=SecretStr("fixture"), password=SecretStr("fixture"), ca_file=Path(certifi.where()))
    adapter = Neo4jGraphAdapter.connect(settings)
    assert adapter.status.mode == "live"
    context = factory.call_args.kwargs["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert factory.call_args.kwargs["encrypted"] is True
    assert factory.call_args.args[0].startswith("neo4j://")


@pytest.mark.parametrize("bad_count", [False, True])
def test_typed_path_conversion_and_inconsistent_aggregates(monkeypatch: pytest.MonkeyPatch, bad_count: bool) -> None:
    local = NetworkXGraphAdapter()
    local.seed(generate()[0])
    path = local.alarm_paths("SYN-ALM-001")[0]
    row = {
        "alarm_code": path.alarm_code, "alarm_id": path.alarm_id,
        "equipment_id": path.equipment_id, "failure_mode_id": path.failure_mode_id, "fix_id": path.fix_id,
        "parts": list(path.part_numbers), "base_path": list(path.paths[0][:3]),
        "history": [w.model_dump_json() for w in path.history.work_orders],
        "attempts": len(path.history.work_orders) + int(bad_count), "worked": path.history.outcomes.worked,
        "manuals": [m.model_dump_json() for m in path.manuals],
    }
    adapter = Neo4jGraphAdapter(MagicMock(), "synthetic")
    monkeypatch.setattr(adapter, "_read", lambda template, params: [row])
    if bad_count:
        with pytest.raises(ValueError, match="aggregate counts"):
            adapter.alarm_paths(path.alarm_code)
    else:
        actual = adapter.alarm_paths(path.alarm_code)[0]
        assert actual.model_dump(exclude={"status"}) == path.model_dump(exclude={"status"})
