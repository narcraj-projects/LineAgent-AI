"""Verify real Supabase feedback across a fresh process; no LLM call is claimed."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import UUID

from src.adapters.supabase_store import SupabaseStore, load_supabase_settings
from src.diagnosis import prepare_facts
from src.feedback import FeedbackService
from src.feedback_models import new_feedback
from src.graph import select_graph
from src.retrieval.corpus import load_dataset
from src.schemas import DiagnosisRequest

ROOT = Path(__file__).resolve().parents[1]


def read_after_restart(env_file: Path, event_id: UUID, graph_backend: str) -> dict[str, Any]:
    store = SupabaseStore(load_supabase_settings(env_file))
    graph = None
    try:
        data = store.snapshot()
        expected_id = f"SYN-WO-FB-{event_id.hex}"
        order = next(w for w in data.work_orders if w.work_order_id == expected_id)
        graph = select_graph(graph_backend, env_file)
        graph.seed(data)
        FeedbackService(store, graph).retry_pending()
        request = DiagnosisRequest(query=order.alarm_code, alarm_code=order.alarm_code, equipment_id=order.equipment_id)
        facts, _, candidates = prepare_facts(data, request, store.retrieve(request), store)
        selected = next(c for c in candidates if c.fix_id == order.fix_id)
        history = store.fix_history(order.equipment_id, order.fix_id)
        if graph.fix_history(order.equipment_id, order.fix_id).work_orders != history.work_orders:
            raise ValueError("Graph/SQL rate audit mismatch")
        return {"status": "PASS", "storage": store.provider, "graph": graph.status.model_dump(mode="json"),
            "generation": "NOT_RUN: persistence/ranking check only", "event_id": str(event_id),
            "work_order_id": expected_id, "persisted_work_orders": len(data.work_orders),
            "worked": history.outcomes.worked, "did_not_work": history.outcomes.did_not_work,
            "rate": history.outcomes.success_rate, "ranked_fix_ids": [f.fix_id for f in facts],
            "citations": list(selected.evidence_source_ids), "pending_events": len(store.pending_feedback())}
    finally:
        if graph is not None:
            graph.close()
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--seed", action="store_true", help="Idempotently seed only the canonical synthetic SQL baseline")
    parser.add_argument("--write-restart-check", action="store_true", help="Append one synthetic failed observation, sync, and verify in a fresh process")
    parser.add_argument("--graph-backend", choices=("networkx", "neo4j", "auto"), default="networkx")
    parser.add_argument("--read-event", type=UUID, help=argparse.SUPPRESS)
    args = parser.parse_args()
    report: dict[str, Any]
    store = None
    graph = None
    write_attempted = False
    event = None
    try:
        if args.read_event:
            print(json.dumps(read_after_restart(args.env_file, args.read_event, args.graph_backend)))
            return 0
        store = SupabaseStore(load_supabase_settings(args.env_file))
        baseline = load_dataset(ROOT / "data")
        if args.seed or args.write_restart_check:
            store.seed(baseline)
        data = store.snapshot()
        if args.write_restart_check:
            graph = select_graph(args.graph_backend, args.env_file)
            graph.seed(data)
            before = store.fix_history("SYN-EQ-001", "SYN-FIX-001").outcomes
            event = new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", "did_not_work")
            write_attempted = True
            receipt = FeedbackService(store, graph).submit(event)
            # Closing clients does not clear server-side records. The child has
            # a fresh interpreter, independent store client, and new graph object.
            graph.close()
            graph = None
            store.close()
            store = None
            child = subprocess.run([sys.executable, "-m", "scripts.check_feedback", "--env-file", str(args.env_file),
                "--graph-backend", args.graph_backend, "--read-event", str(event.event_id)],
                cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=60, check=False)
            if child.returncode:
                raise RuntimeError("Fresh-process verification failed")
            report = json.loads(child.stdout)
            if (report["worked"] != before.worked or report["did_not_work"] != before.did_not_work + 1
                    or event.work_order.work_order_id not in report["citations"]):
                raise ValueError("Persisted counts or citations do not match feedback")
            report["initial_sync_state"] = receipt.sync_state
            report["before"] = before.model_dump(mode="json")
        else:
            report = {"status": "PASS", "check": "read-only snapshot" if not args.seed else "baseline seed/readback",
                      "storage": store.provider, "work_orders": len(data.work_orders),
                      "restart_verification": "NOT_RUN; use --write-restart-check"}
        exit_code = 0
    except Exception:
        report = {"status": "FAIL" if write_attempted else "NOT_RUN",
                  "reason": "Persistence verification incomplete after a write attempt; the event may have committed. Reuse its event ID." if write_attempted else
                  "Supabase configuration/schema or required graph service unavailable; no persistence success is claimed."}
        if event is not None:
            report.update(event_id=str(event.event_id), work_order_id=event.work_order.work_order_id)
        exit_code = 2
    finally:
        if graph is not None:
            graph.close()
        if store is not None:
            store.close()
    target = ROOT / "evals/results/feedback-live.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
