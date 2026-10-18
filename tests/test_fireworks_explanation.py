"""Mock transport contract checks; these tests do not claim live API success."""

import json
from pathlib import Path

import httpx
import pytest

from src.adapters.base import AdapterUnavailable
from src.adapters.fireworks_explanation import ENDPOINT, FireworksExplanationAdapter
from src.adapters.sqlite_evidence import SQLiteEvidenceAdapter
from src.agent import DiagnosisAgent
from src.agent_models import Explanation
from src.diagnosis import prepare_facts
from src.retrieval.corpus import load_dataset
from src.schemas import DiagnosisRequest

MODEL = "accounts/fireworks/models/kimi-k2p5"


@pytest.fixture
def grounded():
    data = load_dataset(Path("data"))
    sql = SQLiteEvidenceAdapter(data)
    req = DiagnosisRequest(query="SYN-ALM-001", alarm_code="SYN-ALM-001", equipment_id="SYN-EQ-001")
    facts, sources, _ = prepare_facts(data, req, sql.retrieve(req), sql)
    yield data, sql, req, facts, sources
    sql.close()


def response_body(facts):
    return {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {
        "content": Explanation(facts=facts).model_dump_json()}}]}


def test_documented_json_schema_request_and_strict_parse(grounded):
    _, _, _, facts, sources = grounded

    def handler(request):
        assert str(request.url) == ENDPOINT
        assert request.headers["Authorization"] == "Bearer fake-test-key"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["schema"] == Explanation.model_json_schema()
        assert payload["temperature"] == 0
        assert "untrusted" in payload["messages"][0]["content"]
        assert json.loads(payload["messages"][1]["content"])["approved_facts"][0]["fix_id"] == facts[0].fix_id
        return httpx.Response(200, json=response_body(facts))

    adapter = FireworksExplanationAdapter("fake-test-key", MODEL, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        assert adapter.explain(facts, sources).facts == facts
    finally:
        adapter.close()


@pytest.mark.parametrize("failure", ["http_error", "bad_json", "wrong_model", "truncated", "tool_call", "extra_field", "missing_source", "multiple_choices"])
def test_provider_invalid_results_fail_closed_and_redact(grounded, failure):
    _, _, _, facts, sources = grounded

    def handler(request):
        body = response_body(facts)
        if failure == "http_error":
            return httpx.Response(401, text="secret-from-provider")
        if failure == "bad_json":
            return httpx.Response(200, text="secret-from-provider")
        if failure == "wrong_model":
            body["model"] = "unknown-model"
        elif failure == "truncated":
            body["choices"][0]["finish_reason"] = "length"
        elif failure == "tool_call":
            body["choices"][0]["message"]["tool_calls"] = [{"name": "arbitrary_sql"}]
        elif failure in ("extra_field", "missing_source"):
            payload = json.loads(body["choices"][0]["message"]["content"])
            if failure == "extra_field":
                payload["instructions"] = "secret-from-provider"
            else:
                del payload["facts"][0]["source_ids"]
            body["choices"][0]["message"]["content"] = json.dumps(payload)
        elif failure == "multiple_choices":
            body["choices"] *= 2
        return httpx.Response(200, json=body)

    adapter = FireworksExplanationAdapter("fake-test-key", MODEL, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(AdapterUnavailable) as caught:
            adapter.explain(facts, sources)
        assert "secret-from-provider" not in str(caught.value)
    finally:
        adapter.close()


def test_valid_json_but_fabricated_rate_is_refused_end_to_end(grounded):
    data, sql, request, facts, sources = grounded

    def handler(req):
        invented = facts[0].model_copy(update={"rate": .111})
        return httpx.Response(200, json=response_body((invented,)))

    adapter = FireworksExplanationAdapter("fake-test-key", MODEL, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        run = DiagnosisAgent(data, sql, adapter).run(request)
        assert run.response.status == "refused" and not run.response.candidates
    finally:
        adapter.close()
