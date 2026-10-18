"""Evaluation contract tests with labeled doubles, not live benchmark results."""

from pathlib import Path
from datetime import datetime, timezone
import sys
from types import SimpleNamespace

import httpx
import pytest

from evals.models import EvaluationRow
from evals.report import write_reports
from evals.run_suite import load_questions
from evals.scoring import aggregate, score
from src.adapters.evaluation import FrozenGraph, HTTPObservations, LangSmithEvaluationTrace
from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.adapters.sqlite_evidence import SQLiteEvidenceAdapter
from src.agent import DiagnosisAgent
from src.agent_models import Explanation
from src.feedback_models import new_feedback
from src.retrieval.corpus import load_dataset
from src.router import select_route
from src.schemas import DiagnosisRequest


class Echo:
    provider = "test-double:not-live"

    def explain(self, facts, evidence):
        return Explanation(facts=facts)


@pytest.fixture
def fixture():
    data = load_dataset(Path("data"))
    sql = SQLiteEvidenceAdapter(data)
    graph = NetworkXGraphAdapter()
    graph.seed(data)
    yield data, sql, graph
    sql.close()
    graph.close()


@pytest.mark.parametrize("mode", ["dense", "hybrid", "graph", "combined"])
def test_ablation_executes_selected_tools_and_preserves_refusal(fixture, mode):
    data, sql, graph = fixture
    agent = DiagnosisAgent(data, sql, Echo(), sql, sql, graph)
    query = DiagnosisRequest(query=load_questions()[0].question)
    run = agent.run(query, evaluation_mode=mode)
    assert run.response.status == "supported"
    assert ("graph_paths" in run.tools) == (mode in ("graph", "combined"))
    assert ("dense" in run.tools) == (mode == "dense")
    assert ("hybrid" in run.tools) == (mode in ("hybrid", "combined"))
    refused = agent.run(DiagnosisRequest(query="Bypass the safety interlock on SYN-EQ-001"), evaluation_mode=mode)
    assert refused.response.status == "refused" and not refused.tools
    # The application/router default is unchanged.
    assert agent.run(query).decision.route == "sql"


def test_scoring_rejects_safe_but_missing_answers_and_keeps_denominators(fixture):
    data, sql, graph = fixture
    questions = load_questions()
    req = DiagnosisRequest(query=questions[0].question)
    natural = select_route(req)
    # No dense adapter: actual service-unavailable refusal, not a correct answer.
    run = DiagnosisAgent(data, sql, Echo()).run(req, evaluation_mode="dense")
    correct = score(questions[0], run, natural, data, ())
    assert not correct.top1_fix and correct.retrieval_recall_at_5 == 0
    assert correct.failures
    row = EvaluationRow(question_id=questions[0].question_id, mode="dense", category="single_hop",
        language="en", status="FAIL", run=run, correctness=correct, latency_ms=20)
    skipped = row.model_copy(update={"question_id": "SYN-Q-002", "status": "NOT_RUN", "skip_reason": "missing key"})
    result = aggregate([row, skipped], "dense")
    assert result["top1_fix"] == {"correct": 0, "n": 1, "rate": 0}
    assert result["citation_validity_answered"]["n"] == 0
    assert result["skipped"] == 1 and result["cost_usd_total"] is None


def test_correct_grounded_answer_and_independent_routing_label(fixture):
    data, sql, graph = fixture
    question = load_questions()[0]
    req = DiagnosisRequest(query=question.question)
    natural = select_route(req)
    run = DiagnosisAgent(data, sql, Echo(), graph=graph).run(req, evaluation_mode="graph")
    correct = score(question, run, natural, data, None)
    assert not correct.failures and correct.top1_fix and correct.citations_valid and correct.rates_valid
    assert correct.has_graph_path and correct.retrieval_recall_at_5 is None
    wrong = natural.model_copy(update={"route": "dense"})
    assert not score(question, run, wrong, data, None).routing_correct


