"""In-memory fallback; graph reads use actual stored nodes and relationships."""

from typing import TypeVar

import networkx as nx

from src.adapters.graph_common import node_records, path_evidence
from src.data_models import Alarm, Dataset, ManualSection, SyntheticRecord, WorkOrder, graph_relationships
from src.graph_models import FixHistory, GraphCounts, GraphFixPath, GraphStatus
from src.schemas import DiagnosisRequest, Evidence

RecordT = TypeVar("RecordT", bound=SyntheticRecord)


class NetworkXGraphAdapter:
    def __init__(self, reason: str = "Explicit local fallback; no Neo4j connection.") -> None:
        self.status = GraphStatus(provider="networkx", mode="fallback", reason=reason)
        self._graph = nx.MultiDiGraph()

    def seed(self, data: Dataset) -> GraphCounts:
        # Revalidate even callers that used Pydantic's unchecked model_copy/update.
        data = Dataset.model_validate_json(data.model_dump_json())
        records = tuple(node_records(data))
        for label, key, canonical_id, record in records:
            self._graph.add_node(canonical_id, label=label, record=record)
        for edge in graph_relationships(data):
            self._graph.add_edge(edge.source_id, edge.target_id, key=edge.relationship)
        return self.counts()

    def counts(self) -> GraphCounts:
        return GraphCounts(nodes=self._graph.number_of_nodes(), relationships=self._graph.number_of_edges())

    def _record(self, node_id: str, record_type: type[RecordT]) -> RecordT:
        record = self._graph.nodes[node_id]["record"]
        if not isinstance(record, record_type):
            raise ValueError("Graph node type mismatch")
        return record

    def _targets(self, node_id: str, relation: str) -> tuple[str, ...]:
        return tuple(sorted({target for _, target, key in self._graph.out_edges(node_id, keys=True) if key == relation}))

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory:
        orders: dict[str, WorkOrder] = {}
        if equipment_id in self._graph and fix_id in self._graph:
            for source, _, relation in self._graph.in_edges(equipment_id, keys=True):
                if relation == "ON" and self._graph.has_edge(source, fix_id, key="APPLIED"):
                    order = self._record(source, WorkOrder)
                    orders[order.work_order_id] = order
        return FixHistory(equipment_id=equipment_id, fix_id=fix_id, work_orders=tuple(orders[key] for key in sorted(orders)))

    def alarm_paths(self, alarm_code: str, equipment_id: str | None = None) -> tuple[GraphFixPath, ...]:
        alarms = [attrs["record"] for _, attrs in self._graph.nodes(data=True)
                  if isinstance(attrs["record"], Alarm) and attrs["record"].code == alarm_code]
        results = []
        for alarm in alarms:
            for eq in self._targets(alarm.alarm_id, "RAISED_BY"):
                if equipment_id is not None and eq != equipment_id:
                    continue
                for mode in self._targets(alarm.alarm_id, "INDICATES"):
                    for fix in self._targets(mode, "RESOLVED_BY"):
                        parts = self._targets(fix, "REQUIRES")
                        manuals = {}
                        for node, _, relation in self._graph.in_edges(eq, keys=True):
                            if relation == "DESCRIBES" and self._graph.has_edge(node, fix, key="DESCRIBES"):
                                manual = self._record(node, ManualSection)
                                if manual.failure_mode_id == mode:
                                    manuals[manual.section_id] = manual
                        base = (alarm.alarm_id, mode, fix)
                        results.append(GraphFixPath(
                            status=self.status, alarm_code=alarm.code, alarm_id=alarm.alarm_id,
                            equipment_id=eq, failure_mode_id=mode, fix_id=fix, part_numbers=parts,
                            paths=tuple((*base, part) for part in parts) or (base,),
                            manuals=tuple(manuals[key] for key in sorted(manuals)),
                            history=self.fix_history(eq, fix),
                        ))
        return tuple(sorted(results, key=lambda item: (item.equipment_id, item.fix_id)))

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        if not request.alarm_code:
            return ()
        return path_evidence(self.alarm_paths(request.alarm_code, request.equipment_id))

    def close(self) -> None:
        self._graph.clear()

    def upsert_work_order(self, order: WorkOrder) -> None:
        order = WorkOrder.model_validate_json(order.model_dump_json())
        alarm = next((attrs["record"] for _, attrs in self._graph.nodes(data=True)
            if isinstance(attrs["record"], Alarm) and attrs["record"].code == order.alarm_code), None)
        if (not order.work_order_id.startswith("SYN-WO-") or alarm is None
                or order.equipment_id != alarm.equipment_id or order.fix_id not in self._graph
                or not self._graph.has_edge(alarm.failure_mode_id, order.fix_id, key="RESOLVED_BY")):
            raise ValueError("Synthetic graph feedback context mismatch")
        if order.work_order_id in self._graph and self._record(order.work_order_id, WorkOrder) != order:
            raise ValueError("Append-only work-order conflict")
        self._graph.add_node(order.work_order_id, label="WorkOrder", record=order)
        self._graph.add_edge(order.work_order_id, order.equipment_id, key="ON")
        self._graph.add_edge(order.work_order_id, order.fix_id, key="APPLIED")
