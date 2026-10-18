"""Opt-in real Streamlit PDF citation smoke; no feedback observations submitted."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def click(app: Any, label: str) -> None:
    next(button for button in app.button if button.label == label).click().run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("NOT_RUN: pass --live after configuring services and ingesting the PDF namespace")
        return 2
    from streamlit.testing.v1 import AppTest

    output = ROOT / "evals/results" / datetime.now(timezone.utc).strftime("pdf-ui-live-%Y%m%dT%H%M%S%fZ.json")
    observations: list[dict[str, Any]] = []
    application = None
    status = "FAIL"
    reason = None
    try:
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
        app.selectbox(key="graph_backend").select("neo4j").run()
        click(app, "Connect services")
        application = app.session_state["application"]
        if application.pdf_dataset is None:
            raise ValueError("PDF mode is not configured")
        for role in ("Operator", "Technician", "Supervisor"):
            app.selectbox(key="demo_role").select(role).run()
            app.text_input(key="alarm_code").set_value("SYN-ALM-004")
            app.text_input(key="question").set_value("Explain fix and parts for")
            click(app, "Retrieve diagnosis")
            result = app.session_state["diagnosis_result"]
            captions = "\n".join(str(c.value) for c in app.caption)
            if (app.exception or result.response.status != "supported" or result.response.candidates[0].fix_id != "SYN-FIX-004"
                    or "linemedic-synthetic-manual-1.pdf" not in captions or "physical page 7" not in captions
                    or "SYN-DOC-002" not in captions or result.decision.route != "combined"
                    or application.graph.status.mode != "live" or application._retrieval is None
                    or "-pdf-" not in application._retrieval.index.namespace):
                raise ValueError("PDF live UI did not meet the smoke contract")
            observations.append({"role": role, "status": "PASS", "result": result.model_dump(mode="json"),
                "namespace": application._retrieval.index.namespace,
                "pdf_caption": [c.value for c in app.caption if "Verified compilation PDF" in str(c.value)]})
        status = "PASS"
    except Exception:
        reason = "Live UI configuration, provider or grounded PDF citation check failed; details withheld"
    finally:
        if application:
            application.close()
    report = {"status": status, "synthetic": True, "observations": observations, "reason": reason,
        "feedback_submissions": 0, "connection_behavior": "Normal app connect performs idempotent Supabase/graph seeding"}
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (ROOT / "evals/results/latest-pdf-ui.txt").write_text(output.name + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "roles_passed": len(observations), "output": str(output), "reason": reason}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
