"""Typed, secret-free projection of verified graph paths for visual exploration."""

from typing import Literal
from pydantic import Field, model_validator

from src.graph_models import GraphCounts, GraphStatus
from src.pdf_models import PDFCitation
from src.schemas import Contract, NonEmpty, OutcomeCounts

NodeKind = Literal['equipment', 'alarm', 'failure', 'fix', 'part', 'manual', 'work_order']
EdgeKind = Literal['RAISED_BY', 'INDICATES', 'RESOLVED_BY', 'REQUIRES', 'ON', 'APPLIED', 'DESCRIBES']


class ExplorerNode(Contract):
    id: NonEmpty
    kind: NodeKind
    title: NonEmpty
    description: NonEmpty
    outcomes: OutcomeCounts | None = None
    outcome: Literal['worked', 'did_not_work'] | None = None
    citation: PDFCitation | None = None


class ExplorerEdge(Contract):
    id: NonEmpty
    source: NonEmpty
    target: NonEmpty
    relationship: EdgeKind


class ExplorerGraph(Contract):
    synthetic: Literal[True] = True
    status: GraphStatus
    alarm_code: NonEmpty
    equipment_id: NonEmpty
    store_counts: GraphCounts
    path_count: int = Field(ge=0)
    nodes: tuple[ExplorerNode, ...]
    edges: tuple[ExplorerEdge, ...]
    history_audited_against_sql: Literal[True] = True
    notice: NonEmpty = 'Retrieved relationships are evidence, not recommended actions. Recorded outcomes are synthetic.'

    @model_validator(mode='after')
    def valid_edges(self) -> 'ExplorerGraph':
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError('Duplicate graph entities')
        if any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError('Orphan relationship')
        return self
