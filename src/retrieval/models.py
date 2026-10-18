"""Typed corpus, embedding, index compatibility and scored retrieval contracts."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.schemas import Contract, NonEmpty
from src.pdf_models import RAGSourceMetadata

Finite = Annotated[float, Field(allow_inf_nan=False)]


class CorpusDocument(Contract):
    source_id: NonEmpty
    source_kind: Literal["manual", "work_order"]
    text: NonEmpty
    equipment_ids: tuple[NonEmpty, ...] = Field(min_length=1)
    alarm_codes: tuple[NonEmpty, ...]
    part_numbers: tuple[NonEmpty, ...]
    source_doc: NonEmpty | None = None
    source_version: NonEmpty | None = None
    page: int | None = Field(default=None, ge=1)
    synthetic: Literal[True] = True


class EmbeddingBatch(Contract):
    model: NonEmpty
    dimension: int = Field(ge=1)
    vectors: tuple[tuple[Finite, ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "EmbeddingBatch":
        if any(len(vector) != self.dimension for vector in self.vectors):
            raise ValueError("Embedding dimensions disagree")
        return self


class IndexConfiguration(Contract):
    index_name: NonEmpty
    dimension: int = Field(ge=1)
    metric: Literal["cosine", "dotproduct", "euclidean"]
    dense_field: NonEmpty
    text_field: NonEmpty | None = None
    ready: bool
    schema_fields: tuple[NonEmpty, ...]
    api_surface: Literal["pinecone-v10-documents"] = "pinecone-v10-documents"


class SearchHit(Contract):
    document: CorpusDocument
    score: Finite
    score_kind: Literal["dense_similarity", "bm25", "weighted_rrf"]
    provider: NonEmpty
    dense_score: Finite | None = None
    lexical_score: Finite | None = None
    source_metadata: RAGSourceMetadata | None = None
