"""Independent scoring: answer keys enter here only after execution."""

import math
from typing import Any

from evals.models import Correctness, EvaluationRow, Mode
from src.agent_models import AgentRun, Route, RouteDecision
from src.data_models import Dataset, EvaluationQuestion
from src.retrieval.corpus import build_corpus


def expected_route(question: EvaluationQuestion) -> Route:
    # Reviewed labels for the frozen prompts, not derived from router output.
    if question.category in ("single_hop", "tamil"):
        return "sql"
    if question.category == "multi_hop":
        return "graph"
    if question.category == "exact_part" or question.question_id == "SYN-Q-026":
        return "combined"
    return "refuse"


def score(question: EvaluationQuestion, run: AgentRun, natural: RouteDecision,
          data: Dataset, retrieved_ids: tuple[str, ...] | None) -> Correctness:
    response = run.response
    status_match = response.status == question.expected_status
    routing_correct = natural.route == expected_route(question)
    failures: list[str] = []
    if not status_match:
        failures.append("status_mismatch")
    if question.expected_status == "refused":
        # Dependency-error refusals are not credited as correct safety behavior.
        reason_markers = {"SYN-Q-026": "Unknown synthetic alarm", "SYN-Q-027": "Unsafe",
                          "SYN-Q-028": "Outside synthetic equipment"}
        refused = (status_match and not response.candidates and not response.evidence
            and reason_markers[question.question_id] in response.message)
        if not refused:
            failures.append("incorrect_refusal_reason_or_payload")
        return Correctness(status_match=status_match, refusal_correct=refused,
            routing_correct=routing_correct, expected_router_route=expected_route(question), failures=tuple(failures))
    first = response.candidates[0] if response.candidates else None
    fix = bool(first and first.fix_id == question.expected_fix_id)
    parts = bool(first and (first.part_number,) == question.expected_part_numbers)
    canonical = {d.source_id: d for d in build_corpus(data)}
    raw = {m.section_id: m.text for m in data.manual_sections}
    raw.update({w.work_order_id: w.note for w in data.work_orders})
    evidence = {e.source_id: e for e in response.evidence}
    positive = {m.section_id for m in data.manual_sections if m.fix_ids == (question.expected_fix_id,)}
    citations = bool(first and set(first.evidence_source_ids) <= evidence.keys()
        and positive.intersection(first.evidence_source_ids)
        and set(question.expected_source_ids).intersection(first.evidence_source_ids)
        and all(e.source_id in canonical and e.text in (canonical[e.source_id].text, raw[e.source_id])
                for e in response.evidence))
    orders = tuple(w for w in data.work_orders if first and w.fix_id == first.fix_id
        and (not natural.equipment_id or w.equipment_id == natural.equipment_id))
    # Alarm-only requests obtain context from the dataset, not the answer key.
    equipment_id = natural.equipment_id or next((a.equipment_id for a in data.alarms
        if a.code == natural.alarm_code), None)
    orders = tuple(w for w in orders if w.equipment_id == equipment_id)
    worked = sum(w.outcome == "worked" for w in orders)
    rates = bool(first and first.outcomes.worked == worked
        and first.outcomes.did_not_work == len(orders) - worked)
    for name, correct in (("top1_fix", fix), ("exact_parts", parts), ("citations", citations), ("rates", rates)):
        if not correct:
            failures.append(name)
    recall = None if retrieved_ids is None else len(set(retrieved_ids[:5])
        & set(question.expected_source_ids)) / len(question.expected_source_ids)
    return Correctness(status_match=status_match, top1_fix=fix, exact_parts=parts,
        citations_valid=citations, rates_valid=rates, routing_correct=routing_correct,
        expected_router_route=expected_route(question), retrieval_recall_at_5=recall,
        has_graph_path=bool(first and first.graph_path), failures=tuple(failures))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def aggregate(rows: list[EvaluationRow], mode: Mode) -> dict[str, Any]:
    all_rows = [r for r in rows if r.mode == mode]
    executed = [r for r in all_rows if r.status != "NOT_RUN"]
    supported = [r for r in executed if r.category != "refusal"]
    refused = [r for r in executed if r.category == "refusal"]
    answered = [r for r in supported if r.run and r.run.response.status == "supported"]
    recall = [r.correctness.retrieval_recall_at_5 for r in supported
              if r.correctness and r.correctness.retrieval_recall_at_5 is not None]
    latencies = [r.latency_ms for r in executed if r.latency_ms is not None]
    english = [r.latency_ms for r in executed if r.language == "en" and r.latency_ms is not None]
    def metric(name: str, items: list[EvaluationRow]) -> dict[str, Any]:
        count = sum(getattr(r.correctness, name) is True for r in items if r.correctness)
        return {"correct": count, "n": len(items), "rate": count / len(items) if items else None}
    known_costs = [r.cost_usd for r in executed if r.cost_usd is not None]
    usage: dict[str, dict[str, int]] = {}
    for row in executed:
        for observed in row.provider_responses:
            endpoint = observed.get("endpoint", "unknown")
            counters = usage.setdefault(endpoint, {"calls": 0, "usage_reported_calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
            counters["calls"] += 1
            returned = observed.get("usage")
            if isinstance(returned, dict):
                counters["usage_reported_calls"] += 1
                for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = returned.get(field)
                    if type(value) is int and value >= 0:
                        counters[field] += value
    return {"mode": mode, "attempted": len(executed), "skipped": len(all_rows) - len(executed),
        "passed": sum(r.status == "PASS" for r in executed),
        "failed_ids": [r.question_id for r in executed if r.status == "FAIL"],
        "skip_reasons": sorted({r.skip_reason for r in all_rows if r.skip_reason}),
        "top1_fix": metric("top1_fix", supported), "exact_parts": metric("exact_parts", supported),
        "citation_validity_answered": metric("citations_valid", answered),
        "citation_coverage_expected_supported": metric("citations_valid", supported),
        "graph_path_coverage": metric("has_graph_path", supported),
        "audited_rates": metric("rates_valid", supported),
        "refusal_correctness": metric("refusal_correct", refused),
        "routing_accuracy": metric("routing_correct", executed),
        "retrieval_recall_at_5": {"mean": sum(recall) / len(recall) if recall else None, "n": len(recall)},
        "latency_ms": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95), "n": len(latencies),
                       "english_p95": percentile(english, .95), "english_n": len(english)},
        "cost_usd_total": sum(known_costs) if len(known_costs) == len(executed) and executed else None,
        "cost_known_queries": len(known_costs), "cost_unknown_queries": len(executed) - len(known_costs),
        "observed_fireworks_usage": usage,
        "trace_statuses": sorted({r.trace_status for r in all_rows})}


