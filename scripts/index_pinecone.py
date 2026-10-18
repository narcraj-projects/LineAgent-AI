"""Check compatibility, optionally index synthetic sources, report live scores."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from src.adapters.pinecone_documents import IndexCompatibilityError
from src.adapters.search_settings import SearchConfigurationError, load_search_settings
from src.retrieval.corpus import build_corpus, load_dataset
from src.retrieval.live import open_live_retrieval
from src.retrieval.metrics import EXACT_CASES, recall_at_5, relevant_source_ids


def run(env_file: Path, data_dir: Path, upsert: bool = False, create_index: bool = False,
        new_dimension: int = 1024) -> dict[str, Any]:
    report: dict[str, Any] = {"status": "NOT_RUN", "provider_mode": "live_only", "results": []}
    try:
        settings = load_search_settings(env_file)
    except SearchConfigurationError as exc:
        report["blocker"] = str(exc)  # Controlled variable names only.
        return report
    except Exception:
        report["blocker"] = "Invalid retrieval configuration; check local fields. Values withheld."
        return report
    live = None
    try:
        documents = build_corpus(load_dataset(data_dir))
        live = open_live_retrieval(settings, documents, create_index, new_dimension)
        report.update(index_configuration=live.index.configuration.model_dump(),
                      embedding_model=live.embedder.model, embedding_probe="PASS",
                      embedding_provider_model=live.embedder.observed_model,
                      query_preprocessing=live.embedder.query_preprocessing,
                      namespace=live.index.namespace, corpus_documents=len(documents))
        if upsert:
            report["upserted_and_visible"] = live.index_documents()
        else:
            live.index.verify_visible()
        for mode, retriever in (("dense", live.dense), ("hybrid", live.hybrid)):
            for token in EXACT_CASES:
                hits = retriever.search(token, top_k=5)
                expected = relevant_source_ids(documents, token)
                report["results"].append({
                    "mode": mode, "query": token, "relevant_source_ids": sorted(expected),
                    "recall_at_5": recall_at_5(hits, expected),
                    "hits": [hit.model_dump(mode="json") for hit in hits],
                })
        failed_cases = [f"{row['mode']}:{row['query']}" for row in report["results"] if row["recall_at_5"] <= 0]
        report["quality_gate"] = {
            "criterion": "Recall@5 must be greater than zero for each exact-token test",
            "status": "FAIL" if failed_cases else "PASS", "failed_cases": failed_cases,
        }
        report["status"] = "COMPLETE"
        return report
    except IndexCompatibilityError as exc:
        report.update(status="FAIL", blocker=str(exc))
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__, blocker="Live retrieval failed; provider exception details withheld.")
    finally:
        if live is not None:
            live.close()
    return report


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument("--report", type=Path, default=root / "evals/results/retrieval-live.json")
    parser.add_argument("--upsert", action="store_true")
    parser.add_argument("--create-index", action="store_true", help="Create a missing dedicated index after embedding probe; never replace existing indexes.")
    parser.add_argument("--dimension", type=int, default=1024)
    args = parser.parse_args()
    report = run(args.env_file, args.data_dir, args.upsert, args.create_index, args.dimension)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "COMPLETE":
        sys.exit(2 if report["status"] == "NOT_RUN" else 1)
    if report["quality_gate"]["status"] == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
