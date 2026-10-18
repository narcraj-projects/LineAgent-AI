"""Measure the existing four exact-token recall cases on the real PDF namespace."""

from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from src.adapters.pdf_manuals import SyntheticPDFAdapter
from src.adapters.search_settings import load_search_settings
from src.retrieval.corpus import build_corpus, load_dataset
from src.retrieval.live import open_live_retrieval
from src.retrieval.metrics import EXACT_CASES, recall_at_5, relevant_source_ids
from src.retrieval.pdf_metadata import build_pdf_metadata, langchain_pdf_namespace_tag

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parsed = SyntheticPDFAdapter().parse(ROOT / "output/pdf/manifest.json", load_dataset(ROOT / "data"))
    documents = build_corpus(parsed.dataset)
    metadata = build_pdf_metadata(parsed)
    live = open_live_retrieval(load_search_settings(ROOT / ".env"), documents,
        namespace_tag=langchain_pdf_namespace_tag(parsed.manifest), source_metadata=metadata)
    output = ROOT / "evals/results" / datetime.now(timezone.utc).strftime("pdf-exact-recall-%Y%m%dT%H%M%S%fZ.json")
    records = []
    try:
        for token in EXACT_CASES:
            relevant = relevant_source_ids(documents, token)
            for mode, retriever in (("dense", live.dense), ("hybrid", live.hybrid)):
                started = perf_counter()
                try:
                    hits = retriever.search(token, top_k=5)
                    elapsed = (perf_counter() - started) * 1000
                    recall = recall_at_5(hits, relevant)
                    records.append({"query": token, "mode": mode, "status": "PASS" if recall == 1 else "FAIL",
                        "recall_at_5": recall, "latency_ms": elapsed, "expected_source_ids": sorted(relevant),
                        "hits": [h.model_dump(mode="json") for h in hits], "cost_usd": None})
                except Exception:
                    records.append({"query": token, "mode": mode, "status": "FAIL",
                        "error": "Live retrieval or source-metadata validation failed; provider details withheld"})
        output.write_text(json.dumps({"synthetic": True, "namespace": live.index.namespace, "cases": records,
            "gate": "Recall@5 equals 1.0; failures retained without tuning or retries",
            "cost_status": "UNKNOWN: provider billing not supplied"}, indent=2) + "\n", encoding="utf-8")
        (ROOT / "evals/results/latest-pdf-recall.txt").write_text(output.name + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "cases": [{k: r.get(k) for k in ("query", "mode", "status", "recall_at_5")}
            for r in records]}, indent=2))
        return 1 if any(r["status"] == "FAIL" for r in records) else 0
    finally:
        live.close()


if __name__ == "__main__":
    raise SystemExit(main())
