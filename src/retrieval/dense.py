"""Independently callable dense-only retrieval through typed external adapters."""

from src.adapters.search_contracts import DocumentIndexAdapter, EmbeddingAdapter
from src.retrieval.models import SearchHit
from src.schemas import DiagnosisRequest, Evidence


class DenseRetriever:
    def __init__(self, embedder: EmbeddingAdapter, index: DocumentIndexAdapter) -> None:
        if embedder.dimension != index.configuration.dimension or embedder.model != index.embedding_model:
            raise ValueError("Embedding and index model/dimensions disagree")
        self.embedder, self.index = embedder, index

    def search(self, query: str, top_k: int = 5, equipment_id: str | None = None) -> tuple[SearchHit, ...]:
        request = DiagnosisRequest(query=query, equipment_id=equipment_id)
        if not 1 <= top_k <= 100:
            raise ValueError("Search top_k must be in 1..100")
        batch = self.embedder.embed((request.query,), "query")
        if batch.model != self.embedder.model or batch.dimension != self.index.configuration.dimension or len(batch.vectors) != 1:
            raise ValueError("Query embedding does not match retrieval configuration")
        return self.index.dense_search(batch.vectors[0], top_k, equipment_id)

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        query = f"{request.alarm_code} {request.query}" if request.alarm_code else request.query
        return tuple(Evidence(source_id=hit.document.source_id, text=hit.document.text,
                              source_kind=hit.document.source_kind, provider=f"{hit.provider}:{hit.score_kind}",
                              score=hit.score) for hit in self.search(query, equipment_id=request.equipment_id))
