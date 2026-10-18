"""Import modules in fresh offline processes without reloading shared model classes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULES = ["app", "scripts", "scripts.generate_data", "scripts.generate_pdfs", "scripts.ingest_pdfs", "scripts.check_pdf_ui", "scripts.check_pdf_retrieval", "scripts.check_neo4j", "scripts.check_graph", "scripts.index_pinecone", "scripts.diagnose", "scripts.check_feedback", "evals", "evals.models", "evals.scoring", "evals.run_suite", "evals.upload_traces", "evals.report", "evals.model_benchmark", "evals.hosted_embedding_benchmark"] + [
    ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
    for path in sorted((ROOT / "src").rglob("*.py"))
]


@pytest.mark.parametrize("module_name", MODULES)
def test_import_is_offline_and_requires_no_credentials(module_name: str) -> None:
    env = os.environ.copy()
    for key in (
        "FIREWORKS_API_KEY", "PINECONE_API_KEY", "NEO4J_URI", "NEO4J_USERNAME",
        "NEO4J_PASSWORD", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "LANGSMITH_API_KEY", "MEM0_API_KEY",
        "PINECONE_INDEX_NAME", "FIREWORKS_EMBEDDING_MODEL",
    ):
        env.pop(key, None)
    script = """
import importlib
import socket
import sys
def disallow_network(*args, **kwargs):
    raise AssertionError('Imports must not open network connections')
socket.socket.connect = disallow_network
socket.create_connection = disallow_network
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, module_name], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
