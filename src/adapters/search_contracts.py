"""Embedding and document-index ports; concrete external I/O stays in adapters."""

from typing import Literal, Protocol

from src.retrieval.models import EmbeddingBatch, IndexConfiguration, SearchHit


class EmbeddingAdapter(Protocol):
    model: str
    dimension: int

    def embed(self, texts: tuple[str, ...], purpose: Literal["query", "document"]) -> EmbeddingBatch: ...


class DocumentIndexAdapter(Protocol):
    configuration: IndexConfiguration
    embedding_model: str

    def dense_search(self, vector: tuple[float, ...], top_k: int, equipment_id: str | None = None) -> tuple[SearchHit, ...]: ...

    def lexical_search(self, query: str, top_k: int, equipment_id: str | None = None) -> tuple[SearchHit, ...]: ...
