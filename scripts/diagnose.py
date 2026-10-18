"""Explicit CLI: live Fireworks explanation, optional live retrieval, labeled graph."""

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values

from src.adapters.fireworks_explanation import FireworksExplanationAdapter
from src.adapters.search_settings import load_search_settings
from src.adapters.sqlite_evidence import SQLiteEvidenceAdapter
from src.agent import DiagnosisAgent
from src.graph import select_graph
from src.retrieval.corpus import build_corpus, load_dataset
from src.retrieval.live import LiveRetrieval, open_live_retrieval
from src.router import select_route
from src.safety import refusal
from src.schemas import DiagnosisRequest

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--model", help="Explicit Fireworks chat model resource ID; otherwise FIREWORKS_MODEL")
    parser.add_argument("--equipment-id")
    parser.add_argument("--graph-backend", choices=("networkx", "neo4j", "auto"), default="networkx")
    args = parser.parse_args()
    request = DiagnosisRequest(query=args.query, equipment_id=args.equipment_id)
    route = select_route(request)
    if route.route == "refuse":
        print(refusal(route.reason).model_dump_json(indent=2))
        return 0
    local = dotenv_values(ROOT / ".env", interpolate=False)
    model = args.model or os.environ.get("FIREWORKS_MODEL", local.get("FIREWORKS_MODEL"))
    key = os.environ.get("FIREWORKS_API_KEY", local.get("FIREWORKS_API_KEY"))
    ca = os.environ.get("SSL_CERT_FILE", local.get("SSL_CERT_FILE"))
    if not key or not model:
        print("NOT RUN: configure FIREWORKS_API_KEY and FIREWORKS_MODEL (or --model) locally.")
        return 2
    live: LiveRetrieval | None = None
    sql = None
    graph = None
    explanation = None
    try:
        data = load_dataset(ROOT / "data")
        sql = SQLiteEvidenceAdapter(data)
        explanation = FireworksExplanationAdapter(key, model, Path(ca) if ca else None)
        if route.route in ("dense", "hybrid", "combined"):
            live = open_live_retrieval(load_search_settings(ROOT / ".env"), build_corpus(data))
        if route.route in ("graph", "combined"):
            graph = select_graph(args.graph_backend, ROOT / ".env")
            if graph.status.mode == "fallback":
                graph.seed(data)
            # Never implicitly modify or seed a live Neo4j database.
        agent = DiagnosisAgent(data, sql, explanation, live.dense if live else None,
                               live.hybrid if live else None, graph)
        result = agent.run(request)
        output = ROOT / "evals/results/diagnosis-live.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(result.model_dump_json(indent=2))
        return 0 if result.response.status == "supported" else 1
    except Exception:
        print("NOT RUN: service configuration or connection failed; no successful diagnosis is claimed.")
        return 2
    finally:
        for adapter in (live, graph, sql, explanation):
            if adapter is not None:
                adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
