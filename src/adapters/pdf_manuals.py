"""Strict text-PDF parser and section-aware chunker for trusted synthetic packets.

Not a general upload/OCR service. Treat extracted content as untrusted; compare
it with canonical synthetic metadata and text before embedding or generation.
"""

import hashlib
from pathlib import Path
import re

from pypdf import PdfReader

from src.data_models import Dataset, ManualSection
from src.pdf_models import PDFChunk, PDFManifest, ParsedPDFDataset
from src.safety import unsafe_text

BEGIN = "BEGIN SECTION TEXT"
END = "END SECTION TEXT"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class PDFParsingError(ValueError):
    """Controlled fail-closed parsing error with no untrusted source text."""


class SyntheticPDFAdapter:
    provider = "pypdf:local-synthetic-pdf"

    def parse(self, manifest_path: Path, baseline: Dataset) -> ParsedPDFDataset:
        try:
            manifest = PDFManifest.model_validate_json(manifest_path.read_bytes())
            if manifest.dataset_sha256 != hashlib.sha256(baseline.model_dump_json().encode()).hexdigest():
                raise PDFParsingError("PDF manifest does not match the frozen synthetic dataset")
            known = {s.section_id: s for s in baseline.manual_sections}
            if {c.section_id for c in manifest.citations} != known.keys():
                raise PDFParsingError("PDF source IDs do not cover the canonical manual sections")
            sections: dict[str, ManualSection] = {}
            chunks: list[PDFChunk] = []
            for item in manifest.files:
                path = manifest_path.parent / item.filename
                if path.stat().st_size > 10_000_000:
                    raise PDFParsingError("Synthetic PDF exceeds the local parsing limit")
                if hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256:
                    raise PDFParsingError("Synthetic PDF checksum mismatch")
                reader = PdfReader(path, strict=True)
                if reader.is_encrypted or len(reader.pages) != item.pages:
                    raise PDFParsingError("Encrypted PDF or unexpected page count")
                root = reader.trailer["/Root"]
                if any(key in root for key in ("/OpenAction", "/AA", "/AcroForm")) or "/Names" in root:
                    raise PDFParsingError("Active content and embedded assets are not supported")
                locations = sorted((c for c in manifest.citations if c.filename == item.filename), key=lambda c: c.pdf_page)
                for location in locations:
                    page = reader.pages[location.pdf_page - 1]
                    if page.get("/Annots"):
                        raise PDFParsingError("PDF page annotations are not supported")
                    text = normalize_text(page.extract_text() or "")
                    if unsafe_text(text):
                        raise PDFParsingError("Unsafe instructions in PDF content were rejected")
                    section = known[location.section_id]
                    if (location.source_doc, location.source_version, location.logical_page) != (
                            section.source_doc, section.source_version, section.page):
                        raise PDFParsingError("PDF logical provenance conflicts with canonical source")
                    markers = (f"Section ID: {section.section_id}",
                        f"Logical document: {section.source_doc} | Version: {section.source_version} | Logical page: {section.page}",
                        "SYNTHETIC TRAINING ONLY - NOT AN OEM MANUAL")
                    if not all(marker in text for marker in markers) or text.count(BEGIN) != 1 or text.count(END) != 1:
                        raise PDFParsingError("Missing or ambiguous synthetic section boundaries")
                    body = text.split(BEGIN, 1)[1].split(END, 1)[0].strip()
                    if not body or len(body) > 2000 or body != normalize_text(section.text):
                        raise PDFParsingError("Extracted PDF section differs from canonical synthetic text")
                    # This text is actually extracted from the PDF. Canonical
                    # JSON supplies only verified structural metadata and labels.
                    parsed = ManualSection.model_validate({**section.model_dump(), "text": body})
                    sections[section.section_id] = parsed
                    chunks.append(PDFChunk(section=parsed, citation=location,
                        text_sha256=hashlib.sha256(body.encode()).hexdigest(), characters=len(body)))
            dataset = Dataset.model_validate({**baseline.model_dump(),
                "manual_sections": tuple(sections[s.section_id] for s in baseline.manual_sections)})
            return ParsedPDFDataset(dataset=dataset, manifest=manifest,
                chunks=tuple(sorted(chunks, key=lambda c: c.section.section_id)))
        except PDFParsingError:
            raise
        except Exception:
            raise PDFParsingError("Synthetic PDF parsing or manifest validation failed") from None
