"""Cross-mode metadata derived from parsed PDFs and canonical graph relationships."""

import hashlib

from src.pdf_models import PDFManifest, ParsedPDFDataset, RAGSourceMetadata
from src.retrieval.corpus import build_corpus


def pdf_namespace_tag(manifest: PDFManifest) -> str:
    digest = hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
    return "pdf-" + digest[:16]


def build_pdf_metadata(parsed: ParsedPDFDataset) -> dict[str, RAGSourceMetadata]:
    data = parsed.dataset
    locations = {c.section_id: c for c in parsed.manifest.citations}
    files = {f.filename: f for f in parsed.manifest.files}
    manuals = {m.section_id: m for m in data.manual_sections}
    orders = {w.work_order_id: w for w in data.work_orders}
    alarms = {a.code: a for a in data.alarms}
    result = {}
    for doc in build_corpus(data):
        common = dict(source_id=doc.source_id, chunk_id=doc.source_id, source_kind=doc.source_kind,
            dataset_seed=data.seed, text_sha256=hashlib.sha256(doc.text.encode()).hexdigest(),
            equipment_ids=doc.equipment_ids, alarm_codes=doc.alarm_codes, part_numbers=doc.part_numbers)
        if doc.source_kind == "manual":
            manual, location = manuals[doc.source_id], locations[doc.source_id]
            result[doc.source_id] = RAGSourceMetadata(**common, origin="pdf",
                failure_mode_ids=(manual.failure_mode_id,), fix_ids=manual.fix_ids,
                source_doc=manual.source_doc, source_version=manual.source_version, logical_page=manual.page,
                pdf_filename=location.filename, pdf_sha256=files[location.filename].sha256, pdf_page=location.pdf_page)
        else:
            order = orders[doc.source_id]
            result[doc.source_id] = RAGSourceMetadata(**common, origin="synthetic_work_order_json",
                failure_mode_ids=(alarms[order.alarm_code].failure_mode_id,), fix_ids=(order.fix_id,))
    return result