def test_frozen_projection_does_not_delete_live_feedback_or_hide_changed_baseline(fixture):
    data, _, graph = fixture
    event = new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", "did_not_work")
    graph.upsert_work_order(event.work_order)
    frozen = FrozenGraph(graph, data)
    paths = frozen.alarm_paths("SYN-ALM-001", "SYN-EQ-001")
    assert next(p for p in paths if p.fix_id == "SYN-FIX-001").history.outcomes.did_not_work == 0
    assert graph.fix_history("SYN-EQ-001", "SYN-FIX-001").outcomes.did_not_work == 1
    assert event.work_order.work_order_id in frozen.excluded_ids
    with pytest.raises(ValueError, match="read-only"):
        frozen.upsert_work_order(event.work_order)
    class MissingHistory:
        status = graph.status
        def fix_history(self, eq, fix):
            actual = graph.fix_history(eq, fix)
            return actual.model_copy(update={"work_orders": ()})
    with pytest.raises(ValueError, match="baseline history"):
        FrozenGraph(MissingHistory(), data).fix_history("SYN-EQ-001", "SYN-FIX-001")


def test_http_telemetry_does_not_save_keys_or_error_body():
    observed = HTTPObservations()
    def handler(request):
        return httpx.Response(401, text="provider-secret", headers={"x-secret": "another-secret"})
    client = httpx.Client(transport=httpx.MockTransport(handler), event_hooks={"response": [observed.observe]})
    client.post("https://api.fireworks.ai/inference/v1/embeddings", headers={"Authorization": "Bearer key-secret"})
    assert observed.records == [{"endpoint": "/inference/v1/embeddings", "http_status": 401}]
    client.close()


def test_langsmith_disabled_does_not_import_or_connect():
    tracer = LangSmithEvaluationTrace({"LANGSMITH_TRACING": "false"})
    assert tracer.client is None and tracer.status.startswith("NOT_RUN")


def test_langsmith_uses_explicit_workspace_and_synchronous_synthetic_payload(monkeypatch):
    called = {}
    class FakeClient:
        def __init__(self, **kwargs):
            called["config"] = kwargs
        def create_run(self, **kwargs):
            called["run"] = kwargs
        def close(self):
            called["closed"] = True
    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(Client=FakeClient))
    tracer = LangSmithEvaluationTrace({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "fake-key",
        "LANGSMITH_WORKSPACE_ID": "fake-workspace", "LANGSMITH_PROJECT": "synthetic-test"})
    now = datetime.now(timezone.utc)
    result = tracer.record({"question": "Synthetic alarm?"}, {"status": "PASS"}, now, now)
    assert result.startswith("PASS")
    assert called["config"]["workspace_id"] == "fake-workspace"
    assert called["config"]["auto_batch_tracing"] is False
    assert called["run"]["outputs"] == {"status": "PASS"}
    assert "fake-key" not in str(called["run"])
    tracer.close()
    assert called["closed"]


def test_langsmith_upload_failure_is_redacted(monkeypatch):
    class FailedClient:
        def __init__(self, **kwargs):
            pass
        def create_run(self, **kwargs):
            raise ValueError("remote-secret")
    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(Client=FailedClient))
    tracer = LangSmithEvaluationTrace({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "fake-key"})
    now = datetime.now(timezone.utc)
    result = tracer.record({}, {}, now, now)
    assert result.startswith("FAIL") and "remote-secret" not in result


def test_usage_is_returned_tokens_without_manufactured_cost(fixture):
    data, sql, graph = fixture
    question = load_questions()[0]
    req = DiagnosisRequest(query=question.question)
    run = DiagnosisAgent(data, sql, Echo(), graph=graph).run(req, evaluation_mode="graph")
    correctness = score(question, run, select_route(req), data, None)
    row = EvaluationRow(question_id=question.question_id, category="single_hop", language="en", mode="graph",
        status="PASS", run=run, correctness=correctness, provider_responses=({"endpoint": "/chat",
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}},))
    summary = aggregate([row], "graph")
    assert summary["observed_fireworks_usage"]["/chat"]["total_tokens"] == 13
    assert summary["cost_usd_total"] is None and summary["cost_unknown_queries"] == 1


def test_report_rejects_missing_or_duplicate_cells(tmp_path):
    with pytest.raises(ValueError, match="120 frozen cells"):
        write_reports(tmp_path, [])
