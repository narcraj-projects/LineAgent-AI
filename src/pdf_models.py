"""Synthetic PDF provenance keeps physical and original logical pages distinct."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.data_models import Dataset, ManualSection
from src.schemas import Contract, NonEmpty

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PDFFile(Contract):
    filename: Annotated[str, Field(pattern=r"^linemedic-synthetic-manual-[12]\.pdf$")]
    sha256: Digest
    pages: int = Field(ge=1, le=100)


class PDFCitation(Contract):
    section_id: NonEmpty
    filename: Annotated[str, Field(pattern=r"^linemedic-synthetic-manual-[12]\.pdf$")]
    pdf_page: int = Field(ge=1)
    source_doc: NonEmpty
    source_version: NonEmpty
    logical_page: int = Field(ge=1)


class PDFManifest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    synthetic: Literal[True] = True
    dataset_sha256: Digest
    files: Annotated[tuple[PDFFile, ...], Field(min_length=2, max_length=2)]
    citations: tuple[PDFCitation, ...] = Field(min_length=30, max_length=30)

    @model_validator(mode="after")
    def validate_locations(self) -> "PDFManifest":
        files = {f.filename: f for f in self.files}
        if len(files) != 2 or len({c.section_id for c in self.citations}) != 30:
            raise ValueError("Two unique PDFs and 30 unique sections required")
        occupied = {(c.filename, c.pdf_page) for c in self.citations}
        expected = {(f.filename, page) for f in self.files for page in range(1, f.pages + 1)}
        if len(occupied) != 30 or occupied != expected:
            raise ValueError("Every physical PDF page must map to exactly one section")
        return self


class PDFChunk(Contract):
    section: ManualSection
    citation: PDFCitation
    text_sha256: Digest
    characters: int = Field(ge=1, le=2000)


class ParsedPDFDataset(Contract):
    dataset: Dataset
    manifest: PDFManifest
    chunks: tuple[PDFChunk, ...] = Field(min_length=30, max_length=30)


class RAGSourceMetadata(Contract):
    """One source identity across dense, lexical and graph retrieval."""

    synthetic: Literal[True] = True
    source_id: NonEmpty
    chunk_id: NonEmpty
    source_kind: Literal["manual", "work_order"]
    origin: Literal["pdf", "synthetic_work_order_json"]
    dataset_seed: int
    text_sha256: Digest
    equipment_ids: tuple[NonEmpty, ...] = Field(min_length=1)
    alarm_codes: tuple[NonEmpty, ...]
    failure_mode_ids: tuple[NonEmpty, ...]
    fix_ids: tuple[NonEmpty, ...]
    part_numbers: tuple[NonEmpty, ...]
    source_doc: NonEmpty | None = None
    source_version: NonEmpty | None = None
    logical_page: int | None = Field(default=None, ge=1)
    pdf_filename: NonEmpty | None = None
    pdf_sha256: Digest | None = None
    pdf_page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_origin(self) -> "RAGSourceMetadata":
        if self.chunk_id != self.source_id:
            raise ValueError("Chunk IDs must preserve canonical citation IDs")
        pdf_fields = (self.source_doc, self.source_version, self.logical_page,
                      self.pdf_filename, self.pdf_sha256, self.pdf_page)
        if self.origin == "pdf":
            if self.source_kind != "manual" or any(v is None for v in pdf_fields):
                raise ValueError("PDF manual provenance must be complete")
        elif self.source_kind != "work_order" or any(v is not None for v in pdf_fields):
            raise ValueError("JSON work orders cannot claim PDF provenance")
        return self
