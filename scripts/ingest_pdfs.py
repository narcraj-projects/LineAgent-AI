"""Parse two synthetic PDFs, preserve sections, and embed into a dedicated namespace."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter, sleep

from src.adapters.evaluation import HTTPObservations
from src.adapters.fireworks_embeddings import FireworksEmbeddingAdapter
from src.adapters.pdf_manuals import SyntheticPDFAdapter
from src.adapters.pinecone_documents import PineconeDocumentAdapter
from src.adapters.search_settings import load_search_settings
from src.retrieval.corpus import build_corpus, corpus_digest, load_dataset
from src.retrieval.pdf_metadata import build_pdf_metadata, pdf_namespace_tag

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "output/pdf/manifest.json")
    parser.add_argument("--parse-only", action="store_true", help="Validate/extract/chunk locally without calling providers")
    args = parser.parse_args()
    started = perf_counter()
    stamp = datetime.now(timezone.utc).strftime("pdf-ingest-%Y%m%dT%H%M%S%fZ")
    output = ROOT / "evals/results" / stamp
    output.mkdir(parents=True, exist_ok=False)
    baseline = load_dataset(ROOT / "data")
    parsed = SyntheticPDFAdapter().parse(args.manifest, baseline)
    (output / "parsed_chunks.json").write_text(json.dumps([c.model_dump(mode="json") for c in parsed.chunks],
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    documents = build_corpus(parsed.dataset)
    metadata = build_pdf_metadata(parsed)
    (output / "source_metadata.json").write_text(json.dumps({key: value.model_dump(mode="json")
        for key, value in metadata.items()}, indent=2) + "\n", encoding="utf-8")
    report = {"synthetic": True, "status": "PARSED", "pdf_files": len(parsed.manifest.files),
        "physical_pages": sum(f.pages for f in parsed.manifest.files), "manual_chunks": len(parsed.chunks),
        "manual_sources": 30, "json_work_order_sources": 50, "total_index_documents": len(documents),
        "chunking": "One bounded canonical manual section per physical PDF page; no overlap or cross-section joins.",
        "text_origin": "Manual bodies extracted from two actual PDFs; structural metadata from verified synthetic JSON.",
        "namespace_tag": pdf_namespace_tag(parsed.manifest), "corpus_digest": corpus_digest(documents),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "cost_usd": None, "cost_status": "UNKNOWN: no provider bill or verified tariffs", "usage": []}
    index, embedder = None, None
    observations = HTTPObservations()
    exit_code = 0
    try:
        if not args.parse_only:
            settings = load_search_settings(ROOT / ".env")
            index = PineconeDocumentAdapter.connect(settings, documents, namespace_tag=pdf_namespace_tag(parsed.manifest),
                source_metadata=metadata)
            report["namespace"] = index.namespace
            report["index_configuration"] = index.configuration.model_dump(mode="json")
            embedder = FireworksEmbeddingAdapter(settings.fireworks_api_key.get_secret_value(), settings.embedding_model,
                index.configuration.dimension, settings.ca_file, client=observations.client(settings.ca_file))
            acknowledged = index.upsert(embedder)
            report["upsert_acknowledged_documents"] = acknowledged
            for attempt in range(6):
                try:
                    index.verify_visible()
                    break
                except Exception:
                    if attempt == 5:
                        raise
                    sleep(1)
            report["status"] = "PASS"
            report["visibility"] = "All 80 canonical payloads and dense/lexical visibility verified live"
            report["embedding_model"] = embedder.observed_model
        else:
            report["live_indexing"] = "NOT_RUN: --parse-only selected"
    except Exception:
        report["status"] = "FAIL"
        report["error"] = "Embedding/indexing/visibility failed; partial writes may exist in the dedicated PDF namespace"
        exit_code = 1
    finally:
        report["usage"] = observations.records
        report["elapsed_ms"] = (perf_counter() - started) * 1000
        (output / "ingestion.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (ROOT / "evals/results/latest-pdf-ingest.txt").write_text(output.name + "\n", encoding="utf-8")
        for adapter in (embedder, index):
            if adapter:
                adapter.close()
    print(json.dumps(report, indent=2))
    print("Results: " + str(output))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
