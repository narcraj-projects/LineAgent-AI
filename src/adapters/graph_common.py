"""Shared synthetic graph payloads and grounded evidence conversion."""

from collections.abc import Iterator

from src.data_models import Dataset, SyntheticRecord
from src.graph_models import GraphFixPath
from src.schemas import Evidence

NAMESPACE = "linemedic-synthetic-v1"
NODE_SPECS = (
    ("lines", "Line", "line_id"), ("equipment", "Equipment", "equipment_id"),
    ("alarms", "Alarm", "alarm_id"), ("failure_modes", "FailureMode", "failure_mode_id"),
    ("fixes", "Fix", "fix_id"), ("spare_parts", "SparePart", "part_number"),
    ("work_orders", "WorkOrder", "work_order_id"), ("manual_sections", "ManualSection", "section_id"),
)


def node_records(data: Dataset) -> Iterator[tuple[str, str, str, SyntheticRecord]]:
    for group, label, key in NODE_SPECS:
        for record in getattr(data, group):
            if not getattr(record, key).startswith("SYN-"):
                raise ValueError("Graph seeding requires synthetic canonical IDs")
            yield label, key, getattr(record, key), record


def path_evidence(paths: tuple[GraphFixPath, ...]) -> tuple[Evidence, ...]:
    sources: dict[str, Evidence] = {}
    for item in paths:
        provider = f"{item.status.provider}:{item.status.mode}"
        for manual in item.manuals:
            sources.setdefault(manual.section_id, Evidence(
                source_id=manual.section_id, text=manual.text, source_kind="manual",
                provider=provider, path=item.paths[0],
            ))
        for order in item.history.work_orders:
            sources.setdefault(order.work_order_id, Evidence(
                source_id=order.work_order_id, text=order.note, source_kind="work_order",
                provider=provider, path=(order.work_order_id, item.fix_id),
            ))
    return tuple(sources[key] for key in sorted(sources))
