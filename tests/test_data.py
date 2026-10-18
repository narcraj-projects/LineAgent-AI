"""Synthetic data integrity, meaningful scenarios and independent benchmark labels."""

from collections import Counter
import json

import networkx as nx
import pytest
from pydantic import ValidationError

from scripts.generate_data import GROUPS, generate
from src.data_models import Benchmark, Dataset, graph_relationships, validate_benchmark

EXPECTED_COUNTS = dict(zip(GROUPS, (3, 10, 20, 15, 25, 20, 50, 30)))


@pytest.fixture(scope="module")
def factory() -> tuple[Dataset, Benchmark]:
    return generate()


def test_exact_counts_and_unique_canonical_ids(factory: tuple[Dataset, Benchmark]) -> None:
    data, benchmark = factory
    keys = ("line_id", "equipment_id", "alarm_id", "failure_mode_id", "fix_id", "part_number", "work_order_id", "section_id")
    prefixes = ("LINE", "EQ", "ALARM", "FM", "FIX", "PART", "WO", "SEC")
    for group, key, prefix in zip(GROUPS, keys, prefixes):
        records = getattr(data, group)
        assert len(records) == EXPECTED_COUNTS[group]
        assert [getattr(record, key) for record in records] == [f"SYN-{prefix}-{i:03d}" for i in range(1, len(records) + 1)]
        assert all(record.synthetic for record in records)
    assert len(data.documents) == 5
    assert {actor.role for actor in data.demo_identities} == {"operator", "technician", "supervisor"}
    assert len(benchmark.alarm_truth) == 20
    assert len({a.code for a in data.alarms}) == 20


def test_every_graph_edge_has_valid_typed_endpoints_and_paths(factory: tuple[Dataset, Benchmark]) -> None:
    data, benchmark = factory
    graph = nx.DiGraph()
    for group, key, kind in (
        (data.lines, "line_id", "Line"), (data.equipment, "equipment_id", "Equipment"),
        (data.alarms, "alarm_id", "Alarm"), (data.failure_modes, "failure_mode_id", "FailureMode"),
        (data.fixes, "fix_id", "Fix"), (data.spare_parts, "part_number", "SparePart"),
        (data.work_orders, "work_order_id", "WorkOrder"), (data.manual_sections, "section_id", "ManualSection"),
    ):
        graph.add_nodes_from((getattr(record, key), {"kind": kind}) for record in group)
    signatures = {
        "HAS": {("Line", "Equipment")}, "RAISED_BY": {("Alarm", "Equipment")},
        "INDICATES": {("Alarm", "FailureMode")}, "RESOLVED_BY": {("FailureMode", "Fix")},
        "REQUIRES": {("Fix", "SparePart")}, "ON": {("WorkOrder", "Equipment")},
        "APPLIED": {("WorkOrder", "Fix")}, "DESCRIBES": {("ManualSection", "Equipment"), ("ManualSection", "Fix")},
    }
    edges = graph_relationships(data)
    assert len(edges) == len(set(edges)) == 290
    for edge in edges:
        assert edge.source_id in graph and edge.target_id in graph
        assert (graph.nodes[edge.source_id]["kind"], graph.nodes[edge.target_id]["kind"]) in signatures[edge.relationship]
        graph.add_edge(edge.source_id, edge.target_id, relationship=edge.relationship)
    assert graph.number_of_nodes() == 173
    alarms = {alarm.code: alarm for alarm in data.alarms}
    for truth in benchmark.alarm_truth:
        alarm = alarms[truth.alarm_code]
        assert graph.has_edge(alarm.alarm_id, truth.equipment_id)
        for part in truth.expected_part_numbers:
            assert nx.shortest_path(graph, alarm.alarm_id, part) == [alarm.alarm_id, truth.failure_mode_id, truth.best_fix_id, part]
        for decoy in truth.decoy_fix_ids:
            assert graph.has_edge(truth.failure_mode_id, decoy)


def test_decoys_have_distinct_fixes_and_meaningful_outcomes(factory: tuple[Dataset, Benchmark]) -> None:
    data, benchmark = factory
    assert {order.outcome for order in data.work_orders} == {"worked", "did_not_work"}
    for truth in benchmark.alarm_truth:
        assert truth.best_fix_id not in truth.decoy_fix_ids
        history = [w for w in data.work_orders if w.alarm_code == truth.alarm_code]
        best = [w for w in history if w.fix_id == truth.best_fix_id]
        assert len(best) == 2
        assert any(w.outcome == "worked" for w in best)
        assert all(w.fix_id in {truth.best_fix_id, *truth.decoy_fix_ids} for w in history)
        assert all(w.outcome == "did_not_work" for w in history if w.fix_id in truth.decoy_fix_ids)
    assert len({w.work_order_id for w in data.work_orders}) == 50
    assert "best_fix_id" not in json.dumps([a.model_dump() for a in data.alarms])


