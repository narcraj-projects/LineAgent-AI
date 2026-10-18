"""Synthetic factory contracts and referential integrity, independent of providers."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from src.schemas import Contract, NonEmpty


class SyntheticRecord(Contract):
    synthetic: Literal[True] = True


class Line(SyntheticRecord):
    line_id: NonEmpty
    product: NonEmpty


class Equipment(SyntheticRecord):
    equipment_id: NonEmpty
    equipment_type: NonEmpty
    line_id: NonEmpty


class Alarm(SyntheticRecord):
    alarm_id: NonEmpty
    code: NonEmpty
    description: NonEmpty
    equipment_id: NonEmpty
    failure_mode_id: NonEmpty


class FailureMode(SyntheticRecord):
    failure_mode_id: NonEmpty
    category: NonEmpty
    description: NonEmpty
    fix_ids: tuple[NonEmpty, ...] = Field(min_length=2)


class Fix(SyntheticRecord):
    fix_id: NonEmpty
    title: NonEmpty
    steps: tuple[NonEmpty, ...] = Field(min_length=1)
    safety_note: NonEmpty
    part_numbers: tuple[NonEmpty, ...] = Field(min_length=1)


class SparePart(SyntheticRecord):
    part_number: NonEmpty
    description: NonEmpty
    stock: int = Field(ge=0)


class WorkOrder(SyntheticRecord):
    work_order_id: NonEmpty
    equipment_id: NonEmpty
    alarm_code: NonEmpty
    fix_id: NonEmpty
    outcome: Literal["worked", "did_not_work"]
    note: NonEmpty
    date: date
    actor_id: NonEmpty


class ManualDocument(SyntheticRecord):
    doc_id: NonEmpty
    title: NonEmpty
    version: NonEmpty
    page_count: int = Field(ge=1)


class ManualSection(SyntheticRecord):
    section_id: NonEmpty
    source_doc: NonEmpty
    source_version: NonEmpty
    page: int = Field(ge=1)
    heading: NonEmpty
    text: NonEmpty
    equipment_ids: tuple[NonEmpty, ...] = Field(min_length=1)
    failure_mode_id: NonEmpty
    fix_ids: tuple[NonEmpty, ...] = Field(min_length=1)


class DemoIdentity(SyntheticRecord):
    actor_id: NonEmpty
    role: Literal["operator", "technician", "supervisor"]
    display_name: NonEmpty


class GraphRelationship(Contract):
    source_id: NonEmpty
    relationship: Literal[
        "HAS", "RAISED_BY", "INDICATES", "RESOLVED_BY", "REQUIRES", "ON", "APPLIED", "DESCRIBES"
    ]
    target_id: NonEmpty


class Dataset(SyntheticRecord):
    schema_version: Literal["1.0"] = "1.0"
    seed: int
    lines: tuple[Line, ...]
    equipment: tuple[Equipment, ...]
    alarms: tuple[Alarm, ...]
    failure_modes: tuple[FailureMode, ...]
    fixes: tuple[Fix, ...]
    spare_parts: tuple[SparePart, ...]
    work_orders: tuple[WorkOrder, ...]
    manual_sections: tuple[ManualSection, ...]
    documents: tuple[ManualDocument, ...]
    demo_identities: tuple[DemoIdentity, ...]

    @model_validator(mode="after")
    def validate_references(self) -> "Dataset":
        groups = (
            (self.lines, "line_id"), (self.equipment, "equipment_id"),
            (self.alarms, "alarm_id"), (self.failure_modes, "failure_mode_id"),
            (self.fixes, "fix_id"), (self.spare_parts, "part_number"),
            (self.work_orders, "work_order_id"), (self.manual_sections, "section_id"),
            (self.documents, "doc_id"), (self.demo_identities, "actor_id"),
        )
        all_ids = [getattr(record, key) for records, key in groups for record in records]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Duplicate canonical ID")
        codes = [alarm.code for alarm in self.alarms]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate alarm code")
        lines = {r.line_id for r in self.lines}
        equipment = {r.equipment_id for r in self.equipment}
        modes = {r.failure_mode_id: r for r in self.failure_modes}
        fixes = {r.fix_id: r for r in self.fixes}
        parts = {r.part_number for r in self.spare_parts}
        alarms = {r.code: r for r in self.alarms}
        actors = {r.actor_id: r for r in self.demo_identities}
        docs = {r.doc_id: r for r in self.documents}
        for eq in self.equipment:
            require(eq.line_id in lines, "Equipment references missing line")
        for alarm in self.alarms:
            require(alarm.equipment_id in equipment, "Alarm references missing equipment")
            require(alarm.failure_mode_id in modes, "Alarm references missing failure mode")
        for mode in self.failure_modes:
            require(len(set(mode.fix_ids)) == len(mode.fix_ids), "Duplicate candidate fix")
            require(set(mode.fix_ids) <= fixes.keys(), "Failure mode references missing fix")
        for fix in self.fixes:
            require(set(fix.part_numbers) <= parts, "Fix references missing part")
        for order in self.work_orders:
            alarm = alarms.get(order.alarm_code)
            require(alarm is not None, "Work order references missing alarm")
            if alarm is not None:
                require(order.equipment_id == alarm.equipment_id, "Work order equipment mismatch")
                require(order.fix_id in modes[alarm.failure_mode_id].fix_ids, "Work order fix mismatch")
            require(order.actor_id in actors, "Work order references missing actor")
            require(actors[order.actor_id].role == "technician", "Work order actor must be technician")
        for section in self.manual_sections:
            doc = docs.get(section.source_doc)
            require(doc is not None, "Manual references missing document")
            if doc is not None:
                require(section.source_version == doc.version, "Manual version mismatch")
                require(section.page <= doc.page_count, "Manual page outside source document")
            require(set(section.equipment_ids) <= equipment, "Manual references missing equipment")
            require(section.failure_mode_id in modes, "Manual references missing failure mode")
            require(set(section.fix_ids) <= set(modes[section.failure_mode_id].fix_ids), "Manual fix mismatch")
            context = {a.equipment_id for a in self.alarms if a.failure_mode_id == section.failure_mode_id}
            require(set(section.equipment_ids) <= context, "Manual equipment context mismatch")
        return self


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def graph_relationships(data: Dataset) -> tuple[GraphRelationship, ...]:
    """Export the PRD's edges, without implementing a graph retrieval adapter."""
    edges: set[tuple[str, str, str]] = set()
    for eq in data.equipment:
        edges.add((eq.line_id, "HAS", eq.equipment_id))
    for alarm in data.alarms:
        edges.add((alarm.alarm_id, "RAISED_BY", alarm.equipment_id))
        edges.add((alarm.alarm_id, "INDICATES", alarm.failure_mode_id))
    for mode in data.failure_modes:
        edges.update((mode.failure_mode_id, "RESOLVED_BY", fix) for fix in mode.fix_ids)
    for fix in data.fixes:
        edges.update((fix.fix_id, "REQUIRES", part) for part in fix.part_numbers)
    for order in data.work_orders:
        edges.add((order.work_order_id, "ON", order.equipment_id))
        edges.add((order.work_order_id, "APPLIED", order.fix_id))
    for section in data.manual_sections:
        edges.update((section.section_id, "DESCRIBES", target) for target in (*section.equipment_ids, *section.fix_ids))
    return tuple(GraphRelationship(source_id=s, relationship=r, target_id=t) for s, r, t in sorted(edges))


