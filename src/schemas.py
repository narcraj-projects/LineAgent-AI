"""Typed boundary contracts; these schemas do not generate diagnoses or evidence."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RetrievalMode(str, Enum):
    DENSE = "dense"
    HYBRID = "hybrid"
    GRAPH = "graph"
    COMBINED = "combined"


class DiagnosisRequest(Contract):
    query: Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
    alarm_code: NonEmpty | None = None
    equipment_id: NonEmpty | None = None
    mode: RetrievalMode = RetrievalMode.COMBINED
    data_mode: Literal["synthetic"] = "synthetic"


class Evidence(Contract):
    source_id: NonEmpty
    text: NonEmpty
    source_kind: Literal["manual", "work_order"]
    provider: NonEmpty
    synthetic: Literal[True] = True
    score: Annotated[float, Field(allow_inf_nan=False)] | None = None
    path: tuple[NonEmpty, ...] = ()


class OutcomeCounts(Contract):
    """Future repository supplies real counts; zero observations yields no rate."""

    worked: Annotated[int, Field(ge=0)]
    did_not_work: Annotated[int, Field(ge=0)]

    @property
    def success_rate(self) -> float | None:
        total = self.worked + self.did_not_work
        return self.worked / total if total else None


class FixCandidate(Contract):
    fix_id: NonEmpty
    cause: NonEmpty
    part_number: NonEmpty | None = None
    evidence_source_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    graph_path: tuple[NonEmpty, ...] = ()
    outcomes: OutcomeCounts
    safety_warning: NonEmpty


class DiagnosisResponse(Contract):
    status: Literal["refused", "supported"]
    message: NonEmpty
    candidates: Annotated[tuple[FixCandidate, ...], Field(max_length=3)] = ()
    evidence: tuple[Evidence, ...] = ()
    synthetic: Literal[True] = True

    @model_validator(mode="after")
    def validate_grounding(self) -> "DiagnosisResponse":
        ids = [item.source_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence source IDs must be unique")
        if self.status == "refused" and self.candidates:
            raise ValueError("Refusals cannot include repair candidates")
        if self.status == "supported" and not self.candidates:
            raise ValueError("Supported responses require grounded candidates")
        known_ids = set(ids)
        for candidate in self.candidates:
            if not set(candidate.evidence_source_ids).issubset(known_ids):
                raise ValueError("Candidate citation is absent from retrieved evidence")
        return self
