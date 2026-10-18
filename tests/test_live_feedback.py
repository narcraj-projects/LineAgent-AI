"""Opt-in real hosted Supabase append/restart check. Mutates synthetic demo only."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("LINEMEDIC_RUN_LIVE_FEEDBACK_TESTS") != "1",
    reason="Hosted Supabase feedback not opted in; configure credentials, apply migration, then set LINEMEDIC_RUN_LIVE_FEEDBACK_TESTS=1.")
def test_hosted_supabase_feedback_survives_fresh_process():
    backend = os.environ.get("LINEMEDIC_LIVE_FEEDBACK_GRAPH_BACKEND", "networkx")
    result = subprocess.run([sys.executable, "-m", "scripts.check_feedback", "--write-restart-check", "--graph-backend", backend],
        capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout
    report = json.loads(Path("evals/results/feedback-live.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["storage"] == "supabase:live"
    assert report["work_order_id"] in report["citations"]
