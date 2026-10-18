"""Provider-independent graph results and distinct, equipment-scoped history."""

from typing import Literal

from pydantic import Field, model_validator

from src.data_models import ManualSection, WorkOrder
from src.schemas import Contract, NonEmpty, OutcomeCounts


class GraphStatus(Contract):
    provider: Literal["neo4j", "networkx"]
    mode: Literal["live", "fallback"]
    reason: NonEmpty

    @model_validator(mode="after")
    def validate_mode(self) -> "GraphStatus":
        if (self.provider == "neo4j") != (self.mode == "live"):
            raise ValueError("Provider and mode disagree")
        return self


class GraphCounts(Contract):
    nodes: int = Field(ge=0)
    relationships: int = Field(ge=0)


class FixHistory(Contract):
    equipment_id: NonEmpty
    fix_id: NonEmpty
    work_orders: tuple[WorkOrder, ...] = ()
    rate_basis: Literal["synthetic_graph_work_orders", "synthetic_sql_snapshot_work_orders", "supabase_work_orders"] = "synthetic_graph_work_orders"

    @model_validator(mode="after")
    def validate_context(self) -> "FixHistory":
        ids = [order.work_order_id for order in self.work_orders]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate work-order ID in history")
        if any(w.equipment_id != self.equipment_id or w.fix_id != self.fix_id for w in self.work_orders):
            raise ValueError("History equipment or fix mismatch")
        return self

    @property
    def outcomes(self) -> OutcomeCounts:
        worked = sum(w.outcome == "worked" for w in self.work_orders)
        return OutcomeCounts(worked=worked, did_not_work=len(self.work_orders) - worked)


class GraphFixPath(Contract):
    status: GraphStatus
    alarm_code: NonEmpty
    alarm_id: NonEmpty
    equipment_id: NonEmpty
    failure_mode_id: NonEmpty
    fix_id: NonEmpty
    part_numbers: tuple[NonEmpty, ...]
    paths: tuple[tuple[NonEmpty, ...], ...] = Field(min_length=1)
    manuals: tuple[ManualSection, ...]
    history: FixHistory

    @model_validator(mode="after")
    def validate_provenance(self) -> "GraphFixPath":
        if self.history.equipment_id != self.equipment_id or self.history.fix_id != self.fix_id:
            raise ValueError("Path and history context disagree")
        base = (self.alarm_id, self.failure_mode_id, self.fix_id)
        expected = {(*base, part) for part in self.part_numbers} or {base}
        if set(self.paths) != expected:
            raise ValueError("Graph paths disagree with returned entities")
        if len({m.section_id for m in self.manuals}) != len(self.manuals):
            raise ValueError("Duplicate manual source ID")
        for manual in self.manuals:
            if self.equipment_id not in manual.equipment_ids or self.fix_id not in manual.fix_ids or manual.failure_mode_id != self.failure_mode_id:
                raise ValueError("Manual provenance context mismatch")
        return self
