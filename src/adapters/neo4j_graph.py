"""Neo4j implementation of the same port as the in-memory NetworkX fallback."""

from __future__ import annotations

from collections import defaultdict
import json
import ssl
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from src.adapters.base import AdapterUnavailable
from src.adapters.cypher import CONSTRAINTS, TEMPLATES
from src.adapters.graph_common import NAMESPACE, node_records, path_evidence
from src.adapters.neo4j_settings import Neo4jSettings
from src.data_models import Dataset, ManualSection, WorkOrder, graph_relationships
from src.graph_models import FixHistory, GraphCounts, GraphFixPath, GraphStatus
from src.schemas import Contract, DiagnosisRequest, Evidence, NonEmpty

if TYPE_CHECKING:
    from neo4j import Driver, ManagedTransaction


class Neo4jPathRow(Contract):
    alarm_code: NonEmpty
    alarm_id: NonEmpty
    equipment_id: NonEmpty
    failure_mode_id: NonEmpty
    fix_id: NonEmpty
    parts: tuple[NonEmpty, ...]
    base_path: tuple[NonEmpty, NonEmpty, NonEmpty]
    history: tuple[NonEmpty, ...]
    attempts: int = Field(ge=0)
    worked: int = Field(ge=0)
    manuals: tuple[NonEmpty, ...]