class AlarmTruth(SyntheticRecord):
    alarm_code: NonEmpty
    equipment_id: NonEmpty
    failure_mode_id: NonEmpty
    best_fix_id: NonEmpty
    decoy_fix_ids: tuple[NonEmpty, ...] = Field(min_length=1)
    expected_part_numbers: tuple[NonEmpty, ...] = Field(min_length=1)
    source_ids: tuple[NonEmpty, ...] = Field(min_length=1)


class EvaluationQuestion(SyntheticRecord):
    question_id: NonEmpty
    category: Literal["single_hop", "multi_hop", "tamil", "refusal", "exact_part"]
    language: Literal["en", "ta"] = "en"
    question: NonEmpty
    expected_status: Literal["supported", "refused"]
    alarm_code: NonEmpty | None = None
    expected_fix_id: NonEmpty | None = None
    expected_part_numbers: tuple[NonEmpty, ...] = ()
    expected_source_ids: tuple[NonEmpty, ...] = ()
    refusal_reason: NonEmpty | None = None
    variant_of: NonEmpty | None = None


class Benchmark(SyntheticRecord):
    schema_version: Literal["1.0"] = "1.0"
    seed: int
    alarm_truth: tuple[AlarmTruth, ...]
    questions: tuple[EvaluationQuestion, ...]


