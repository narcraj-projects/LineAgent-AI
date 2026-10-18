"""Rebuild comparison artifacts from saved rows; never invoke live services."""

import argparse
import csv
import json
from pathlib import Path

from evals.models import EvaluationRow, Mode
from evals.scoring import aggregate, comparison_table

MODES: tuple[Mode, ...] = ("dense", "hybrid", "graph", "combined")


def write_reports(directory: Path, rows: list[EvaluationRow]) -> None:
    cells = {(r.question_id, r.mode) for r in rows}
    expected = {(f"SYN-Q-{n:03d}", mode) for n in range(1, 31) for mode in MODES}
    if len(rows) != 120 or cells != expected:
        raise ValueError("Expected exactly one saved result for each of the 120 frozen cells")
    summaries = [aggregate(rows, mode) for mode in MODES]
    upload_path = directory / "trace_upload.json"
    uploaded = json.loads(upload_path.read_bytes()) if upload_path.is_file() else []
    for summary in summaries:
        attempts = [r for r in uploaded if r["mode"] == summary["mode"]]
        summary["trace_replay"] = {
            "acknowledged_cells": len({r["question_id"] for r in attempts if r["status"].startswith("PASS")}),
            "failed_upload_attempts": sum(r["status"].startswith("FAIL") for r in attempts),
            "note": "Original trace_status records startup configuration; replay uploads are separate observations."}
    (directory / "aggregate.json").write_text(json.dumps(summaries, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (directory / "supervisor_comparison.md").write_text(comparison_table(summaries), encoding="utf-8")
    records = []
    for row in rows:
        item = {k: getattr(row, k) for k in ("question_id", "mode", "category", "language", "status",
            "latency_ms", "cost_usd", "cost_status", "skip_reason", "trace_status")}
        if row.correctness:
            item.update(row.correctness.model_dump())
        item["failures"] = ";".join(row.correctness.failures) if row.correctness else ""
        item["natural_route"] = row.natural_decision.route if row.natural_decision else None
        item["executed_route"] = row.run.decision.route if row.run else None
        item["response_status"] = row.run.response.status if row.run else None
        item["predicted_fix_id"] = row.run.response.candidates[0].fix_id if row.run and row.run.response.candidates else None
        records.append(item)
    fields = list(dict.fromkeys(key for record in records for key in record))
    with (directory / "per_question.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    rows = [EvaluationRow.model_validate_json(line) for line in
        (args.result_dir / "raw_outputs.jsonl").read_text(encoding="utf-8").splitlines()]
    write_reports(args.result_dir, rows)
    print((args.result_dir / "supervisor_comparison.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
