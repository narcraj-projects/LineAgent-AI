"""Read-only graph projection. Never seeds, mutates, or asks a model for facts."""

from src.adapters.contracts import GraphAdapter
from src.adapters.diagnosis_contracts import SQLEvidenceAdapter
from src.data_models import Dataset
from src.explorer_models import EdgeKind, ExplorerEdge, ExplorerGraph, ExplorerNode
from src.graph_models import FixHistory, GraphFixPath
from src.pdf_models import PDFCitation


def explore_graph(data: Dataset, graph: GraphAdapter, sql: SQLEvidenceAdapter, alarm_code: str,
                  citations: tuple[PDFCitation, ...] = ()) -> ExplorerGraph:
    alarm = next((a for a in data.alarms if a.code == alarm_code), None)
    if alarm is None:
        raise LookupError('Unknown synthetic alarm')
    equipment = next(e for e in data.equipment if e.equipment_id == alarm.equipment_id)
    failure = next(f for f in data.failure_modes if f.failure_mode_id == alarm.failure_mode_id)
    citation_map = {c.section_id: c for c in citations}
    nodes: dict[str, ExplorerNode] = {}
    edges: dict[str, ExplorerEdge] = {}

    def add(node: ExplorerNode) -> None:
        if node.id in nodes and nodes[node.id] != node:
            raise ValueError('Conflicting graph node')
        nodes[node.id] = node

    def edge(source: str, target: str, relation: EdgeKind) -> None:
        item = ExplorerEdge(id=f'{source}:{relation}:{target}', source=source, target=target, relationship=relation)
        edges[item.id] = item

    paths = graph.alarm_paths(alarm.code, equipment.equipment_id)
    for raw in paths:
        path = GraphFixPath.model_validate_json(raw.model_dump_json())
        fix = next((f for f in data.fixes if f.fix_id == path.fix_id), None)
        audited = FixHistory.model_validate_json(sql.fix_history(equipment.equipment_id, path.fix_id).model_dump_json())
        if (path.status != graph.status or path.alarm_id != alarm.alarm_id or path.alarm_code != alarm.code
                or path.equipment_id != equipment.equipment_id or path.failure_mode_id != failure.failure_mode_id
                or fix is None or fix.fix_id not in failure.fix_ids or path.part_numbers != fix.part_numbers
                or audited.equipment_id != equipment.equipment_id or audited.fix_id != path.fix_id
                or {w.work_order_id:w for w in path.history.work_orders} != {w.work_order_id:w for w in audited.work_orders}
                or any(m not in data.manual_sections for m in path.manuals)):
            raise ValueError('Graph path failed canonical or SQL audit')
        add(ExplorerNode(id=equipment.equipment_id, kind='equipment', title=equipment.equipment_type,
            description=f'Synthetic equipment on {equipment.line_id}.'))
        add(ExplorerNode(id=alarm.alarm_id, kind='alarm', title=alarm.code, description=alarm.description))
        add(ExplorerNode(id=failure.failure_mode_id, kind='failure', title=failure.category, description=failure.description))
        add(ExplorerNode(id=fix.fix_id, kind='fix', title=fix.title, description=fix.safety_note, outcomes=audited.outcomes))
        edge(alarm.alarm_id, equipment.equipment_id, 'RAISED_BY')
        edge(alarm.alarm_id, failure.failure_mode_id, 'INDICATES')
        edge(failure.failure_mode_id, fix.fix_id, 'RESOLVED_BY')
        for number in path.part_numbers:
            part = next(p for p in data.spare_parts if p.part_number == number)
            add(ExplorerNode(id=number, kind='part', title=number, description=part.description))
            edge(fix.fix_id, number, 'REQUIRES')
        for manual in path.manuals:
            add(ExplorerNode(id=manual.section_id, kind='manual', title=manual.heading,
                description=f'{manual.source_doc} v{manual.source_version}, logical page {manual.page}.',
                citation=citation_map.get(manual.section_id)))
            edge(manual.section_id, fix.fix_id, 'DESCRIBES')
            edge(manual.section_id, equipment.equipment_id, 'DESCRIBES')
        for work in audited.work_orders:
            add(ExplorerNode(id=work.work_order_id, kind='work_order', title=work.work_order_id,
                description=f'{work.date.isoformat()} | {work.alarm_code} | recorded synthetic observation.', outcome=work.outcome))
            edge(work.work_order_id, equipment.equipment_id, 'ON')
            edge(work.work_order_id, fix.fix_id, 'APPLIED')
    return ExplorerGraph(status=graph.status, alarm_code=alarm.code, equipment_id=equipment.equipment_id,
        store_counts=graph.counts(), path_count=len(paths), nodes=tuple(nodes[k] for k in sorted(nodes)),
        edges=tuple(edges[k] for k in sorted(edges)))
