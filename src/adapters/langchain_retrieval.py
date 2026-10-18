"""LangChain Embeddings, VectorStore and retriever around audited provider ports.

Pinecone's v10 documents/full-text surface and canonical validation remain in its
adapter. No unrestricted LangChain SQL/Cypher tool or mutable general upload API.
"""
from collections.abc import Callable, Iterable
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langsmith import tracing_context

from src.adapters.langchain_documents import corpus_documents
from src.adapters.search_contracts import DocumentIndexAdapter, EmbeddingAdapter
from src.pdf_models import RAGSourceMetadata
from src.retrieval.models import CorpusDocument, EmbeddingBatch, SearchHit


class ValidatedEmbeddings(Embeddings):
    """LangChain embedding interface; keep verified preprocessing and identities."""

    def __init__(self, adapter: EmbeddingAdapter) -> None:
        self.adapter = adapter

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            raise ValueError("No document texts")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 16):
            group = tuple(texts[start:start + 16])
            batch = self.adapter.embed(group, "document")
            self._validate(batch, len(group))
            vectors.extend([list(vector) for vector in batch.vectors])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        batch = self.adapter.embed((text,), "query")
        self._validate(batch, 1)
        return list(batch.vectors[0])

    def _validate(self, batch: EmbeddingBatch, count: int) -> None:
        checked = EmbeddingBatch.model_validate_json(batch.model_dump_json())
        if (checked.model != self.adapter.model or checked.dimension != self.adapter.dimension
                or len(checked.vectors) != count):
            raise ValueError("LangChain embedding result conflicts with the configured model")


class _IngestionEmbeddingPort:
    """Let the existing upsert validator request embeddings via LangChain."""

    def __init__(self, embeddings: ValidatedEmbeddings) -> None:
        self.embeddings = embeddings
        self.model = embeddings.adapter.model
        self.dimension = embeddings.adapter.dimension

    def embed(self, texts: tuple[str, ...], purpose: Literal["query", "document"]) -> EmbeddingBatch:
        if purpose != "document":
            raise ValueError("Ingestion only embeds documents")
        vectors = self.embeddings.embed_documents(list(texts))
        return EmbeddingBatch(model=self.model, dimension=self.dimension,
            vectors=tuple(tuple(vector) for vector in vectors))


class AuditedVectorStore(VectorStore):
    """LangChain owns add_documents/as_retriever; application owns evidence checks."""

    def __init__(self, embedder: EmbeddingAdapter, index: DocumentIndexAdapter,
                 corpus: tuple[CorpusDocument, ...] = (),
                 metadata: dict[str, RAGSourceMetadata] | None = None,
                 upsert: Callable[[EmbeddingAdapter], int] | None = None) -> None:
        if embedder.model != index.embedding_model or embedder.dimension != index.configuration.dimension:
            raise ValueError("Embedding and index model/dimensions disagree")
        self._embeddings = ValidatedEmbeddings(embedder)
        self.index, self.corpus, self.metadata, self.upsert = index, corpus, metadata, upsert

    @property
    def embeddings(self) -> Embeddings:
        return self._embeddings

    def add_documents(self, documents: list[Document], **kwargs: Any) -> list[str]:
        if kwargs or self.upsert is None or not self.corpus:
            raise ValueError("Only explicit full verified corpus ingestion is supported")
        expected = corpus_documents(self.corpus, self.metadata)
        if (len(documents) != len(expected) or len({doc.id for doc in documents}) != len(documents)
                or {doc.id: doc for doc in documents} != {doc.id: doc for doc in expected}):
            raise ValueError("LangChain documents disagree with the verified corpus/provenance")
        count = self.upsert(_IngestionEmbeddingPort(self._embeddings))
        if count != len(expected):
            raise ValueError("Incomplete ingestion acknowledgement")
        return [source.source_id for source in self.corpus]

    def add_texts(self, texts: Iterable[str], metadatas: list[dict[str, Any]] | None = None,
                  *, ids: list[str] | None = None, **kwargs: Any) -> list[str]:
        raise ValueError("Use verified canonical Documents, not arbitrary text uploads")

    @classmethod
    def from_texts(cls, texts: list[str], embedding: Embeddings,
                   metadatas: list[dict[str, Any]] | None = None, **kwargs: Any) -> "AuditedVectorStore":
        raise ValueError("An explicit verified corpus and configured index are required")

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        return [document for document, _ in self.similarity_search_with_score(query, k, **kwargs)]

    def similarity_search_with_score(self, query: str, k: int = 4,
                                     **kwargs: Any) -> list[tuple[Document, float]]:
        if not 1 <= k <= 100 or set(kwargs) - {"equipment_id", "mode"}:
            raise ValueError("Invalid bounded search options")
        equipment_id, mode = kwargs.get("equipment_id"), kwargs.get("mode", "dense")
        if mode not in {"dense", "hybrid"} or (equipment_id is not None and not isinstance(equipment_id, str)):
            raise ValueError("Invalid search context or mode")
        if mode == "hybrid" and self.index.configuration.text_field is None:
            raise ValueError("Hybrid search requires a full-text field")
        vector = self._embeddings.embed_query(query)
        size = min(100, max(20, k)) if mode == "hybrid" else k
        hits = self.index.dense_search(tuple(vector), size, equipment_id)
        if mode == "hybrid":
            from src.retrieval.hybrid import fuse_hits

            lexical = self.index.lexical_search(query, size, equipment_id)
            hits = fuse_hits(hits, lexical, k)
        checked = tuple(SearchHit.model_validate_json(hit.model_dump_json()) for hit in hits)
        if (len(checked) > k or len({h.document.source_id for h in checked}) != len(checked)
                or any(equipment_id and equipment_id not in h.document.equipment_ids for h in checked)):
            raise ValueError("Invalid or conflicting search results")
        return [(Document(id=hit.document.source_id, page_content=hit.document.text,
            metadata={"search_hit": hit.model_dump_json()}), hit.score) for hit in checked]


def search_with_langchain(embedder: EmbeddingAdapter, index: DocumentIndexAdapter, query: str,
                          top_k: int, equipment_id: str | None,
                          mode: Literal["dense", "hybrid"]) -> tuple[SearchHit, ...]:
    store = AuditedVectorStore(embedder, index)
    retriever = store.as_retriever(search_kwargs={"k": top_k, "equipment_id": equipment_id, "mode": mode})
    with tracing_context(enabled=False):
        documents = retriever.invoke(query, config={"callbacks": []})
    hits: list[SearchHit] = []
    for document in documents:
        if set(document.metadata) != {"search_hit"}:
            raise ValueError("Unknown retriever metadata")
        hit = SearchHit.model_validate_json(document.metadata["search_hit"])
        if document.id != hit.document.source_id or document.page_content != hit.document.text:
            raise ValueError("LangChain retriever altered a canonical source")
        hits.append(hit)
    return tuple(hits)


def ingest_with_langchain(embedder: EmbeddingAdapter, index: DocumentIndexAdapter,
                          corpus: tuple[CorpusDocument, ...], metadata: dict[str, RAGSourceMetadata] | None,
                          upsert: Callable[[EmbeddingAdapter], int],
                          documents: list[Document] | None = None) -> int:
    store = AuditedVectorStore(embedder, index, corpus, metadata, upsert)
    with tracing_context(enabled=False):
        ids = store.add_documents(documents if documents is not None else corpus_documents(corpus, metadata))
    if ids != [source.source_id for source in corpus]:
        raise ValueError("LangChain ingestion returned conflicting canonical IDs")
    return len(ids)
