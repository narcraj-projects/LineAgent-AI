"""Injectable ports for future retrieval, generation, storage and tracing adapters."""

from typing import Protocol, runtime_checkable

from src.schemas import Contract, DiagnosisRequest, DiagnosisResponse, Evidence
from src.data_models import Dataset, WorkOrder
from src.graph_models import FixHistory, GraphCounts, GraphFixPath, GraphStatus


class RetrievalAdapter(Protocol):
    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]: ...


@runtime_checkable
class GraphAdapter(RetrievalAdapter, Protocol):
    """Common port for live Neo4j and explicitly labeled in-memory fallback."""

    status: GraphStatus

    def seed(self, data: Dataset) -> GraphCounts: ...

    def counts(self) -> GraphCounts: ...

    def alarm_paths(self, alarm_code: str, equipment_id: str | None = None) -> tuple[GraphFixPath, ...]: ...

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory: ...

    def upsert_work_order(self, order: WorkOrder) -> None: ...

    def close(self) -> None: ...


class GenerationAdapter(Protocol):
    def generate(
        self, request: DiagnosisRequest, evidence: tuple[Evidence, ...]
    ) -> DiagnosisResponse: ...


class StorageAdapter(Protocol):
    def get(self, collection: str, record_id: str) -> Contract | None: ...

    def save(self, collection: str, record_id: str, record: Contract) -> None: ...


class TraceAdapter(Protocol):
    def record(self, event: Contract) -> None: ...
