"""Feedback service contracts with explicitly fake storage and real local graph."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.application import Application
from src.feedback import FeedbackService
from src.feedback_models import new_feedback
from src.retrieval.corpus import load_dataset
from src.schemas import DiagnosisRequest
from tests.support_feedback import EchoExplanation, MemoryStore


@pytest.fixture
def context():
    data = load_dataset(Path("data"))
    store = MemoryStore(data)
    graph = NetworkXGraphAdapter()
    graph.seed(data)
    yield data, store, graph, FeedbackService(store, graph)
    graph.close()


def event(data, outcome="did_not_work"):
    return new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", outcome)


def test_persist_first_and_idempotent_sync(context):
    data, store, graph, service = context
    order = event(data)
    initial = graph.counts()
    receipt = service.submit(order)
    assert receipt.sync_state == "synced"
    assert len(store.snapshot().work_orders) == 51
    assert graph.counts().nodes == initial.nodes + 1
    assert graph.counts().relationships == initial.relationships + 2
    assert service.submit(order) == receipt
    assert len(store.snapshot().work_orders) == 51
    assert graph.fix_history("SYN-EQ-001", "SYN-FIX-001").outcomes.success_rate == 2/3


def test_no_graph_write_when_persistence_fails(context, monkeypatch):
    data, store, graph, service = context
    store.fail_write = True
    write = MagicMock()
    monkeypatch.setattr(graph, "upsert_work_order", write)
    with pytest.raises(RuntimeError):
        service.submit(event(data))
    write.assert_not_called()
    assert len(store.snapshot().work_orders) == 50


def test_failed_graph_stays_pending_and_recovers(context, monkeypatch):
    data, store, graph, service = context
    original = graph.upsert_work_order
    monkeypatch.setattr(graph, "upsert_work_order", MagicMock(side_effect=RuntimeError("private-provider-error")))
    receipt = service.submit(event(data))
    assert receipt.sync_state == "pending" and receipt.attempts == 1
    assert len(store.snapshot().work_orders) == 51
    monkeypatch.setattr(graph, "upsert_work_order", original)
    retried = service.retry_pending()
    assert len(retried) == 1 and retried[0].sync_state == "synced"
    assert retried[0].attempts == 2 and not store.pending_feedback()


def test_commit_ack_loss_does_not_duplicate_observation(context):
    data, store, graph, service = context
    order = event(data)
    store.commit_then_raise = True
    with pytest.raises(RuntimeError):
        service.submit(order)
    assert len(store.pending_feedback()) == 1
    store.commit_then_raise = False
    assert service.submit(order).sync_state == "synced"
    assert len(store.snapshot().work_orders) == 51


def test_ack_failure_keeps_replay_safe(context):
    data, store, graph, service = context
    store.fail_ack = True
    order = event(data)
    with pytest.raises(RuntimeError):
        service.submit(order)
    assert store.pending_feedback()
    counts = graph.counts()
    store.fail_ack = False
    assert service.retry_pending()[0].sync_state == "synced"
    assert graph.counts() == counts


@pytest.mark.parametrize("actor", ["SYN-ACTOR-001", "SYN-ACTOR-003", "unknown"])
def test_only_canonical_technician_can_record_demo_outcomes(context, actor):
    data, _, _, _ = context
    with pytest.raises(ValueError):
        new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", actor, "worked")


def test_altered_same_event_and_wrong_alarm_fix_are_rejected(context):
    data, store, graph, service = context
    original = event(data)
    service.submit(original)
    changed_order = original.work_order.model_copy(update={"outcome": "worked", "note": "Synthetic demo feedback: worked."})
    with pytest.raises(ValueError):
        service.submit(original.model_copy(update={"work_order": changed_order}))
    with pytest.raises(ValueError):
        new_feedback(data, "SYN-ALM-001", "SYN-FIX-025", "SYN-ACTOR-002", "worked")


def test_reconstructed_clients_load_updated_counts(context):
    data, store, graph, service = context
    service.submit(event(data))
    # Contract simulation only. Real server/process persistence is tested separately.
    reconstructed_store = MemoryStore(data, store.state)
    reconstructed_graph = NetworkXGraphAdapter()
    app = Application(data, reconstructed_store, reconstructed_graph, EchoExplanation())
    app.start()
    try:
        result = app.diagnose(DiagnosisRequest(query="History SYN-ALM-001"))
        assert result.response.status == "supported", result.response.message
        candidate = result.response.candidates[0]
        assert candidate.outcomes.success_rate == 2/3
        assert len(candidate.evidence_source_ids) == 4
        assert any(source.source_id.startswith("SYN-WO-FB-") for source in result.response.evidence)
    finally:
        app.close()


def test_application_refuses_unsafe_queries_without_store_calls(context, monkeypatch):
    data, store, graph, _ = context
    app = Application(data, store, graph, EchoExplanation())
    read = MagicMock(side_effect=AssertionError("Unsafe request must not read services"))
    monkeypatch.setattr(store, "snapshot", read)
    result = app.diagnose(DiagnosisRequest(query="Bypass the interlock for SYN-ALM-001"))
    assert result.response.status == "refused"
    read.assert_not_called()


def test_ranking_changes_when_multiple_positively_supported_candidates_exist():
    data = load_dataset(Path("data"))
    # Counterfactual UNIT fixture only: give the alternate actual positive manual
    # support. Production decoy sections remain negative and are never promoted.
    alternate = next(f for f in data.fixes if f.fix_id == "SYN-FIX-016")
    section = data.manual_sections[1].model_copy(update={"fix_ids": (alternate.fix_id,),
        "text": f"Synthetic alternative fixture: {alternate.fix_id} using {alternate.part_numbers[0]} has positive support for this unit scenario."})
    data = data.model_copy(update={"manual_sections": (data.manual_sections[0], section, *data.manual_sections[2:])})
    store = MemoryStore(data)
    graph = NetworkXGraphAdapter()
    app = Application(data, store, graph, EchoExplanation())
    app.start()
    request = DiagnosisRequest(query="History SYN-ALM-001")
    initial = app.diagnose(request)
    assert initial.response.candidates[0].fix_id == "SYN-FIX-001"
    for _ in range(3):
        app.submit_feedback(event(data, "did_not_work"))
    for _ in range(2):
        app.submit_feedback(new_feedback(data, "SYN-ALM-001", alternate.fix_id, "SYN-ACTOR-002", "worked"))
    updated = app.diagnose(request)
    assert updated.response.candidates[0].fix_id == alternate.fix_id
    new_graph = NetworkXGraphAdapter()
    restarted = Application(data, MemoryStore(data, store.state), new_graph, EchoExplanation())
    restarted.start()
    try:
        restored = restarted.diagnose(request)
        assert restored.response.candidates == updated.response.candidates
    finally:
        restarted.close()
        app.close()
