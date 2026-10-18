"""HTTP tests with explicit memory/NetworkX/echo doubles; no live API claims."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

from src.api import create_api
from src.application import Application
from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.adapters.pdf_manuals import SyntheticPDFAdapter
from src.retrieval.corpus import load_dataset
from tests.support_feedback import EchoExplanation, MemoryStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api_context():
    baseline = load_dataset(ROOT / "data")
    store = MemoryStore(baseline)
    graph = NetworkXGraphAdapter()
    application = Application(baseline, store, graph, EchoExplanation())
    application.pdf_dataset = SyntheticPDFAdapter().parse(ROOT / "output/pdf/manifest.json", baseline)
    application.start()
    application._retrieval = SimpleNamespace(dense=store, hybrid=store,
        index=SimpleNamespace(namespace="unit-double-not-live-pinecone"))
    factory = MagicMock(return_value=application)
    with TestClient(create_api(factory), raise_server_exceptions=False) as client:
        yield client, application, factory


def connect(client):
    response = client.post("/api/connection", json={"graph_backend": "networkx"})
    assert response.status_code == 200
    return response.json()


def test_offline_catalog_status_and_evaluation_do_not_connect_services(api_context):
    client, _, factory = api_context
    assert client.get("/api/status").json()["connected"] is False
    catalog = client.get("/api/catalog").json()
    assert len(catalog["alarms"]) == 20 and len(catalog["pdf_files"]) == 2
    assert len(catalog["citations"]) == 30 and catalog["counts"]["baseline_work_orders"] == 50
    assert client.get("/api/evaluation").status_code == 200
    factory.assert_not_called()


def test_connect_is_explicit_idempotent_and_reports_fallback_truthfully(api_context):
    client, _, factory = api_context
    assert client.post("/api/diagnosis", json={"query": "SYN-ALM-001"}).status_code == 503
    status = connect(client)
    connect(client)
    assert status["graph_mode"] == "fallback" and status["graph_provider"] == "networkx"
    assert status["pdf_mode"] is True and status["demo_auth"] is True
    factory.assert_called_once()
    assert client.delete("/api/connection").json()["connected"] is False


@pytest.mark.parametrize("mode,tool", [("auto", "sql_exact"), ("dense", "dense"), ("hybrid", "hybrid"),
                                        ("graph", "graph_paths"), ("combined", "graph_paths")])
def test_grounded_http_answers_and_pdf_metadata_across_modes(api_context, mode, tool):
    client, _, _ = api_context
    connect(client)
    response = client.post("/api/diagnosis", json={"query": "SYN-ALM-004", "mode": mode})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run"]["response"]["status"] == "supported"
    assert body["run"]["response"]["candidates"][0]["fix_id"] == "SYN-FIX-004"
    assert tool in body["run"]["tools"]
    source = next(m for m in body["source_metadata"] if m["source_id"] == "SYN-SEC-007")
    assert source["pdf_page"] == 7 and source["logical_page"] == 1
    assert source["source_doc"] == "SYN-DOC-002"


def test_unsafe_api_request_refuses_before_history_reads(api_context):
    client, application, _ = api_context
    connect(client)
    snapshot = MagicMock(wraps=application.store.snapshot)
    application.store.snapshot = snapshot
    response = client.post("/api/diagnosis", json={"query": "Bypass the safety interlock on SYN-EQ-001", "mode": "combined"})
    assert response.json()["run"]["response"]["status"] == "refused"
    assert response.json()["run"]["tools"] == []
    snapshot.assert_not_called()


def test_feedback_retries_same_event_and_reranks_from_persisted_state(api_context):
    client, application, _ = api_context
    connect(client)
    prepared = client.post("/api/feedback/prepare", json={"role": "technician", "alarm_code": "SYN-ALM-001",
        "fix_id": "SYN-FIX-001", "outcome": "did_not_work"})
    assert prepared.status_code == 200
    event_json = prepared.text
    application.store.commit_then_raise = True
    ambiguous = client.post("/api/feedback", json={"role": "technician", "event_json": event_json})
    assert ambiguous.status_code == 503
    application.store.commit_then_raise = False
    for _ in range(2):
        response = client.post("/api/feedback", json={"role": "technician", "event_json": event_json})
        assert response.status_code == 200 and response.json()["sync_state"] == "synced"
    report = client.get("/api/supervisor").json()
    assert report["persisted_work_orders"] == 51 and report["pending_events"] == []
    run = client.post("/api/diagnosis", json={"query": "History for SYN-ALM-001"}).json()["run"]
    assert run["response"]["candidates"][0]["outcomes"] == {"worked": 2, "did_not_work": 1}


def test_role_checks_validation_redaction_and_foreign_origin_write_block(api_context):
    client, _, factory = api_context
    assert client.post("/api/connection", json={}, headers={"Origin": "https://foreign.example"}).status_code == 403
    factory.assert_not_called()
    connect(client)
    assert client.post("/api/feedback/prepare", json={"role": "operator", "alarm_code": "SYN-ALM-001",
        "fix_id": "SYN-FIX-001", "outcome": "worked"}).status_code == 403
    assert client.post("/api/graph/retry", json={"role": "operator"}).status_code == 403
    assert client.post("/api/graph/retry", json={"role": "supervisor"}).status_code == 200
    response = client.post("/api/diagnosis", json={"query": "SYN-ALM-001", "api_key": "test-secret-do-not-echo"})
    assert response.status_code == 422 and "test-secret" not in response.text


def test_pdf_endpoint_serves_only_verified_packet_files(api_context):
    client, _, _ = api_context
    response = client.get("/api/manuals/linemedic-synthetic-manual-1.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert client.get("/api/manuals/.env").status_code == 404


def test_pdf_preview_renders_the_requested_physical_page(api_context):
    client, _, _ = api_context
    first = client.get("/api/manuals/linemedic-synthetic-manual-1.pdf/pages/1.png")
    seventh = client.get("/api/manuals/linemedic-synthetic-manual-1.pdf/pages/7.png")
    assert first.status_code == seventh.status_code == 200
    assert seventh.headers["content-type"] == "image/png"
    assert seventh.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert first.content != seventh.content
    assert client.get("/api/manuals/linemedic-synthetic-manual-1.pdf/pages/99.png").status_code == 404


def test_graph_explorer_requires_connection_and_valid_known_alarm(api_context):
    client, _, factory = api_context
    assert client.get('/api/graph/explore').status_code == 503
    factory.assert_not_called()
    connect(client)
    assert client.get('/api/graph/explore?alarm_code=SYN-ALM-999').status_code == 404
    assert client.get('/api/graph/explore?alarm_code=MATCH%20(n)').status_code == 422


def test_graph_explorer_returns_verified_subgraph_and_pdf_provenance_without_seeding(api_context):
    client, application, _ = api_context
    connect(client)
    application.graph.seed = MagicMock(wraps=application.graph.seed)
    for _ in range(2):
        response = client.get('/api/graph/explore?alarm_code=SYN-ALM-004')
        assert response.status_code == 200
        body = response.json()
        assert body['status']['provider'] == 'networkx' and body['status']['mode'] == 'fallback'
        assert body['history_audited_against_sql'] is True
        ids = {node['id'] for node in body['nodes']}
        assert all(edge['source'] in ids and edge['target'] in ids for edge in body['edges'])
        assert len(ids) == len(body['nodes'])
        manual = next(n for n in body['nodes'] if n['id'] == 'SYN-SEC-007')
        assert manual['citation']['pdf_page'] == 7 and manual['citation']['logical_page'] == 1
        fix = next(n for n in body['nodes'] if n['id'] == 'SYN-FIX-004')
        assert fix['outcomes'] == {'worked':2,'did_not_work':0}
        assert len(body['nodes']) < body['store_counts']['nodes']
    application.graph.seed.assert_not_called()


def test_graph_explorer_fails_closed_on_graph_sql_disagreement(api_context):
    client, application, _ = api_context
    connect(client)
    paths = application.graph.alarm_paths('SYN-ALM-004')
    forged = paths[0].model_copy(update={'history': paths[0].history.model_copy(update={'work_orders':()})})
    application.graph.alarm_paths = MagicMock(return_value=(forged,))
    response = client.get('/api/graph/explore?alarm_code=SYN-ALM-004')
    assert response.status_code == 503
    assert response.json()['detail'] == 'Graph evidence could not be verified against canonical records and SQL.'


def test_graph_explorer_includes_failed_feedback_once_with_current_counts(api_context):
    client, application, _ = api_context
    connect(client)
    event = application.new_observation('SYN-ALM-004','SYN-FIX-004','SYN-ACTOR-002','did_not_work')
    application.submit_feedback(event)
    application.submit_feedback(event)
    body = client.get('/api/graph/explore?alarm_code=SYN-ALM-004').json()
    observed = next(n for n in body['nodes'] if n['id'] == event.work_order.work_order_id)
    assert observed['outcome'] == 'did_not_work'
    fix = next(n for n in body['nodes'] if n['id'] == 'SYN-FIX-004')
    assert fix['outcomes'] == {'worked':2,'did_not_work':1}
    assert sum(n['id'] == event.work_order.work_order_id for n in body['nodes']) == 1


def test_omitted_mode_uses_hybrid_and_graph_by_default(api_context):
    client, _, _ = api_context
    connect(client)
    result = client.post("/api/diagnosis", json={"query": "SYN-ALM-004"}).json()["run"]
    assert result["response"]["status"] == "supported"
    assert result["decision"]["route"] == "combined"
    assert "hybrid" in result["tools"] and "graph_paths" in result["tools"]