class Neo4jGraphAdapter:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database
        self.status = GraphStatus(provider="neo4j", mode="live", reason="Live Neo4j connection; no automatic seeding.")

    @classmethod
    def connect(cls, settings: Neo4jSettings) -> Neo4jGraphAdapter:
        driver = None
        try:
            from neo4j import GraphDatabase, Query

            uri = settings.uri.get_secret_value()
            options: dict[str, Any] = {
                "connection_timeout": 5.0, "connection_acquisition_timeout": 10.0,
                "max_transaction_retry_time": 0.0,
            }
            if settings.ca_file is not None:
                # +s disallows ssl_context. Equivalent explicit settings retain
                # encryption, CA validation and hostname verification.
                parsed = urlsplit(uri)
                if parsed.scheme not in {"neo4j+s", "bolt+s"}:
                    raise ValueError("A custom CA bundle requires a verified TLS URI")
                uri = urlunsplit((parsed.scheme.split("+")[0], parsed.netloc, parsed.path, "", ""))
                options["encrypted"] = True
                options["ssl_context"] = ssl.create_default_context(cafile=str(settings.ca_file))
            driver = GraphDatabase.driver(
                uri, auth=(settings.username.get_secret_value(), settings.password.get_secret_value()), **options,
            )
            driver.verify_connectivity()
            with driver.session(database=settings.database, default_access_mode="READ") as session:
                probe = session.run(Query("RETURN 1 AS ok", timeout=10.0)).single()
                if probe is None or probe["ok"] != 1:
                    raise ValueError("Read-only probe failed")
            return cls(driver, settings.database)
        except Exception:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass
            raise AdapterUnavailable("Neo4j connection unavailable; provider details withheld.") from None

    def _read(self, template: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        if template not in {"counts", "alarm_paths", "fix_history"}:
            raise ValueError("Query is not an allowlisted read template")

        def read(tx: ManagedTransaction) -> list[dict[str, Any]]:
            return tx.run(TEMPLATES[template], {"namespace": NAMESPACE, **parameters}).data()

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(read)
        except Exception:
            raise AdapterUnavailable("Neo4j graph read failed; provider details withheld.") from None

    def seed(self, data: Dataset) -> GraphCounts:
        data = Dataset.model_validate_json(data.model_dump_json())
        batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for label, key, canonical_id, record in node_records(data):
            properties = record.model_dump(mode="json")
            properties.update(namespace=NAMESPACE, canonical_id=canonical_id, payload_json=record.model_dump_json())
            batches[f"seed_{label}"].append(properties)
        for edge in graph_relationships(data):
            batches[f"seed_{edge.relationship}"].append(edge.model_dump())

        def merge(tx: ManagedTransaction) -> None:
            for template, rows in batches.items():
                result = tx.run(TEMPLATES[template], namespace=NAMESPACE, rows=rows).single()
                if result is None or result["merged"] != len(rows):
                    raise ValueError("Incomplete synthetic graph merge")

        try:
            with self._driver.session(database=self._database) as session:
                for statement in CONSTRAINTS:
                    session.run(statement).consume()
                session.execute_write(merge)
            return self.counts()
        except Exception:
            raise AdapterUnavailable("Neo4j seed failed; provider details withheld.") from None

    def counts(self) -> GraphCounts:
        rows = self._read("counts", {})
        if len(rows) != 1:
            raise AdapterUnavailable("Neo4j returned invalid graph counts")
        return GraphCounts.model_validate(rows[0])

    def upsert_work_order(self, order: WorkOrder) -> None:
        order = WorkOrder.model_validate_json(order.model_dump_json())
        if not order.work_order_id.startswith("SYN-WO-"):
            raise ValueError("Only synthetic feedback may enter the graph")
        properties = order.model_dump(mode="json")
        properties.update(namespace=NAMESPACE, canonical_id=order.work_order_id, payload_json=order.model_dump_json())

        def merge(tx: ManagedTransaction) -> None:
            result = tx.run(TEMPLATES["upsert_work_order"], namespace=NAMESPACE,
                alarm_code=order.alarm_code, equipment_id=order.equipment_id, fix_id=order.fix_id,
                work_order_id=order.work_order_id, payload_json=order.model_dump_json(), properties=properties).single()
            if result is None or result["merged"] != 1:
                raise ValueError("Feedback graph context or append-only event conflict")

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(merge)
        except Exception:
            raise AdapterUnavailable("Graph feedback sync failed; durable outbox retry is required.") from None

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory:
        rows = self._read("fix_history", {"equipment_id": equipment_id, "fix_id": fix_id})
        orders = tuple(WorkOrder.model_validate_json(row["payload_json"]) for row in rows)
        return FixHistory(equipment_id=equipment_id, fix_id=fix_id, work_orders=tuple(sorted(orders, key=lambda w: w.work_order_id)))

    def alarm_paths(self, alarm_code: str, equipment_id: str | None = None) -> tuple[GraphFixPath, ...]:
        rows = self._read("alarm_paths", {"alarm_code": alarm_code, "equipment_id": equipment_id})
        result = []
        for raw in rows:
            row = Neo4jPathRow.model_validate_json(json.dumps(raw))
            history = FixHistory(
                equipment_id=row.equipment_id, fix_id=row.fix_id,
                work_orders=tuple(sorted((WorkOrder.model_validate_json(value) for value in row.history), key=lambda w: w.work_order_id)),
            )
            if row.attempts != len(history.work_orders) or row.worked != history.outcomes.worked:
                raise ValueError("Neo4j aggregate counts disagree with distinct work-order provenance")
            parts = tuple(sorted(row.parts))
            result.append(GraphFixPath(
                status=self.status, alarm_code=row.alarm_code, alarm_id=row.alarm_id,
                equipment_id=row.equipment_id, failure_mode_id=row.failure_mode_id,
                fix_id=row.fix_id, part_numbers=parts,
                paths=tuple((*row.base_path, part) for part in parts) or (row.base_path,),
                history=history,
                manuals=tuple(sorted((ManualSection.model_validate_json(value) for value in row.manuals), key=lambda m: m.section_id)),
            ))
        return tuple(sorted(result, key=lambda item: (item.equipment_id, item.fix_id)))

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        if not request.alarm_code:
            return ()
        return path_evidence(self.alarm_paths(request.alarm_code, request.equipment_id))

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            raise AdapterUnavailable("Neo4j close failed; provider details withheld.") from None
