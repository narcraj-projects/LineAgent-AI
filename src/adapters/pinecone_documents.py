"""Pinecone v10 schema/document API; no legacy sparse/dense API assumptions."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Callable, TYPE_CHECKING

from src.adapters.base import AdapterUnavailable
from src.adapters.search_contracts import EmbeddingAdapter
from src.adapters.search_settings import SearchSettings
from src.retrieval.corpus import corpus_digest, exact_tokens
from src.retrieval.models import CorpusDocument, IndexConfiguration, SearchHit
from src.pdf_models import RAGSourceMetadata

if TYPE_CHECKING:
    from pinecone import Pinecone
    from pinecone.client.index import Index


class IndexCompatibilityError(ValueError):
    """Controlled schema incompatibility, with no API keys or raw provider body."""


def inspect_schema(description: Any, dense_override: str | None = None, text_override: str | None = None) -> IndexConfiguration:
    import msgspec

    fields = msgspec.to_builtins(description.schema)["fields"]
    dense_names = [name for name, field in fields.items() if field["type"] == "dense_vector"]
    text_names = [name for name, field in fields.items() if field["type"] == "string" and field.get("full_text_search") is not None]
    dense = dense_override or (dense_names[0] if len(dense_names) == 1 else None)
    text = text_override or (text_names[0] if len(text_names) == 1 else None)
    if dense not in dense_names:
        raise IndexCompatibilityError("Select one existing dense_vector field using PINECONE_DENSE_FIELD.")
    if text_override and text_override not in text_names:
        raise IndexCompatibilityError("PINECONE_TEXT_FIELD is not a full-text-enabled string field.")
    if not description.status.ready:
        raise IndexCompatibilityError("Pinecone index is not ready; no upsert or search attempted.")
    return IndexConfiguration(
        index_name=description.name, dimension=fields[dense]["dimension"], metric=fields[dense]["metric"],
        dense_field=dense, text_field=text, ready=description.status.ready, schema_fields=tuple(sorted(fields)),
    )


class PineconeDocumentAdapter:
    def __init__(self, client: Pinecone, index: Index, configuration: IndexConfiguration,
                 documents: tuple[CorpusDocument, ...], model: str, namespace_tag: str | None = None,
                 source_metadata: dict[str, RAGSourceMetadata] | None = None) -> None:
        if len({d.source_id for d in documents}) != len(documents):
            raise ValueError("Duplicate corpus source IDs")
        self.configuration = configuration
        self.embedding_model = model
        self._client, self._index = client, index
        self._corpus = {d.source_id: d for d in documents}
        self._source_metadata = dict(source_metadata or {})
        if self._source_metadata and self._source_metadata.keys() != self._corpus.keys():
            raise ValueError("Ingestion metadata must cover all source IDs")
        for source_id, metadata in self._source_metadata.items():
            doc = self._corpus[source_id]
            if (metadata.source_id != source_id or metadata.source_kind != doc.source_kind
                    or metadata.equipment_ids != doc.equipment_ids or metadata.alarm_codes != doc.alarm_codes
                    or metadata.part_numbers != doc.part_numbers
                    or (metadata.origin == "pdf" and (metadata.source_doc, metadata.source_version, metadata.logical_page)
                        != (doc.source_doc, doc.source_version, doc.page))
                    or metadata.text_sha256 != hashlib.sha256(doc.text.encode()).hexdigest()):
                raise ValueError("Ingestion metadata disagrees with canonical source")
        self._digest, self._model = corpus_digest(documents), model
        fingerprint = hashlib.sha256(f"{model}:{configuration.dimension}:l2-v1".encode()).hexdigest()[:12]
        if namespace_tag is not None and not re.fullmatch(r"[a-z0-9-]{1,40}", namespace_tag):
            raise ValueError("Namespace tag must be 1-40 lowercase letters, digits or hyphens")
        prefix = f"linemedic-{namespace_tag}" if namespace_tag else "linemedic"
        self.namespace = f"{prefix}-{self._digest[:16]}-{fingerprint}"

    @classmethod
    def connect(cls, settings: SearchSettings, documents: tuple[CorpusDocument, ...],
                create_dimension: int | None = None,
                before_create: Callable[[int], None] | None = None,
                namespace_tag: str | None = None,
                source_metadata: dict[str, RAGSourceMetadata] | None = None) -> PineconeDocumentAdapter:
        from pinecone import Pinecone, NotFoundError

        client = Pinecone(api_key=settings.pinecone_api_key.get_secret_value(),
                          ssl_ca_certs=str(settings.ca_file) if settings.ca_file else None, timeout=30.0)
        try:
            try:
                description = client.indexes.describe(settings.index_name)
            except NotFoundError:
                if create_dimension is None:
                    raise IndexCompatibilityError("Index not found; creation requires explicit --create-index.") from None
                if create_dimension <= 0:
                    raise IndexCompatibilityError("New index dimension must be positive.")
                if before_create is None:
                    raise IndexCompatibilityError("New index creation requires a successful embedding compatibility probe.")
                before_create(create_dimension)
                description = client.indexes.create(
                    name=settings.index_name,
                    schema={"fields": {
                        "embedding": {"type": "dense_vector", "dimension": create_dimension, "metric": "cosine"},
                        "body": {"type": "string", "full_text_search": {"language": "en", "stemming": False}},
                    }},
                    deployment={"deployment_type": "managed", "cloud": "aws", "region": "us-east-1"},
                    deletion_protection="enabled", timeout=45,
                )
            configuration = inspect_schema(description, settings.dense_field, settings.text_field)
            if not description.host:
                raise IndexCompatibilityError("Pinecone did not return an index host.")
            index = client.index(host=description.host)
            return cls(client, index, configuration, documents, settings.embedding_model, namespace_tag, source_metadata)
        except (IndexCompatibilityError, AdapterUnavailable):
            client.close()
            raise
        except Exception:
            client.close()
            raise AdapterUnavailable("Pinecone index configuration request failed; provider details withheld.") from None

    def _filter(self, equipment_id: str | None = None, tokens: tuple[str, ...] = ()) -> dict[str, Any]:
        conditions: list[dict[str, Any]] = [
            {"synthetic": {"$eq": True}}, {"corpus_id": {"$eq": self._digest}},
            {"embedding_model": {"$eq": self._model}},
            {"embedding_dimension": {"$eq": self.configuration.dimension}},
        ]
        if equipment_id:
            conditions.append({"equipment_ids": {"$in": [equipment_id]}})
        for token in tokens:
            field = "alarm_codes" if token.startswith("SYN-ALM-") else "part_numbers"
            conditions.append({field: {"$in": [token]}})
        return {"$and": conditions}

    def _validate_document(self, returned: Any) -> CorpusDocument:
        expected = self._corpus.get(returned.id)
        if expected is None:
            raise ValueError("Index returned an unknown citation ID")
        if returned.get("corpus_id") != self._digest or returned.get("embedding_model") != self._model:
            raise ValueError("Index returned stale corpus or model metadata")
        if returned.get("embedding_dimension") != self.configuration.dimension or returned.get("synthetic") is not True:
            raise ValueError("Index returned invalid embedding metadata")
        decoded = CorpusDocument.model_validate_json(returned.get("source_payload"))
        if decoded != expected:
            raise ValueError("Citation payload disagrees with canonical synthetic source")
        text_field = self.configuration.text_field or "source_text"
        if returned.get(text_field) != expected.text:
            raise ValueError("Indexed text disagrees with citation source")
        if expected.source_id in self._source_metadata:
            metadata = RAGSourceMetadata.model_validate_json(returned.get("rag_metadata"))
            if metadata != self._source_metadata[expected.source_id]:
                raise ValueError("Indexed ingestion metadata disagrees with verified source")
        return decoded

    def upsert(self, embedder: EmbeddingAdapter) -> int:
        if embedder.model != self._model or embedder.dimension != self.configuration.dimension:
            raise ValueError("Index and embedding configuration disagree")
        documents = tuple(self._corpus.values())
        total = 0
        try:
            for start in range(0, len(documents), 16):
                batch = documents[start:start + 16]
                embedded = embedder.embed(tuple(d.text for d in batch), "document")
                if embedded.model != self._model or embedded.dimension != self.configuration.dimension or len(embedded.vectors) != len(batch):
                    raise ValueError("Embedding batch does not match corpus batch")
                rows = []
                for document, vector in zip(batch, embedded.vectors):
                    rows.append({
                        "_id": document.source_id, self.configuration.dense_field: list(vector),
                        (self.configuration.text_field or "source_text"): document.text,
                        "source_payload": document.model_dump_json(), "corpus_id": self._digest,
                        "synthetic": True, "embedding_model": self._model,
                        "embedding_dimension": embedded.dimension,
                        "equipment_ids": list(document.equipment_ids), "alarm_codes": list(document.alarm_codes),
                        "part_numbers": list(document.part_numbers),
                    })
                    if document.source_id in self._source_metadata:
                        metadata = self._source_metadata[document.source_id]
                        rows[-1].update({"rag_metadata": metadata.model_dump_json(), "source_kind": metadata.source_kind,
                            "fix_ids": list(metadata.fix_ids), "failure_mode_ids": list(metadata.failure_mode_ids)})
                        if metadata.pdf_filename:
                            rows[-1].update({"pdf_filename": metadata.pdf_filename, "pdf_page": metadata.pdf_page,
                                "source_doc": metadata.source_doc, "source_version": metadata.source_version,
                                "logical_page": metadata.logical_page, "pdf_sha256": metadata.pdf_sha256})
                response = self._index.documents.upsert(namespace=self.namespace, documents=rows, timeout=30.0)
                if response.upserted_count != len(rows):
                    raise ValueError("Provider did not acknowledge the complete batch")
                total += response.upserted_count
            return total
        except Exception:
            raise AdapterUnavailable("Pinecone upsert/validation failed; partial writes may exist. Provider details withheld.") from None

    def verify_visible(self) -> None:
        try:
            response = self._index.documents.fetch(
                namespace=self.namespace, ids=sorted(self._corpus),
                include_fields=["source_payload", "corpus_id", "embedding_model", "embedding_dimension", "synthetic",
                                self.configuration.text_field or "source_text", *(["rag_metadata"] if self._source_metadata else [])], timeout=30.0,
            )
            if set(response.documents) != set(self._corpus):
                raise ValueError("Not all indexed documents are visible yet")
            for document in response.documents.values():
                self._validate_document(document)
            if self._corpus:
                first_id = min(self._corpus)
                vectors = self._index.documents.fetch(
                    namespace=self.namespace, ids=[first_id], include_fields=[self.configuration.dense_field], timeout=30.0,
                )
                vector = tuple(vectors.documents[first_id].get(self.configuration.dense_field))
                published = self.dense_search(vector, 100)
                if {h.document.source_id for h in published} != set(self._corpus):
                    raise ValueError("Dense index is not fully searchable yet")
                if self.configuration.text_field:
                    first = self._corpus[first_id]
                    token = next(iter((*first.alarm_codes, *first.part_numbers)), None)
                    if token is not None and first_id not in {h.document.source_id for h in self.lexical_search(token, 100)}:
                        raise ValueError("Full-text index is not searchable yet")
        except Exception:
            raise AdapterUnavailable("Indexed documents are not fully visible or valid; provider details withheld.") from None

    def _search(self, clause: Any, kind: str, top_k: int, equipment_id: str | None, tokens: tuple[str, ...] = ()) -> tuple[SearchHit, ...]:
        if not 1 <= top_k <= 100:
            raise ValueError("Search top_k must be in 1..100")
        try:
            response = self._index.documents.search(
                namespace=self.namespace, score_by=[clause], top_k=top_k,
                include_fields=["*"], filter=self._filter(equipment_id, tokens), timeout=30.0,
            )
            hits = []
            ids = set()
            for returned in response.matches:
                document = self._validate_document(returned)
                if document.source_id in ids:
                    raise ValueError("Duplicate citation ID in search response")
                if equipment_id and equipment_id not in document.equipment_ids:
                    raise ValueError("Search result equipment context mismatch")
                if any(token not in (*document.alarm_codes, *document.part_numbers) for token in tokens):
                    raise ValueError("Search exact-token metadata filter mismatch")
                score = returned.get("_score")
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("Provider search score is invalid")
                ids.add(document.source_id)
                hits.append(SearchHit(
                    document=document, score=float(score), score_kind=kind, provider="pinecone:live",
                    dense_score=float(score) if kind == "dense_similarity" else None,
                    lexical_score=float(score) if kind == "bm25" else None,
                    source_metadata=self._source_metadata.get(document.source_id),
                ))
            if len(hits) > top_k:
                raise ValueError("Provider exceeded requested top_k")
            return tuple(hits)
        except Exception:
            raise AdapterUnavailable("Pinecone search/citation validation failed; provider details withheld.") from None

    def dense_search(self, vector: tuple[float, ...], top_k: int, equipment_id: str | None = None) -> tuple[SearchHit, ...]:
        from pinecone import DenseVectorQuery

        if len(vector) != self.configuration.dimension or any(not math.isfinite(v) for v in vector):
            raise ValueError("Query vector is incompatible with index")
        return self._search(DenseVectorQuery(field=self.configuration.dense_field, values=list(vector)), "dense_similarity", top_k, equipment_id)

    def lexical_search(self, query: str, top_k: int, equipment_id: str | None = None) -> tuple[SearchHit, ...]:
        from pinecone import TextQuery

        if self.configuration.text_field is None:
            raise IndexCompatibilityError("Hybrid retrieval requires a declared full-text string field; existing index is dense-only.")
        return self._search(TextQuery(query=query, fields=[self.configuration.text_field]), "bm25", top_k, equipment_id, exact_tokens(query))

    def close(self) -> None:
        self._index.close()
        self._client.close()
