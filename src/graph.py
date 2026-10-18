"""Explicit backend selection and independent canonical-fixture SQL rate audit."""

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Literal

from src.adapters.base import AdapterUnavailable
from src.adapters.contracts import GraphAdapter
from src.adapters.neo4j_graph import Neo4jGraphAdapter
from src.adapters.neo4j_settings import load_neo4j_settings
from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.data_models import Dataset

GraphBackend = Literal["auto", "neo4j", "networkx"]


def select_graph(backend: GraphBackend = "auto", env_file: Path | None = None) -> GraphAdapter:
    """Connect only; never seed or switch backends during a running operation."""
    if backend not in {"auto", "neo4j", "networkx"}:
        raise ValueError("Unknown graph backend")
    if backend == "networkx":
        return NetworkXGraphAdapter("Explicit NetworkX fallback; in-memory state only.")
    try:
        settings = load_neo4j_settings(env_file)
        return Neo4jGraphAdapter.connect(settings)
    except Exception:
        if backend == "neo4j":
            raise AdapterUnavailable("Neo4j unavailable; explicit live mode does not use fallback.") from None
        return NetworkXGraphAdapter("Neo4j configuration or connection unavailable; using in-memory NetworkX fallback.")


def audit_rates(graph: GraphAdapter, data: Dataset) -> int:
    """Compare to SQLite counts of canonical synthetic records, not Supabase."""
    modes = {mode.failure_mode_id: mode for mode in data.failure_modes}
    pairs = sorted({(alarm.equipment_id, fix_id) for alarm in data.alarms for fix_id in modes[alarm.failure_mode_id].fix_ids})
    with closing(sqlite3.connect(":memory:")) as sql:
        sql.execute("CREATE TABLE work_orders (id TEXT PRIMARY KEY, equipment TEXT, fix TEXT, outcome TEXT)")
        sql.executemany("INSERT INTO work_orders VALUES (?, ?, ?, ?)", [
            (w.work_order_id, w.equipment_id, w.fix_id, w.outcome) for w in data.work_orders
        ])
        for equipment, fix in pairs:
            total, worked = sql.execute(
                "SELECT COUNT(DISTINCT id), COUNT(DISTINCT CASE WHEN outcome = 'worked' THEN id END) "
                "FROM work_orders WHERE equipment = ? AND fix = ?", (equipment, fix),
            ).fetchone()
            history = graph.fix_history(equipment, fix)
            if (len(history.work_orders), history.outcomes.worked) != (total, worked):
                raise ValueError("Graph rate disagrees with canonical SQLite fixture")
    return len(pairs)
