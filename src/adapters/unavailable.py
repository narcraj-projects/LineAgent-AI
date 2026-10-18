"""Explicit fail-closed retrieval boundary; never simulates a provider response."""

from src.adapters.base import AdapterStatus, AdapterUnavailable
from src.schemas import DiagnosisRequest, Evidence


class UnavailableRetrievalAdapter:
    def __init__(self, provider: str) -> None:
        self.status = AdapterStatus(provider=provider)

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        raise AdapterUnavailable(f"{self.status.provider}: {self.status.reason}")