def test_manual_provenance_has_real_generated_sections_not_external_claims(factory: tuple[Dataset, Benchmark]) -> None:
    data, benchmark = factory
    for doc in data.documents:
        sections = [s for s in data.manual_sections if s.source_doc == doc.doc_id]
        assert doc.synthetic and "Fictional" in doc.title
        assert len(sections) == 6
        assert {s.page for s in sections} == set(range(1, 7))
        assert all(s.source_version == doc.version for s in sections)
        assert all("Synthetic simulator only" in s.text and len(s.text) > 100 for s in sections)
    validate_benchmark(data, benchmark)
    source_ids = {s.section_id for s in data.manual_sections} | {w.work_order_id for w in data.work_orders}
    assert all(set(t.source_ids) <= source_ids for t in benchmark.alarm_truth)
    assert all(len(t.source_ids) >= 4 for t in benchmark.alarm_truth)


def test_evaluation_mix_and_tamil_equivalence(factory: tuple[Dataset, Benchmark]) -> None:
    _, benchmark = factory
    assert Counter(q.category for q in benchmark.questions) == {
        "single_hop": 10, "multi_hop": 10, "tamil": 5, "refusal": 3, "exact_part": 2,
    }
    assert [q.question_id for q in benchmark.questions] == [f"SYN-Q-{i:03d}" for i in range(1, 31)]
    questions = {q.question_id: q for q in benchmark.questions}
    for q in benchmark.questions:
        if q.category == "tamil":
            base = questions[q.variant_of]
            assert q.language == "ta" and any("\u0b80" <= char <= "\u0bff" for char in q.question)
            assert q.expected_fix_id == base.expected_fix_id
            assert q.expected_source_ids == base.expected_source_ids
        elif q.category == "refusal":
            assert q.expected_status == "refused" and q.refusal_reason
            assert not q.expected_source_ids and q.expected_fix_id is None
        elif q.category == "exact_part":
            assert all(part in q.question for part in q.expected_part_numbers)


@pytest.mark.parametrize("mutation,message", [
    ("duplicate_id", "Duplicate canonical"), ("duplicate_code", "Duplicate alarm"),
    ("equipment_line", "missing line"), ("alarm_equipment", "missing equipment"),
    ("alarm_mode", "missing failure mode"), ("mode_fix", "missing fix"),
    ("fix_part", "missing part"), ("order_alarm", "missing alarm"),
    ("order_equipment", "equipment mismatch"), ("order_fix", "fix mismatch"),
    ("order_actor", "missing actor"), ("manual_doc", "missing document"),
    ("manual_version", "version mismatch"), ("manual_page", "outside source"),
    ("manual_equipment", "context mismatch"),
])
def test_referential_integrity_rejects_corruption(factory: tuple[Dataset, Benchmark], mutation: str, message: str) -> None:
    payload = factory[0].model_dump()
    mutations = {
        "duplicate_id": ("lines", 1, "line_id", payload["lines"][0]["line_id"]),
        "duplicate_code": ("alarms", 1, "code", payload["alarms"][0]["code"]),
        "equipment_line": ("equipment", 0, "line_id", "SYN-LINE-999"),
        "alarm_equipment": ("alarms", 0, "equipment_id", "SYN-EQ-999"),
        "alarm_mode": ("alarms", 0, "failure_mode_id", "SYN-FM-999"),
        "mode_fix": ("failure_modes", 0, "fix_ids", ("SYN-FIX-999", "SYN-FIX-016")),
        "fix_part": ("fixes", 0, "part_numbers", ("SYN-PART-999",)),
        "order_alarm": ("work_orders", 0, "alarm_code", "SYN-ALM-999"),
        "order_equipment": ("work_orders", 0, "equipment_id", "SYN-EQ-002"),
        "order_fix": ("work_orders", 0, "fix_id", "SYN-FIX-999"),
        "order_actor": ("work_orders", 0, "actor_id", "SYN-ACTOR-999"),
        "manual_doc": ("manual_sections", 0, "source_doc", "SYN-DOC-999"),
        "manual_version": ("manual_sections", 0, "source_version", "missing-version"),
        "manual_page": ("manual_sections", 0, "page", 7),
        "manual_equipment": ("manual_sections", 0, "equipment_ids", ("SYN-EQ-002",)),
    }
    group, index, key, value = mutations[mutation]
    payload[group][index][key] = value
    with pytest.raises(ValidationError, match=message):
        Dataset.model_validate(payload)


@pytest.mark.parametrize("mutation,message", [
    ("fix", "best fix"), ("source", "missing source"),
    ("equipment_source", "equipment mismatch"), ("part", "part mismatch"),
    ("question", "Question best fix"), ("duplicate_question", "Duplicate evaluation"),
])
def test_benchmark_rejects_corrupt_ground_truth(factory: tuple[Dataset, Benchmark], mutation: str, message: str) -> None:
    data, benchmark = factory
    payload = benchmark.model_dump()
    if mutation == "fix":
        payload["alarm_truth"][0]["best_fix_id"] = "SYN-FIX-999"
    elif mutation == "source":
        payload["alarm_truth"][0]["source_ids"] = ("SYN-SEC-999",)
    elif mutation == "equipment_source":
        payload["alarm_truth"][0]["source_ids"] = ("SYN-SEC-005",)
    elif mutation == "part":
        payload["alarm_truth"][0]["expected_part_numbers"] = ("SYN-PART-999",)
    elif mutation == "question":
        payload["questions"][0]["expected_fix_id"] = "SYN-FIX-999"
    else:
        payload["questions"][1]["question_id"] = payload["questions"][0]["question_id"]
    with pytest.raises(ValueError, match=message):
        validate_benchmark(data, Benchmark.model_validate(payload))