def validate_benchmark(data: Dataset, benchmark: Benchmark) -> None:
    """Check labels and provenance against canonical records, not model outputs."""
    require(data.seed == benchmark.seed, "Benchmark seed mismatch")
    alarms = {a.code: a for a in data.alarms}
    fixes = {f.fix_id: f for f in data.fixes}
    modes = {m.failure_mode_id: m for m in data.failure_modes}
    sections = {s.section_id: s for s in data.manual_sections}
    orders = {w.work_order_id: w for w in data.work_orders}
    truth = {t.alarm_code: t for t in benchmark.alarm_truth}
    require(len(truth) == len(benchmark.alarm_truth), "Duplicate alarm truth")
    require(truth.keys() == alarms.keys(), "Truth must cover every alarm exactly once")
    for label in benchmark.alarm_truth:
        alarm = alarms[label.alarm_code]
        require(label.equipment_id == alarm.equipment_id, "Truth equipment mismatch")
        require(label.failure_mode_id == alarm.failure_mode_id, "Truth failure mode mismatch")
        require(label.best_fix_id in fixes, "Truth references missing best fix")
        candidates = set(modes[alarm.failure_mode_id].fix_ids)
        require({label.best_fix_id, *label.decoy_fix_ids} == candidates, "Truth candidate mismatch")
        require(label.best_fix_id not in label.decoy_fix_ids, "Best fix is also a decoy")
        require(tuple(fixes[label.best_fix_id].part_numbers) == label.expected_part_numbers, "Truth part mismatch")
        for source_id in label.source_ids:
            if source_id in sections:
                section = sections[source_id]
                require(alarm.equipment_id in section.equipment_ids, "Truth source equipment mismatch")
                require(label.best_fix_id in section.fix_ids, "Truth source fix mismatch")
            elif source_id in orders:
                order = orders[source_id]
                require(order.equipment_id == alarm.equipment_id and order.fix_id == label.best_fix_id, "Truth work order mismatch")
            else:
                raise ValueError("Truth references missing source")
    questions = {q.question_id: q for q in benchmark.questions}
    require(len(questions) == len(benchmark.questions), "Duplicate evaluation question")
    for question in benchmark.questions:
        if question.expected_status == "refused":
            require(question.category == "refusal" and bool(question.refusal_reason), "Refusal reason missing")
            require(question.expected_fix_id is None and not question.expected_source_ids and not question.expected_part_numbers, "Refusal contains advice")
            continue
        require(question.alarm_code in truth, "Question references missing alarm truth")
        label = truth[question.alarm_code]
        require(question.expected_fix_id == label.best_fix_id, "Question best fix mismatch")
        require(question.expected_part_numbers == label.expected_part_numbers, "Question parts mismatch")
        require(bool(question.expected_source_ids) and set(question.expected_source_ids) <= set(label.source_ids), "Question source mismatch")
        if question.category == "tamil":
            require(question.language == "ta" and question.variant_of in questions, "Tamil variant missing base question")
            base = questions[question.variant_of]
            require(base.category == "single_hop" and base.alarm_code == question.alarm_code, "Tamil variant context mismatch")
