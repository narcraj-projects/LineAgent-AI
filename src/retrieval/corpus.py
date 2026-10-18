"""Indexable synthetic records only; never load or index data/eval answer keys."""

import hashlib
import json
from pathlib import Path
import re

from src.data_models import Dataset
from src.retrieval.models import CorpusDocument

EXACT_TOKEN = re.compile(r"\bSYN-(?:ALM|PART)-\d{3}\b", re.IGNORECASE)
GROUPS = ("lines", "equipment", "alarms", "failure_modes", "fixes", "spare_parts", "work_orders", "manual_sections")


def load_dataset(data_dir: Path) -> Dataset:
    metadata = json.loads((data_dir / "synthetic/metadata.json").read_bytes())
    metadata.pop("notice", None)
    for group in GROUPS:
        metadata[group] = json.loads((data_dir / f"synthetic/{group}.json").read_bytes())
    return Dataset.model_validate_json(json.dumps(metadata))


def build_corpus(data: Dataset) -> tuple[CorpusDocument, ...]:
    data = Dataset.model_validate_json(data.model_dump_json())
    fixes = {fix.fix_id: fix for fix in data.fixes}
    documents = []
    for section in data.manual_sections:
        alarms = tuple(sorted(a.code for a in data.alarms if a.failure_mode_id == section.failure_mode_id and a.equipment_id in section.equipment_ids))
        parts = tuple(sorted({part for fix_id in section.fix_ids for part in fixes[fix_id].part_numbers}))
        context = f"Synthetic indexed context: alarms {' '.join(alarms)}; parts {' '.join(parts)}; equipment {' '.join(section.equipment_ids)}."
        documents.append(CorpusDocument(
            source_id=section.section_id, source_kind="manual", text=f"{context}\n{section.text}",
            equipment_ids=section.equipment_ids, alarm_codes=alarms, part_numbers=parts,
            source_doc=section.source_doc, source_version=section.source_version, page=section.page,
        ))
    for order in data.work_orders:
        parts = fixes[order.fix_id].part_numbers
        context = f"Synthetic work order {order.work_order_id}: alarm {order.alarm_code}; fix {order.fix_id}; parts {' '.join(parts)}; equipment {order.equipment_id}; date {order.date.isoformat()}."
        documents.append(CorpusDocument(
            source_id=order.work_order_id, source_kind="work_order", text=f"{context}\n{order.note}",
            equipment_ids=(order.equipment_id,), alarm_codes=(order.alarm_code,), part_numbers=parts,
        ))
    if any(not d.source_id.startswith(("SYN-SEC-", "SYN-WO-")) for d in documents):
        raise ValueError("Only canonical synthetic source IDs may be indexed")
    if len({d.source_id for d in documents}) != len(documents):
        raise ValueError("Duplicate corpus source ID")
    return tuple(sorted(documents, key=lambda d: d.source_id))


def corpus_digest(documents: tuple[CorpusDocument, ...]) -> str:
    payload = [d.model_dump(mode="json") for d in sorted(documents, key=lambda d: d.source_id)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def exact_tokens(query: str) -> tuple[str, ...]:
    return tuple(sorted({match.upper() for match in EXACT_TOKEN.findall(query)}))
