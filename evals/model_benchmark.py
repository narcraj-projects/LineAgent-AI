"""Reproducible, read-only synthetic generation and embedding comparison.

Run: python -m evals.model_benchmark
Protocol: docs/MODEL_BENCHMARK_PROTOCOL.md. No deployed defaults are changed.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from typing import Any

from dotenv import dotenv_values
from src.adapters.evaluation import HTTPObservations
from src.adapters.fireworks_catalog import FireworksCatalog
from src.adapters.fireworks_embeddings import FireworksEmbeddingAdapter
from src.adapters.fireworks_explanation import FireworksExplanationAdapter
from src.adapters.langchain_retrieval import ValidatedEmbeddings
from src.adapters.pdf_manuals import SyntheticPDFAdapter
from src.adapters.sqlite_evidence import SQLiteEvidenceAdapter
from src.agent import DiagnosisAgent
from src.agent_models import Explanation, GroundedFact
from src.graph import select_graph
from src.retrieval.corpus import build_corpus, corpus_digest, load_dataset
from src.router import select_route
from src.schemas import DiagnosisRequest, Evidence
from evals.run_suite import load_questions, write_json

ROOT = Path(__file__).resolve().parents[1]
GENERATORS = tuple('accounts/fireworks/models/' + name for name in
                   ('deepseek-v4p1-flash', 'gpt-oss-120b', 'glm-5p3-flash'))
EMBEDDERS = (('fireworks/qwen3-embedding-8b', 1024),
             ('nomic-ai/nomic-embed-text-v1.5', 768), ('BAAI/bge-small-en-v1.5', 384))


class ContextCapture:
    """Deterministic context preparation only; never a provider result or score."""
    provider = 'benchmark:context-construction-only'

    def __init__(self) -> None:
        self.context: tuple[tuple[GroundedFact, ...], tuple[Evidence, ...]] | None = None

    def explain(self, facts: tuple[GroundedFact, ...], evidence: tuple[Evidence, ...]) -> Explanation:
        self.context = (facts, evidence)
        return Explanation(facts=facts)


def append_raw(path: Path, row: dict[str, Any]) -> None:
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        handle.flush()


def percentile95(values: list[float]) -> float | None:
    return sorted(values)[math.ceil(.95 * len(values)) - 1] if values else None


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for model in dict.fromkeys(r['model'] for r in rows):
        group = [r for r in rows if r['model'] == model]
        timings = [r['latency_ms'] for r in group if r.get('latency_ms') is not None]
        successful = [r['latency_ms'] for r in group if r['status'] == 'PASS' and r.get('latency_ms') is not None]
        summaries.append({'model': model, 'rows': len(group),
            'pass': sum(r['status'] == 'PASS' for r in group),
            'fail': sum(r['status'] == 'FAIL' for r in group),
            'not_run': sum(r['status'] == 'NOT_RUN' for r in group),
            'latency_n': len(timings), 'latency_median_ms': median(timings) if timings else None,
            'latency_p95_ms': percentile95(timings), 'successful_latency_n': len(successful),
            'successful_latency_median_ms': median(successful) if successful else None,
            'successful_latency_p95_ms': percentile95(successful), 'cost_usd': None})
    return summaries


def main() -> int:
    output = ROOT / 'evals/results' / datetime.now(timezone.utc).strftime('model-benchmark-%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True, exist_ok=False)
    local = dotenv_values(ROOT / '.env', interpolate=False)
    key = os.environ.get('FIREWORKS_API_KEY') or local.get('FIREWORKS_API_KEY')
    ca_value = os.environ.get('SSL_CERT_FILE') or local.get('SSL_CERT_FILE')
    if not key:
        raise ValueError('Fireworks environment credentials required; no substitute outputs')
    ca = Path(ca_value) if ca_value else None
    catalog = FireworksCatalog(key, ca)
    try:
        available = catalog.models()
    finally:
        catalog.close()
    write_json(output / 'catalog.json', [row.model_dump(mode='json') for row in available])
    advertised = {row.model for row in available if row.serverless}
    if not set(GENERATORS) <= advertised:
        raise ValueError('Frozen generation candidates are no longer advertised serverless; retain catalog and stop')
    data = SyntheticPDFAdapter().parse(ROOT / 'output/pdf/manifest.json', load_dataset(ROOT / 'data')).dataset
    corpus = build_corpus(data)
    questions = load_questions()
    graph = select_graph('networkx')
    graph.seed(data)
    sql = SQLiteEvidenceAdapter(data)
    contexts = {}
    controls = []
    for question in questions:
        capture = ContextCapture()
        # No evaluation labels are passed to the context builder.
        run = DiagnosisAgent(data, sql, capture, graph=graph).run(
            DiagnosisRequest(query=question.question), evaluation_mode='graph')
        if capture.context:
            contexts[question.question_id] = capture.context
        else:
            controls.append({'question_id': question.question_id, 'public_question': question.question,
                'route': run.decision.route, 'status': run.response.status, 'model_called': False})
    sql.close()
    graph.close()
    if len(contexts) != 27 or len(controls) != 3 or any(r['status'] != 'refused' for r in controls):
        raise ValueError('Unexpected context construction; do not continue with silently dropped cases')
    write_json(output / 'contexts.json', {qid: {'facts': [f.model_dump(mode='json') for f in facts],
        'evidence': [e.model_dump(mode='json') for e in evidence]} for qid, (facts, evidence) in contexts.items()})
    write_json(output / 'router-controls.json', controls)
    manifest = {'synthetic': True, 'started_at': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(), 'generation_models': GENERATORS, 'embedding_models': EMBEDDERS,
        'repeats': 2, 'supported_contexts': 27, 'router_controls': 3,
        'question_sha256': hashlib.sha256((ROOT / 'data/eval/questions.json').read_bytes()).hexdigest(),
        'protocol_sha256': hashlib.sha256((ROOT / 'docs/MODEL_BENCHMARK_PROTOCOL.md').read_bytes()).hexdigest(),
        'corpus_digest': corpus_digest(corpus), 'generation_settings': {'temperature': 0, 'max_tokens': 3000, 'retries': 0},
        'cost_usd': None, 'hosted_database_writes': False, 'vector_collection': 'local exact cosine; not hosted Pinecone',
        'limitations': ['Constrained approved-fact fidelity, not open-ended reasoning', 'Only synthetic questions',
            'Router refusal controls do not measure model safety', 'Small repeated set; no statistical superiority claim']}
    write_json(output / 'manifest.json', manifest)
    print(f'Results: {output}', flush=True)
    observations = HTTPObservations()
    adapters = {model: FireworksExplanationAdapter(key, model, ca, client=observations.client(ca)) for model in GENERATORS}
    generation = []
    supported = [q for q in questions if q.question_id in contexts]
    try:
        for repeat in range(2):
            for number, question in enumerate(supported):
                shift = (number + repeat) % len(GENERATORS)
                order = GENERATORS[shift:] + GENERATORS[:shift]
                if repeat:
                    order = tuple(reversed(order))
                facts, evidence = contexts[question.question_id]
                for model in order:
                    observations.records.clear()
                    start = perf_counter()
                    proposal = None
                    reason = None
                    try:
                        proposal = adapters[model].explain(facts, evidence)
                    except Exception:
                        reason = 'Provider unavailable or response failed strict adapter validation; error payload withheld'
                    elapsed = (perf_counter() - start) * 1000
                    raw = {'model': model, 'question_id': question.question_id, 'repeat': repeat + 1,
                        'latency_ms': elapsed, 'proposal': proposal.model_dump(mode='json') if proposal else None,
                        'provider_responses': list(observations.records), 'error': reason, 'cost_usd': None}
                    append_raw(output / 'generation-raw.jsonl', raw)
                    # Only score after saving the raw attempt.
                    row = {**raw, 'status': 'PASS' if proposal and proposal.facts == facts else 'FAIL',
                        'exact_fact_fidelity': bool(proposal and proposal.facts == facts)}
                    generation.append(row)
                    print(f'GEN {len(generation):03d}/162 {model.rsplit("/",1)[-1]} {question.question_id} r{repeat+1} {row["status"]} {elapsed:.0f}ms', flush=True)
    finally:
        for adapter in adapters.values():
            adapter.close()
    write_json(output / 'generation-results.json', generation)
    write_json(output / 'generation-summary.json', summarize(generation))
    embedding = []
    preflights = []
    for model, dimension in EMBEDDERS:
        observations.records.clear()
        adapter = FireworksEmbeddingAdapter(key, model, dimension, ca, client=observations.client(ca))
        bridge = ValidatedEmbeddings(adapter)
        try:
            try:
                bridge.embed_query('Synthetic equipment alarm SYN-ALM-001')
                preflight = {'model': model, 'dimension': dimension, 'status': 'PASS',
                    'observed_model': adapter.observed_model, 'provider_responses': list(observations.records)}
            except Exception:
                preflight = {'model': model, 'dimension': dimension, 'status': 'FAIL',
                    'reason': 'Embedding preflight unavailable or failed strict validation',
                    'provider_responses': list(observations.records)}
            append_raw(output / 'embedding-preflight-raw.jsonl', preflight)
            preflights.append(preflight)
            if preflight['status'] != 'PASS':
                for repeat in range(2):
                    for question in supported:
                        row = {'model': model, 'question_id': question.question_id, 'repeat': repeat + 1,
                            'status': 'NOT_RUN', 'latency_ms': None, 'reason': preflight['reason']}
                        append_raw(output / 'embedding-raw.jsonl', row)
                        embedding.append(row)
                print(f'EMBED {model}: NOT_RUN after failed preflight', flush=True)
                continue
            observations.records.clear()
            document_vectors = bridge.embed_documents([d.text for d in corpus])
            write_json(output / f'embedding-documents-{len(preflights)}.json', {'model': model,
                'source_ids': [d.source_id for d in corpus], 'vectors': document_vectors,
                'provider_responses': list(observations.records)})
            for repeat in range(2):
                for question in supported:
                    request = DiagnosisRequest(query=question.question)
                    decision = select_route(request)
                    alarm = next((a for a in data.alarms if a.code == decision.alarm_code), None)
                    equipment = alarm.equipment_id if alarm else decision.equipment_id
                    query = f'{decision.alarm_code} {request.query}' if decision.alarm_code else request.query
                    observations.records.clear()
                    start = perf_counter()
                    hits = []
                    error = None
                    try:
                        vector = bridge.embed_query(query)
                        hits = sorted([{'source_id': doc.source_id,
                            'score': sum(a*b for a,b in zip(vector, docvector, strict=True))}
                            for doc, docvector in zip(corpus, document_vectors, strict=True)
                            if equipment in doc.equipment_ids], key=lambda hit: (-hit['score'], hit['source_id']))[:5]
                    except Exception:
                        error = 'Embedding query or vector validation failed'
                    elapsed = (perf_counter() - start) * 1000
                    raw = {'model': model, 'question_id': question.question_id, 'repeat': repeat+1,
                        'latency_ms': elapsed, 'hits': hits, 'equipment_id': equipment,
                        'provider_responses': list(observations.records), 'error': error, 'cost_usd': None}
                    append_raw(output / 'embedding-raw.jsonl', raw)
                    expected = set(question.expected_source_ids)
                    found = {h['source_id'] for h in hits}
                    recall = len(expected & found) / len(expected) if expected else None
                    row = {**raw, 'status': 'PASS' if not error else 'FAIL', 'recall_at_5': recall,
                        'expected_count': len(expected), 'found_expected_count': len(expected & found)}
                    embedding.append(row)
            print(f'EMBED {model}: completed 54 retrieval observations', flush=True)
        finally:
            adapter.close()
    write_json(output / 'embedding-results.json', embedding)
    summaries = summarize(embedding)
    for summary in summaries:
        cells = [r for r in embedding if r['model'] == summary['model'] and r['status'] == 'PASS']
        values = [r['recall_at_5'] for r in cells if r['recall_at_5'] is not None]
        summary.update({'recall_denominator_queries': len(values),
            'mean_source_recall_at_5': sum(values)/len(values) if values else None,
            'all_expected_sources_found': sum(r['recall_at_5'] == 1 for r in cells),
            'retrieval_status_meaning': 'PASS means request/search completed, not perfect recall'})
    write_json(output / 'embedding-summary.json', summaries)
    write_json(output / 'embedding-preflights.json', preflights)
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(output / 'manifest.json', manifest)
    (ROOT / 'evals/results/latest-model-benchmark.txt').write_text(output.name + '\n', encoding='utf-8')
    print('Benchmark complete; deployed defaults unchanged.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())