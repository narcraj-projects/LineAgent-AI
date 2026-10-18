"""Run frozen synthetic questions once per mode, saving actual results incrementally.

Usage: python -m evals.run_suite --graph-backend auto
No index writes, hosted graph seeding or feedback mutations are performed.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
from time import perf_counter
from typing import Any

from dotenv import dotenv_values
from pydantic import TypeAdapter

from evals.models import EvaluationRow, Mode
from evals.report import write_reports
from evals.scoring import aggregate, comparison_table, score
from src.adapters.evaluation import FrozenGraph, HTTPObservations, LangSmithEvaluationTrace, ObservedRetriever
from src.adapters.fireworks_embeddings import FireworksEmbeddingAdapter
from src.adapters.fireworks_explanation import FireworksExplanationAdapter
from src.adapters.pinecone_documents import PineconeDocumentAdapter
from src.adapters.pdf_manuals import SyntheticPDFAdapter
from src.adapters.search_settings import load_search_settings
from src.adapters.sqlite_evidence import SQLiteEvidenceAdapter
from src.adapters.supabase_store import SupabaseStore, load_supabase_settings
from src.agent import DiagnosisAgent
from src.data_models import Benchmark, EvaluationQuestion, validate_benchmark
from src.graph import GraphBackend, select_graph
from src.retrieval.corpus import build_corpus, corpus_digest, load_dataset
from src.retrieval.dense import DenseRetriever
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.pdf_metadata import build_pdf_metadata, langchain_pdf_namespace_tag
from src.router import select_route
from src.schemas import DiagnosisRequest

ROOT = Path(__file__).resolve().parents[1]
MODES: tuple[Mode, ...] = ("dense", "hybrid", "graph", "combined")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_questions() -> tuple[EvaluationQuestion, ...]:
    questions = TypeAdapter(tuple[EvaluationQuestion, ...]).validate_json((ROOT / "data/eval/questions.json").read_bytes())
    if len(questions) != 30 or len({q.question_id for q in questions}) != 30:
        raise ValueError("Exactly 30 unique frozen questions required")
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-backend", choices=("auto", "neo4j", "networkx"), default="auto")
    parser.add_argument("--pdf-manifest", type=Path, help="Use verified PDF-extracted manuals and the ingested PDF namespace")
    args = parser.parse_args()
    backend: GraphBackend = args.graph_backend
    started = datetime.now(timezone.utc)
    output = ROOT / "evals/results" / started.strftime("frozen30-%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True, exist_ok=False)
    baseline = load_dataset(ROOT / "data")
    parsed_pdf = SyntheticPDFAdapter().parse(args.pdf_manifest, baseline) if args.pdf_manifest else None
    if parsed_pdf:
        baseline = parsed_pdf.dataset
    pdf_locations = {c.section_id: c for c in parsed_pdf.manifest.citations} if parsed_pdf else {}
    source_metadata = build_pdf_metadata(parsed_pdf) if parsed_pdf else {}
    questions = load_questions()
    truth = json.loads((ROOT / "data/eval/alarm_truth.json").read_bytes())
    benchmark = Benchmark.model_validate_json(json.dumps({"seed": baseline.seed,
        "alarm_truth": truth, "questions": [q.model_dump(mode="json") for q in questions]}))
    validate_benchmark(baseline, benchmark)
    write_json(output / "questions.json", [q.model_dump(mode="json") for q in questions])
    write_json(output / "snapshot.json", baseline.model_dump(mode="json"))
    local = dotenv_values(ROOT / ".env", interpolate=False)
    names = ("FIREWORKS_API_KEY", "FIREWORKS_MODEL", "SSL_CERT_FILE", "LANGSMITH_API_KEY",
             "LANGSMITH_PROJECT", "LANGSMITH_TRACING", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID")
    values = {k: os.environ.get(k, local.get(k)) for k in names}
    trace = LangSmithEvaluationTrace(values)
    http = HTTPObservations()
    ca = Path(values["SSL_CERT_FILE"]) if values["SSL_CERT_FILE"] else None
    sql = SQLiteEvidenceAdapter(baseline)
    graph, index, embedder, explanation = None, None, None, None
    dense, hybrid = None, None
    setup: dict[str, Any] = {"sql": sql.provider, "tracing": trace.status}
    blockers: dict[str, str] = {}
    try:
        if values["FIREWORKS_API_KEY"] and values["FIREWORKS_MODEL"]:
            explanation = FireworksExplanationAdapter(values["FIREWORKS_API_KEY"], values["FIREWORKS_MODEL"],
                ca, client=http.client(ca))
            setup["explanation"] = {"provider": explanation.provider, "model": explanation.model}
        else:
            blockers["explanation"] = "Fireworks API key or explicit chat model missing"
        try:
            settings = load_search_settings(ROOT / ".env")
            index = PineconeDocumentAdapter.connect(settings, build_corpus(baseline),
                namespace_tag=langchain_pdf_namespace_tag(parsed_pdf.manifest) if parsed_pdf else None,
                source_metadata=source_metadata or None)
            index.verify_visible()
            embedder = FireworksEmbeddingAdapter(settings.fireworks_api_key.get_secret_value(), settings.embedding_model,
                index.configuration.dimension, settings.ca_file, client=http.client(settings.ca_file))
            dense = ObservedRetriever(DenseRetriever(embedder, index))
            if index.configuration.text_field:
                hybrid = ObservedRetriever(HybridRetriever(embedder, index))
            else:
                blockers["hybrid"] = "Pinecone index lacks full-text field"
            setup["retrieval"] = {"provider": "pinecone:live", "namespace": index.namespace,
                "configuration": index.configuration.model_dump(mode="json"), "embedding_model": embedder.model}
        except Exception:
            blockers["retrieval"] = "Pinecone configuration, connection or frozen corpus visibility check failed"
        try:
            graph = select_graph(backend, ROOT / ".env")
            if graph.status.mode == "fallback":
                graph.seed(baseline)
            setup["graph"] = graph.status.model_dump(mode="json")
        except Exception:
            blockers["graph"] = "Explicit live Neo4j unavailable; no fallback selected"
        # Read Supabase only to verify baseline equality, not to change rates.
        store = None
        try:
            store = SupabaseStore(load_supabase_settings(ROOT / ".env"))
            snapshot = store.snapshot()
            known = {w.work_order_id: w for w in baseline.work_orders}
            if (snapshot.model_dump(exclude={"work_orders"}) != baseline.model_dump(exclude={"work_orders"})
                    or {w.work_order_id: w for w in snapshot.work_orders if w.work_order_id in known} != known):
                raise ValueError("Frozen baseline changed in Supabase")
            setup["supabase_readonly_baseline_check"] = {"status": "PASS", "live_orders": len(snapshot.work_orders),
                "frozen_orders": len(baseline.work_orders), "excluded_later_feedback": len(snapshot.work_orders) - len(known)}
        except Exception:
            setup["supabase_readonly_baseline_check"] = {"status": "NOT_RUN_OR_FAIL",
                "reason": "Hosted snapshot unavailable or differs from immutable baseline; local frozen SQL used"}
        finally:
            if store:
                store.close()
        frozen_graph = FrozenGraph(graph, baseline) if graph else None
        manifest = {"synthetic": True, "started_at": started.isoformat(), "python": platform.python_version(),
            "question_count": len(questions), "modes": MODES, "seed": baseline.seed,
            "question_sha256": hashlib.sha256((ROOT / "data/eval/questions.json").read_bytes()).hexdigest(),
            "dataset_sha256": hashlib.sha256(baseline.model_dump_json().encode()).hexdigest(),
            "corpus_digest": corpus_digest(build_corpus(baseline)), "setup": setup, "blockers": blockers,
            "method": "Forced retrieval ablations; natural router recorded separately. No answer labels supplied to agent.",
            "order": "Serial; mode order rotates by question index; no retries or result cherry-picking.",
            "cost": "USD unknown; no provider bill or verified tariffs. Usage is observed, not billed cost.",
            "graph_snapshot": "Read-only live paths with histories restricted to original 50 WO IDs; baseline content verified.",
            "limits": "One observation/cell, no Tamil language scoring, no RAGAS evaluator, no cache flush."}
        if parsed_pdf:
            manifest["pdf_pipeline"] = {"manifest": parsed_pdf.manifest.model_dump(mode="json"),
                "manual_text_origin": "PDF extraction, canonical metadata verified before retrieval",
                "manual_chunks": len(parsed_pdf.chunks), "work_order_origin": "Frozen synthetic JSON"}
        write_json(output / "manifest.json", manifest)
        print(f"Results: {output}", flush=True)
        rows: list[EvaluationRow] = []
        for number, question in enumerate(questions):
            for mode in MODES[number % 4:] + MODES[:number % 4]:
                # The only benchmark value passed at runtime is the public text.
                request = DiagnosisRequest(query=question.question)
                natural = select_route(request)
                required = ["explanation"]
                if mode in ("dense", "hybrid", "combined"):
                    required.append("retrieval")
                if mode in ("hybrid", "combined"):
                    required.append("hybrid")
                if mode in ("graph", "combined"):
                    required.append("graph")
                reason = "; ".join(blockers[k] for k in required if k in blockers)
                if reason:
                    row = EvaluationRow(question_id=question.question_id, category=question.category,
                        language=question.language, mode=mode, status="NOT_RUN", skip_reason=reason)
                else:
                    http.records.clear()
                    for retriever in (dense, hybrid):
                        if retriever:
                            retriever.hits, retriever.called = (), False
                    if frozen_graph:
                        frozen_graph.paths.clear()
                    call_start = datetime.now(timezone.utc)
                    clock_start = perf_counter()
                    run = DiagnosisAgent(baseline, sql, explanation, dense, hybrid, frozen_graph).run(
                        request, evaluation_mode=mode)
                    elapsed = (perf_counter() - clock_start) * 1000
                    call_end = datetime.now(timezone.utc)
                    retriever = dense if mode == "dense" else hybrid if mode in ("hybrid", "combined") else None
                    hits = retriever.hits if retriever else ()
                    correctness = score(question, run, natural, baseline,
                        tuple(h.document.source_id for h in hits) if retriever else None)
                    no_external = not http.records and not run.tools
                    row = EvaluationRow(question_id=question.question_id, category=question.category, language=question.language,
                        mode=mode, status="FAIL" if correctness.failures else "PASS", natural_decision=natural, run=run,
                        correctness=correctness, latency_ms=elapsed, cost_usd=0.0 if no_external else None,
                        cost_status="ZERO: no metered service invoked" if no_external else
                            "UNKNOWN: provider billing and verified tariffs unavailable",
                        provider_responses=tuple(http.records), retrieved_hits=tuple(h.model_dump(mode="json") for h in hits),
                        graph_paths=tuple(p.model_dump(mode="json") for p in frozen_graph.paths) if frozen_graph else (),
                        pdf_citations=tuple(pdf_locations[e.source_id].model_dump(mode="json") for e in run.response.evidence
                            if e.source_id in pdf_locations),
                        source_metadata=tuple(source_metadata[e.source_id].model_dump(mode="json") for e in run.response.evidence
                            if e.source_id in source_metadata))
                    trace_status = trace.record({"question": request.query, "mode": mode},
                        row.model_dump(mode="json"), call_start, call_end)
                    row = row.model_copy(update={"trace_status": trace_status})
                rows.append(row)
                with (output / "raw_outputs.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(row.model_dump_json() + "\n")
                print(f"{len(rows):03d}/120 {question.question_id} {mode}: {row.status} "
                      f"{row.latency_ms:.0f}ms" if row.latency_ms is not None else
                      f"{len(rows):03d}/120 {question.question_id} {mode}: NOT_RUN {row.skip_reason}", flush=True)
        summaries = [aggregate(rows, mode) for mode in MODES]
        write_json(output / "aggregate.json", summaries)
        write_json(output / "per_question.json", [r.model_dump(mode="json", exclude={"provider_responses", "retrieved_hits", "graph_paths"}) for r in rows])
        write_json(output / "failed_cases.json", [r.model_dump(mode="json") for r in rows if r.status == "FAIL"])
        write_json(output / "skipped_cases.json", [r.model_dump(mode="json") for r in rows if r.status == "NOT_RUN"])
        (output / "supervisor_comparison.md").write_text(comparison_table(summaries), encoding="utf-8")
        write_reports(output, rows)
        manifest.update({"finished_at": datetime.now(timezone.utc).isoformat(), "completed_cells": len(rows),
            "excluded_graph_feedback_ids": sorted(frozen_graph.excluded_ids) if frozen_graph else []})
        write_json(output / "manifest.json", manifest)
        (ROOT / "evals/results/latest-frozen30.txt").write_text(output.name + "\n", encoding="utf-8")
        print(comparison_table(summaries), flush=True)
        return 1 if any(r.status == "FAIL" for r in rows) else 2 if any(r.status == "NOT_RUN" for r in rows) else 0
    finally:
        for adapter in (trace, explanation, embedder, index, graph, sql):
            if adapter:
                try:
                    adapter.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
