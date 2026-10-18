"""Synchronous fail-closed orchestration with injected service adapters."""

import logging
from time import perf_counter
from typing import Literal

from src.adapters.contracts import GraphAdapter, RetrievalAdapter
from src.adapters.diagnosis_contracts import SQLEvidenceAdapter
from src.agent_models import AgentRun, ExplanationAdapter
from src.data_models import Dataset
from src.diagnosis import GroundingError, prepare_facts, validated_answer
from src.graph_models import GraphFixPath
from src.router import select_route
from src.safety import refusal
from src.schemas import DiagnosisRequest, Evidence

LOGGER = logging.getLogger(__name__)


class DiagnosisAgent:
    def __init__(self, data: Dataset, sql: SQLEvidenceAdapter, explanation: ExplanationAdapter,
                 dense: RetrievalAdapter | None = None, hybrid: RetrievalAdapter | None = None,
                 graph: GraphAdapter | None = None) -> None:
        self.data = Dataset.model_validate_json(data.model_dump_json())
        self.sql, self.explanation = sql, explanation
        self.dense, self.hybrid, self.graph = dense, hybrid, graph

    def run(self, request: DiagnosisRequest, *,
            evaluation_mode: Literal["dense", "hybrid", "graph", "combined"] | None = None,
            retrieval_mode: Literal["dense", "hybrid", "graph", "combined"] | None = None) -> AgentRun:
        request = DiagnosisRequest.model_validate_json(request.model_dump_json())
        started = perf_counter()
        decision = select_route(request)
        # Explicit benchmark or UI retrieval selection. Safety and context
        # remain authoritative; ordinary application calls retain router behavior.
        selected_mode = retrieval_mode or evaluation_mode
        if evaluation_mode is not None and retrieval_mode is not None:
            raise ValueError("Specify only one retrieval override")
        if selected_mode is not None:
            if selected_mode not in {"dense", "hybrid", "graph", "combined"}:
                raise ValueError("Unknown evaluation mode")
            if decision.route != "refuse":
                decision = decision.model_copy(update={"route": selected_mode,
                    "reason": "Explicit retrieval selection; safety/context router retained."})
        tools: list[str] = []
        providers: set[str] = set()
        explanation_provider = None
        response = refusal(decision.reason)
        try:
            if decision.route != "refuse":
                eq = decision.equipment_id
                alarm = next((a for a in self.data.alarms if a.code == decision.alarm_code), None)
                if decision.alarm_code and alarm is None:
                    raise GroundingError("Unknown synthetic alarm; no evidence is available.")
                if alarm is not None:
                    if eq and eq != alarm.equipment_id:
                        raise GroundingError("Alarm and equipment context conflict.")
                    eq = alarm.equipment_id
                if eq not in {e.equipment_id for e in self.data.equipment}:
                    raise GroundingError("Specify a known synthetic equipment ID.")
                decision = decision.model_copy(update={"equipment_id": eq})
                scoped = request.model_copy(update={"equipment_id": eq, "alarm_code": decision.alarm_code})
                evidence: list[Evidence] = []
                if decision.route == "sql":
                    tools.append("sql_exact")
                    evidence.extend(self.sql.retrieve(scoped))
                if decision.route in ("graph", "combined"):
                    if self.graph is None:
                        raise GroundingError("Graph adapter unavailable.")
                    tools.append("graph_paths")
                    codes = (alarm.code,) if alarm else tuple(a.code for a in self.data.alarms if a.equipment_id == eq)
                    for code in codes:
                        paths = self.graph.alarm_paths(code, eq)
                        expected_alarm = next(a for a in self.data.alarms if a.code == code)
                        mode = next(m for m in self.data.failure_modes if m.failure_mode_id == expected_alarm.failure_mode_id)
                        for path in paths:
                            path = GraphFixPath.model_validate_json(path.model_dump_json())
                            fix = next((f for f in self.data.fixes if f.fix_id == path.fix_id), None)
                            if (path.alarm_id != expected_alarm.alarm_id or path.alarm_code != code or path.equipment_id != eq
                                    or path.failure_mode_id != mode.failure_mode_id or path.fix_id not in mode.fix_ids
                                    or fix is None or path.part_numbers != fix.part_numbers
                                    or path.history.work_orders != self.sql.fix_history(eq, path.fix_id).work_orders):
                                raise GroundingError("Graph facts or rates conflict with SQL evidence.")
                            if any(m not in self.data.manual_sections for m in path.manuals):
                                raise GroundingError("Graph manual provenance is unverified.")
                        from src.adapters.graph_common import path_evidence
                        evidence.extend(path_evidence(paths))
                if decision.route in ("dense", "hybrid", "combined"):
                    retriever = self.dense if decision.route == "dense" else self.hybrid
                    if retriever is None:
                        raise GroundingError("Selected retrieval adapter unavailable.")
                    tools.append("dense" if decision.route == "dense" else "hybrid")
                    evidence.extend(retriever.retrieve(scoped))
                providers.update(e.provider for e in evidence)
                tools.append("sql_history_audit")
                providers.add(self.sql.provider)
                facts, sources, candidates = prepare_facts(self.data, scoped, tuple(evidence), self.sql)
                tools.append("structured_explanation")
                explanation_provider = self.explanation.provider
                proposal = self.explanation.explain(facts, sources)
                response = validated_answer(proposal, facts, sources, candidates)
        except GroundingError as error:
            response = refusal(str(error))
        except Exception:
            # Neither provider payloads nor exception messages are safe to log.
            response = refusal("Evidence or explanation service failed validation or is unavailable.")
        elapsed = (perf_counter() - started) * 1000
        LOGGER.info("route=%s status=%s tools=%s elapsed_ms=%.3f", decision.route,
                    response.status, ','.join(tools), elapsed)
        return AgentRun(decision=decision, response=response, tools=tuple(tools),
                        providers=tuple(sorted(providers)), explanation_provider=explanation_provider,
                        explanation_model=getattr(self.explanation, "model", None) if explanation_provider else None,
                        elapsed_ms=elapsed)