def comparison_table(summaries: list[dict[str, Any]]) -> str:
    lines = ["# Supervisor comparison — synthetic frozen evaluation", "",
        "| Mode | Run / skipped | Pass / fail | Top-1 fix | Exact parts | Valid citations (answered) | Graph paths | Refusals | Recall@5 | p50 / p95 ms | Total USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def ratio(value: dict[str, Any]) -> str:
        return f"{value['correct']}/{value['n']}"
    for s in summaries:
        recall = s['retrieval_recall_at_5']['mean']
        p50, p95 = s['latency_ms']['p50'], s['latency_ms']['p95']
        latency = f"{p50:.1f} / {p95:.1f}" if p50 is not None else "NOT RUN"
        recall_text = f"{recall:.3f}" if recall is not None else "N/A"
        lines.append(f"| {s['mode']} | {s['attempted']} / {s['skipped']} | {s['passed']} / {s['attempted']-s['passed']} | "
            f"{ratio(s['top1_fix'])} | {ratio(s['exact_parts'])} | {ratio(s['citation_validity_answered'])} | "
            f"{ratio(s['graph_path_coverage'])} | {ratio(s['refusal_correctness'])} | {recall_text} | {latency} | unknown |")
    lines += ["", "Top-1/part denominators include all 27 expected-supported questions, including failed answers.",
        "Citation validity uses answered questions; coverage over all 27 is in aggregate.json.",
        "Pass means correct fix/part/citation/rate or expected refusal; graph paths are a separate capability metric.",
        "Recall@5 is the fraction of frozen expected source IDs in the first five vector/fused hits, before SQL audit.",
        "Graph evidence is an unranked traversal set; graph recall@5 is N/A. Combined recall measures its hybrid component only.",
        "Costs are unknown: token usage is recorded where returned; no billing total or verified tariffs were supplied.",
        "Latencies are serial single observations, include retrieval/SQL audit/generation, exclude setup, scoring and trace upload.",
        "Tamil cases test correct IDs, parts and grounding; output-language quality is not scored.", ""]
    return "\n".join(lines)
