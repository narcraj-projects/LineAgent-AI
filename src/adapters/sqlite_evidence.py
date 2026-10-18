"""Parameterized read adapter over a local synthetic snapshot, not Supabase."""

import sqlite3

from src.data_models import Dataset, WorkOrder
from src.graph_models import FixHistory
from src.retrieval.corpus import build_corpus
from src.schemas import DiagnosisRequest, Evidence


class SQLiteEvidenceAdapter:
    provider = "sqlite:synthetic-snapshot"

    def __init__(self, data: Dataset) -> None:
        data = Dataset.model_validate_json(data.model_dump_json())
        self._db = sqlite3.connect(":memory:")
        self._db.executescript(
            "CREATE TABLE sources(id TEXT PRIMARY KEY, kind TEXT, body TEXT, equipment TEXT, alarm TEXT);"
            "CREATE TABLE orders(id TEXT PRIMARY KEY, equipment TEXT, fix TEXT, payload TEXT);"
        )
        self._db.executemany("INSERT INTO sources VALUES(?,?,?,?,?)", (
            (doc.source_id + ':' + eq + ':' + alarm, doc.source_kind, doc.text, eq, alarm)
            for doc in build_corpus(data) for eq in doc.equipment_ids for alarm in doc.alarm_codes
            if any(a.code == alarm and a.equipment_id == eq for a in data.alarms)
        ))
        self._db.executemany("INSERT INTO orders VALUES(?,?,?,?)", (
            (w.work_order_id, w.equipment_id, w.fix_id, w.model_dump_json()) for w in data.work_orders
        ))

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        rows = self._db.execute(
            "SELECT DISTINCT id,kind,body FROM sources WHERE (? IS NULL OR alarm=?) AND (? IS NULL OR equipment=?) ORDER BY id",
            (request.alarm_code, request.alarm_code, request.equipment_id, request.equipment_id),
        ).fetchall()
        sources: dict[str, Evidence] = {}
        for key, kind, body in rows:
            source_id = key.split(':', 1)[0]
            sources[source_id] = Evidence(source_id=source_id, source_kind=kind, text=body, provider=self.provider)
        return tuple(sources[key] for key in sorted(sources))

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory:
        rows = self._db.execute(
            "SELECT payload FROM orders WHERE equipment=? AND fix=? ORDER BY id", (equipment_id, fix_id),
        ).fetchall()
        return FixHistory(equipment_id=equipment_id, fix_id=fix_id, rate_basis="synthetic_sql_snapshot_work_orders",
                          work_orders=tuple(WorkOrder.model_validate_json(row[0]) for row in rows))

    def close(self) -> None:
        self._db.close()
