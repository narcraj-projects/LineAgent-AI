"""Upload saved synthetic evaluation rows without repeating any inference calls."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import TypeAdapter

from evals.models import EvaluationRow
from evals.run_suite import ROOT, write_json
from src.adapters.evaluation import LangSmithEvaluationTrace
from src.data_models import EvaluationQuestion


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    directory = args.result_dir.resolve()
    if not directory.is_relative_to((ROOT / "evals/results").resolve()):
        raise ValueError("Only local evals/results directories can be uploaded")
    local = dotenv_values(ROOT / ".env", interpolate=False)
    names = ("LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT",
             "LANGSMITH_WORKSPACE_ID", "SSL_CERT_FILE")
    tracer = LangSmithEvaluationTrace({k: os.environ.get(k, local.get(k)) for k in names})
    if tracer.client is None:
        print(tracer.status)
        return 2
    questions = TypeAdapter(tuple[EvaluationQuestion, ...]).validate_json((directory / "questions.json").read_bytes())
    inputs = {q.question_id: q.question for q in questions}
    previous_path = directory / "trace_upload.json"
    previous = json.loads(previous_path.read_bytes()) if previous_path.is_file() else []
    done = {(r["question_id"], r["mode"]) for r in previous if r["status"].startswith("PASS")}
    results = list(previous)
    try:
        for line in (directory / "raw_outputs.jsonl").read_text(encoding="utf-8").splitlines():
            row = EvaluationRow.model_validate_json(line)
            if row.status == "NOT_RUN" or (row.question_id, row.mode) in done:
                continue
            # Historic timestamps were not retained by the first runner revision.
            # Record upload time, with original measured duration in outputs;
            # do not fabricate historic wall-clock timestamps.
            now = datetime.now(timezone.utc)
            status = tracer.record({"question": inputs[row.question_id], "mode": row.mode,
                "replayed_from_saved_result": True}, row.model_dump(mode="json"),
                now, now)
            results.append({"question_id": row.question_id, "mode": row.mode, "status": status,
                "uploaded_at": now.isoformat(), "timestamp_basis": "replay upload time; historic duration in outputs"})
            write_json(previous_path, results)
            print(f"{row.question_id} {row.mode}: {status}", flush=True)
            if status.startswith("FAIL"):
                return 1
        return 0
    finally:
        tracer.close()


if __name__ == "__main__":
    raise SystemExit(main())
