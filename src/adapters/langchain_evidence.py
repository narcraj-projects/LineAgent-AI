"""Read-only LangChain tools for exact SQL and graph evidence; never model-chosen."""
from typing import Literal

from langchain_core.tools import StructuredTool
from langsmith import tracing_context

from src.adapters.contracts import GraphAdapter
from src.adapters.diagnosis_contracts import SQLEvidenceAdapter
from src.graph_models import GraphFixPath
from src.schemas import Contract, DiagnosisRequest, Evidence, NonEmpty, RetrievalMode


class GraphLookup(Contract):
    alarm_code: NonEmpty
    equipment_id: NonEmpty | None = None


def sql_evidence_tool(sql: SQLEvidenceAdapter, request: DiagnosisRequest) -> tuple[Evidence, ...]:
    def retrieve(query: str, alarm_code: str | None = None, equipment_id: str | None = None,
                 mode: RetrievalMode = RetrievalMode.COMBINED,
                 data_mode: Literal["synthetic"] = "synthetic") -> tuple[Evidence, ...]:
        return sql.retrieve(DiagnosisRequest(query=query, alarm_code=alarm_code,
            equipment_id=equipment_id, mode=mode, data_mode=data_mode))

    tool = StructuredTool.from_function(retrieve, name="sql_exact",
        description="Read canonical synthetic SQL evidence; no arbitrary SQL.", args_schema=DiagnosisRequest)
    with tracing_context(enabled=False):
        result = tool.invoke(request.model_dump(), config={"callbacks": []})
    return tuple(Evidence.model_validate_json(item.model_dump_json()) for item in result)


def graph_paths_tool(graph: GraphAdapter, alarm_code: str,
                     equipment_id: str | None = None) -> tuple[GraphFixPath, ...]:
    tool = StructuredTool.from_function(graph.alarm_paths, name="graph_paths",
        description="Read allowlisted canonical graph paths; no arbitrary Cypher.", args_schema=GraphLookup)
    with tracing_context(enabled=False):
        result = tool.invoke({"alarm_code": alarm_code, "equipment_id": equipment_id}, config={"callbacks": []})
    return tuple(GraphFixPath.model_validate_json(item.model_dump_json()) for item in result)
