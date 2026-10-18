"""Generate exactly two deterministic PDF compilations of all canonical manuals."""

import argparse
import hashlib
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from src.data_models import Dataset, ManualSection
from src.pdf_models import PDFCitation, PDFFile, PDFManifest
from src.retrieval.corpus import load_dataset

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "BEGIN SECTION TEXT"
END = "END SECTION TEXT"


def paragraph(canvas: Canvas, text: str, top: float, *, size: float = 11,
              color: str = "#22334a", bold: bool = False) -> float:
    style = ParagraphStyle("manual", fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size, leading=size * 1.55, textColor=colors.HexColor(color), spaceAfter=12)
    block = Paragraph(escape(text), style)
    width, height = block.wrap(A4[0] - 104, A4[1])
    if top - height < 95:
        raise ValueError("Synthetic section would overflow its canonical page")
    block.drawOn(canvas, 52, top - height)
    return top - height - 16


def draw_section(canvas: Canvas, section: ManualSection, volume: int, physical_page: int,
                 pages: int) -> None:
    width, height = A4
    canvas.setFillColor(colors.HexColor("#123447"))
    canvas.rect(0, height - 86, width, 86, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 19)
    canvas.drawString(52, height - 43, "LineMedic | Synthetic manuals")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(52, height - 65, f"Compilation volume {volume} / 2 | Deterministic training dataset")
    top = paragraph(canvas, "SYNTHETIC TRAINING ONLY - NOT AN OEM MANUAL", height - 119,
        size=10, color="#92400e", bold=True)
    top = paragraph(canvas, section.heading, top - 6, size=19, bold=True)
    top = paragraph(canvas, f"Section ID: {section.section_id}", top, size=11, bold=True)
    top = paragraph(canvas, f"Logical document: {section.source_doc} | Version: {section.source_version} | Logical page: {section.page}", top, size=10)
    top = paragraph(canvas, "Equipment: " + ", ".join(section.equipment_ids), top, size=10)
    top = paragraph(canvas, "Failure mode: " + section.failure_mode_id + " | Referenced fixes: " + ", ".join(section.fix_ids), top, size=10)
    top = paragraph(canvas, BEGIN, top - 5, size=8, color="#52657a")
    top = paragraph(canvas, section.text, top, size=12)
    paragraph(canvas, END, top, size=8, color="#52657a")
    canvas.setStrokeColor(colors.HexColor("#cbd5df"))
    canvas.line(52, 76, width - 52, 76)
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#52657a"))
    canvas.drawString(52, 58, "Fictional evidence. No equipment operation or restart authorization.")
    canvas.drawString(52, 42, f"Original reference: {section.source_doc} v{section.source_version} p.{section.page}")
    canvas.drawRightString(width - 52, 42, f"PDF page {physical_page} / {pages}")
    canvas.showPage()


def write_pdfs(output_dir: Path, dataset: Dataset) -> PDFManifest:
    if len(dataset.manual_sections) != 30 or len(dataset.documents) != 5:
        raise ValueError("Expected the canonical 30-section, five-document dataset")
    output_dir.mkdir(parents=True, exist_ok=True)
    citations: list[PDFCitation] = []
    files: list[PDFFile] = []
    doc_ids = sorted(d.doc_id for d in dataset.documents)
    for volume, ids in enumerate((doc_ids[:3], doc_ids[3:]), 1):
        sections = sorted((s for s in dataset.manual_sections if s.source_doc in ids),
            key=lambda s: (s.source_doc, s.page, s.section_id))
        filename = f"linemedic-synthetic-manual-{volume}.pdf"
        path = output_dir / filename
        canvas = Canvas(str(path), pagesize=A4, invariant=1, pageCompression=1)
        canvas.setTitle(f"LineMedic synthetic manual compilation {volume}")
        canvas.setAuthor("LineMedic synthetic data generator")
        canvas.setSubject("Fictional training evidence; no real equipment maintenance authority")
        for physical_page, section in enumerate(sections, 1):
            draw_section(canvas, section, volume, physical_page, len(sections))
            citations.append(PDFCitation(section_id=section.section_id, filename=filename, pdf_page=physical_page,
                source_doc=section.source_doc, source_version=section.source_version, logical_page=section.page))
        canvas.save()
        files.append(PDFFile(filename=filename, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), pages=len(sections)))
    manifest = PDFManifest(dataset_sha256=hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
        files=tuple(files), citations=tuple(sorted(citations, key=lambda c: c.section_id)))
    (output_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/pdf")
    args = parser.parse_args()
    manifest = write_pdfs(args.output_dir, load_dataset(ROOT / "data"))
    print(manifest.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
