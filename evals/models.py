"""Closed result contracts; unknown measurements remain null."""

from typing import Any, Literal

from pydantic import Field

from src.agent_models import AgentRun, Route, RouteDecision
from src.schemas import Contract

Mode = Literal["dense", "hybrid", "graph", "combined"]


class Correctness(Contract):
    status_match: bool
    top1_fix: bool | None = None
    exact_parts: bool | None = None
    citations_valid: bool | None = None
    rates_valid: bool | None = None
    refusal_correct: bool | None = None
    routing_correct: bool
    expected_router_route: Route
    retrieval_recall_at_5: float | None = Field(default=None, ge=0, le=1)
    has_graph_path: bool = False
    failures: tuple[str, ...] = ()


class EvaluationRow(Contract):
    question_id: str
    mode: Mode
    category: str
    language: str
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    skip_reason: str | None = None
    natural_decision: RouteDecision | None = None
    run: AgentRun | None = None
    correctness: Correctness | None = None
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_status: str = "UNKNOWN: provider billing and verified tariffs unavailable"
    provider_responses: tuple[dict[str, Any], ...] = ()
    retrieved_hits: tuple[dict[str, Any], ...] = ()
    graph_paths: tuple[dict[str, Any], ...] = ()
    pdf_citations: tuple[dict[str, Any], ...] = ()
    source_metadata: tuple[dict[str, Any], ...] = ()
    trace_status: str = "NOT_RUN"
