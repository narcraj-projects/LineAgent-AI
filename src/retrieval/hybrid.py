"""Hybrid semantic/BM25 retrieval via explicit weighted reciprocal-rank fusion."""

from src.adapters.base import AdapterUnavailable
from src.retrieval.dense import DenseRetriever
from src.retrieval.models import SearchHit


def fuse_hits(dense: tuple[SearchHit, ...], lexical: tuple[SearchHit, ...], top_k: int = 5) -> tuple[SearchHit, ...]:
    if not 1 <= top_k <= 100:
        raise ValueError("Search top_k must be in 1..100")
    merged: dict[str, SearchHit] = {}
    scores: dict[str, float] = {}
    raw: dict[str, dict[str, float | None]] = {}
    for hits, weight, field in ((dense, 1.0, "dense_score"), (lexical, 2.0, "lexical_score")):
        seen = set()
        for rank, hit in enumerate(hits, 1):
            source_id = hit.document.source_id
            if source_id in seen:
                raise ValueError("Duplicate citation ID within a retrieval component")
            seen.add(source_id)
            if source_id in merged and merged[source_id].document != hit.document:
                raise ValueError("Retrieval components disagree about source content")
            if source_id in merged and merged[source_id].source_metadata != hit.source_metadata:
                raise ValueError("Retrieval components disagree about source metadata")
            merged[source_id] = hit
            scores[source_id] = scores.get(source_id, 0.0) + weight / (60 + rank)
            raw.setdefault(source_id, {"dense_score": None, "lexical_score": None})[field] = hit.score
    ordered = sorted(merged, key=lambda source_id: (-scores[source_id], source_id))[:top_k]
    return tuple(SearchHit(document=merged[source_id].document, provider=merged[source_id].provider,
                           score=scores[source_id], score_kind="weighted_rrf",
                           source_metadata=merged[source_id].source_metadata, **raw[source_id]) for source_id in ordered)


class HybridRetriever(DenseRetriever):
    def search(self, query: str, top_k: int = 5, equipment_id: str | None = None) -> tuple[SearchHit, ...]:
        if not 1 <= top_k <= 100:
            raise ValueError("Search top_k must be in 1..100")
        if self.index.configuration.text_field is None:
            raise AdapterUnavailable("Hybrid search unavailable: index has no full-text field.")
        dense = super().search(query, min(100, max(20, top_k)), equipment_id)
        lexical = self.index.lexical_search(query, min(100, max(20, top_k)), equipment_id)
        return fuse_hits(dense, lexical, top_k)
