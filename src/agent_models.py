"""Closed contracts for routing, model proposals and audited execution."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from src.schemas import Contract, DiagnosisResponse, Evidence, NonEmpty

Route = Literal["sql", "dense", "hybrid", "graph", "combined", "refuse"]


class RouteDecision(Contract):
    route: Route
    reason: NonEmpty
    alarm_code: str | None = None
    equipment_id: str | None = None


class GroundedFact(Contract):
    fix_id: NonEmpty
    part_numbers: tuple[NonEmpty, ...]
    source_ids: tuple[NonEmpty, ...]
    worked: Annotated[int, Field(ge=0)]
    did_not_work: Annotated[int, Field(ge=0)]
    rate: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    quote_source_id: NonEmpty
    quote: Annotated[str, Field(min_length=20, max_length=2000)]


class Explanation(Contract):
    """The model selects a supporting excerpt, never supplies unchecked prose."""

    facts: Annotated[tuple[GroundedFact, ...], Field(min_length=1, max_length=3)]


class ExplanationAdapter(Protocol):
    provider: str

    def explain(self, facts: tuple[GroundedFact, ...], evidence: tuple[Evidence, ...]) -> Explanation: ...


class AgentRun(Contract):
    decision: RouteDecision
    response: DiagnosisResponse
    providers: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    explanation_provider: str | None = None
    explanation_model: str | None = None
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
