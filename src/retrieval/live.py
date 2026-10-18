"""Explicit provider composition and bounded visibility checks, never on import."""

from dataclasses import dataclass
import time

from src.adapters.base import AdapterUnavailable
from src.adapters.fireworks_embeddings import FireworksEmbeddingAdapter
from src.adapters.pinecone_documents import IndexCompatibilityError, PineconeDocumentAdapter
from src.adapters.search_settings import SearchSettings
from src.retrieval.dense import DenseRetriever
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.models import CorpusDocument
from src.pdf_models import RAGSourceMetadata


@dataclass
class LiveRetrieval:
    index: PineconeDocumentAdapter
    embedder: FireworksEmbeddingAdapter
    documents: tuple[CorpusDocument, ...]
    source_metadata: dict[str, RAGSourceMetadata] | None = None

    @property
    def dense(self) -> DenseRetriever:
        return DenseRetriever(self.embedder, self.index)

    @property
    def hybrid(self) -> HybridRetriever:
        return HybridRetriever(self.embedder, self.index)

    def index_documents(self) -> int:
        from src.adapters.langchain_retrieval import ingest_with_langchain

        count = ingest_with_langchain(self.embedder, self.index, self.documents,
            self.source_metadata, self.index.upsert)
        for attempt in range(6):
            try:
                self.index.verify_visible()
                return count
            except AdapterUnavailable:
                if attempt == 5:
                    raise
                time.sleep(0.5)
        raise AdapterUnavailable("Index visibility could not be verified")

    def close(self) -> None:
        try:
            self.embedder.close()
        finally:
            self.index.close()


def open_live_retrieval(settings: SearchSettings, documents: tuple[CorpusDocument, ...],
                        create_index: bool = False, new_dimension: int = 1024,
                        namespace_tag: str | None = None,
                        source_metadata: dict[str, RAGSourceMetadata] | None = None) -> LiveRetrieval:
    def probe(dimension: int) -> None:
        embedder = FireworksEmbeddingAdapter(settings.fireworks_api_key.get_secret_value(), settings.embedding_model, dimension, settings.ca_file)
        try:
            embedder.embed(("Synthetic document compatibility probe.",), "document")
            embedder.embed(("Synthetic query compatibility probe.",), "query")
        finally:
            embedder.close()

    index = PineconeDocumentAdapter.connect(settings, documents, new_dimension if create_index else None, probe,
        namespace_tag=namespace_tag or 'lc-v1', source_metadata=source_metadata)
    embedder = None
    try:
        if index.configuration.text_field is None:
            raise IndexCompatibilityError("Existing index has no full-text field; dense+hybrid workflow requires a new compatible index. Existing schema was not changed.")
        embedder = FireworksEmbeddingAdapter(settings.fireworks_api_key.get_secret_value(), settings.embedding_model, index.configuration.dimension, settings.ca_file)
        embedder.embed(("Synthetic query compatibility probe.",), "query")
        return LiveRetrieval(index=index, embedder=embedder, documents=documents, source_metadata=source_metadata)
    except Exception:
        if embedder is not None:
            embedder.close()
        index.close()
        raise
