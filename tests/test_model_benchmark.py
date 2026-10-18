"""Meaningful boundary checks for catalog access and benchmark reporting."""
import httpx
import pytest
from src.adapters.base import AdapterUnavailable
from src.adapters.fireworks_catalog import FireworksCatalog
from evals.model_benchmark import summarize, percentile95


def test_catalog_paginates_and_sorts_public_ids() -> None:
    calls = []
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={'models': [{'name': 'accounts/fireworks/models/z',
                'supportsServerless': True, 'kind': 'HF_BASE_MODEL', 'contextLength': 4096}], 'nextPageToken': 'next'})
        assert request.url.params['pageToken'] == 'next'
        return httpx.Response(200, json={'models': [{'name': 'accounts/fireworks/models/a',
            'kind': 'EMBEDDING_MODEL'}]})
    catalog = FireworksCatalog('test-only', client=httpx.Client(transport=httpx.MockTransport(respond)))
    try:
        rows = catalog.models()
        assert [r.model for r in rows] == ['accounts/fireworks/models/a', 'accounts/fireworks/models/z']
        assert rows[1].serverless is True and rows[0].serverless is False
    finally:
        catalog.close()


@pytest.mark.parametrize('body', [
    {'models': 'invalid'},
    {'models': [{'name': 'accounts/private/models/a'}]},
    {'models': [{'name': 'accounts/fireworks/models/a'}, {'name': 'accounts/fireworks/models/a', 'supportsServerless': True}]},
    {'models': [], 'nextPageToken': 'repeat'},
])
def test_catalog_rejects_invalid_identity_duplicates_and_pagination(body: dict) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    catalog = FireworksCatalog('secret-must-not-leak', client=client)
    with pytest.raises(AdapterUnavailable) as error:
        catalog.models()
    assert 'secret-must-not-leak' not in str(error.value)
    catalog.close()


def test_summary_keeps_failures_and_not_run_out_of_success_latency() -> None:
    rows = [{'model': 'm', 'status': 'PASS', 'latency_ms': 10.0},
            {'model': 'm', 'status': 'FAIL', 'latency_ms': 100.0},
            {'model': 'm', 'status': 'NOT_RUN', 'latency_ms': None}]
    row = summarize(rows)[0]
    assert (row['pass'], row['fail'], row['not_run']) == (1, 1, 1)
    assert row['latency_n'] == 2 and row['latency_p95_ms'] == 100
    assert row['successful_latency_n'] == 1 and row['successful_latency_median_ms'] == 10
    assert row['cost_usd'] is None
    assert percentile95([]) is None