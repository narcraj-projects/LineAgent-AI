"""Select a backend; exercise fallback locally without seeding a live database."""

import argparse
import json
from pathlib import Path

from scripts.generate_data import generate
from src.graph import audit_rates, select_graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("auto", "neo4j", "networkx"), default="auto")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    graph = select_graph(args.backend, args.env_file)
    try:
        report = {"status": graph.status.model_dump(), "live_database_modified": False}
        if graph.status.mode == "fallback":
            data, _ = generate()
            first = graph.seed(data)
            report.update(
                counts=first.model_dump(), reseed_idempotent=graph.seed(data) == first,
                canonical_sqlite_rate_checks=audit_rates(graph, data),
                alarm_path_counts={code: len(graph.alarm_paths(code)) for code in ("SYN-ALM-001", "SYN-ALM-020")},
            )
        else:
            report["scope"] = "Live connection and read-only probe only; graph seed/retrieval not exercised."
        print(json.dumps(report, indent=2))
    finally:
        graph.close()


if __name__ == "__main__":
    main()
