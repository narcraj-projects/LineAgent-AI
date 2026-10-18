"""Read-only SQL evidence port shared by snapshot and future durable adapters."""

from typing import Protocol

from src.graph_models import FixHistory
from src.schemas import DiagnosisRequest, Evidence


class SQLEvidenceAdapter(Protocol):
    provider: str

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]: ...

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory: ...
