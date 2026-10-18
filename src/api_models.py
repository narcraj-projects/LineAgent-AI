"""Secret-free HTTP contracts for the local React demo."""

from typing import Annotated, Literal

from pydantic import Field

from src.agent_models import AgentRun
from src.data_models import Alarm, Equipment, ManualSection
from src.feedback_models import FeedbackReceipt
from src.graph import GraphBackend
from src.pdf_models import PDFCitation, PDFFile, RAGSourceMetadata
from src.schemas import Contract, NonEmpty

DemoRole = Literal["operator", "technician", "supervisor"]
SelectedMode = Literal["auto", "dense", "hybrid", "graph", "combined"]


class ConnectionRequest(Contract):
    graph_backend: GraphBackend = "auto"


class ServiceStatus(Contract):
    connected: bool
    graph_provider: str | None = None
    graph_mode: str | None = None
    vector_namespace: str | None = None
    pdf_mode: bool = False
    demo_auth: Literal[True] = True
    synthetic: Literal[True] = True


class ConfigurationStatus(Contract):
    required_present: dict[str, bool]
    chat_model_format_valid: bool
    supabase_key_is_publishable: bool
    custom_ca_file_exists: bool | None
    source_revision: str | None = None
    scope: Literal['Configuration checks only; no live service verification'] = 'Configuration checks only; no live service verification'


class Catalog(Contract):
    synthetic: Literal[True] = True
    snapshot_label: str = "Frozen synthetic catalog; persistence totals require a live connection"
    alarms: tuple[Alarm, ...]
    equipment: tuple[Equipment, ...]
    manuals: tuple[ManualSection, ...]
    pdf_files: tuple[PDFFile, ...]
    citations: tuple[PDFCitation, ...]
    counts: dict[str, int]


class DiagnosisInput(Contract):
    query: Annotated[str, Field(min_length=1, max_length=4000)]
    alarm_code: NonEmpty | None = None
    mode: SelectedMode = "auto"


class DiagnosisOutput(Contract):
    run: AgentRun
    source_metadata: tuple[RAGSourceMetadata, ...] = ()


class FeedbackPreparation(Contract):
    role: DemoRole
    alarm_code: NonEmpty
    fix_id: NonEmpty
    outcome: Literal["worked", "did_not_work"]


class FeedbackSubmission(Contract):
    role: DemoRole
    # Strict UUID/datetime JSON parsing happens through FeedbackEvent's contract.
    event_json: Annotated[str, Field(min_length=1, max_length=20000)]


class SupervisorAction(Contract):
    role: DemoRole


class SyncOutput(Contract):
    receipts: tuple[FeedbackReceipt, ...]


class EvaluationMode(Contract):
    mode: str
    attempted: int = Field(ge=0)
    skipped: int = Field(ge=0)
    passed: int = Field(ge=0)
    top1_correct: int = Field(ge=0)
    top1_n: int = Field(ge=0)
    citation_correct: int = Field(ge=0)
    citation_n: int = Field(ge=0)
    refusals_correct: int = Field(ge=0)
    refusals_n: int = Field(ge=0)
    recall_at_5: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    english_p95_ms: float | None = None
    failed_ids: tuple[str, ...] = ()
    cost_usd: float | None = None


class EvaluationOutput(Contract):
    status: Literal["measured", "not_run"]
    run_id: str | None = None
    started_at: str | None = None
    pdf_backed: bool = False
    modes: tuple[EvaluationMode, ...] = ()
