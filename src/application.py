"""Application services shared by Streamlit and restart verification scripts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from src.adapters.base import AdapterUnavailable
from src.adapters.contracts import GraphAdapter
from src.adapters.feedback_contracts import FeedbackStore
from src.agent import DiagnosisAgent
from src.agent_models import AgentRun, ExplanationAdapter
from src.data_models import Dataset
from src.feedback import FeedbackService
from src.feedback_models import FeedbackEvent, FeedbackReceipt, HistorySummary, SupervisorReport, new_feedback
from src.retrieval.corpus import build_corpus, load_dataset
from src.router import select_route
from src.schemas import DiagnosisRequest, OutcomeCounts
from src.pdf_models import ParsedPDFDataset
from src.retrieval.pdf_metadata import build_pdf_metadata, pdf_namespace_tag

if TYPE_CHECKING:
    from src.retrieval.live import LiveRetrieval


class Application:
    def __init__(self, baseline: Dataset, store: FeedbackStore, graph: GraphAdapter,
                 explanation: ExplanationAdapter, env_file: Path | None = None) -> None:
        self.baseline, self.store, self.graph, self.explanation = baseline, store, graph, explanation
        self.env_file = env_file
        self._retrieval: LiveRetrieval | None = None
        self.feedback = FeedbackService(store, graph)
        self.pdf_dataset: ParsedPDFDataset | None = None

    def start(self) -> None:
        self.store.seed(self.baseline)
        current = self.store.snapshot()
        if current.model_dump(exclude={"work_orders"}) != self.baseline.model_dump(exclude={"work_orders"}):
            raise AdapterUnavailable("Supabase catalog differs from the canonical synthetic baseline.")
        self.graph.seed(current)
        self.feedback.retry_pending()

    def diagnose(self, request: DiagnosisRequest,
                 retrieval_mode: Literal["dense", "hybrid", "graph", "combined"] | None = None) -> AgentRun:
        # Fresh authoritative history for every request; never cache rate answers.
        route = select_route(request)
        if route.route == "refuse":
            return DiagnosisAgent(self.baseline, self.store, self.explanation).run(request)
        current = self.store.snapshot()
        active_route = retrieval_mode or route.route
        if active_route in ("dense", "hybrid", "combined") and self._retrieval is None:
            from src.adapters.search_settings import load_search_settings
            from src.retrieval.live import open_live_retrieval

            # The existing index holds the immutable baseline. Newly recorded
            # feedback is fetched/cited from SQL, not assigned a fake index score.
            self._retrieval = open_live_retrieval(load_search_settings(self.env_file), build_corpus(self.baseline),
                namespace_tag=pdf_namespace_tag(self.pdf_dataset.manifest) if self.pdf_dataset else None,
                source_metadata=build_pdf_metadata(self.pdf_dataset) if self.pdf_dataset else None)
        agent = DiagnosisAgent(current, self.store, self.explanation,
            self._retrieval.dense if self._retrieval else None,
            self._retrieval.hybrid if self._retrieval else None, self.graph)
        return agent.run(request, retrieval_mode=retrieval_mode)

    def submit_feedback(self, event: FeedbackEvent) -> FeedbackReceipt:
        return self.feedback.submit(event)

    def new_observation(self, alarm_code: str, fix_id: str, actor_id: str,
                        outcome: Literal["worked", "did_not_work"]) -> FeedbackEvent:
        return new_feedback(self.store.snapshot(), alarm_code, fix_id, actor_id, outcome)

    def retry_sync(self) -> tuple[FeedbackReceipt, ...]:
        return self.feedback.retry_pending()

    def report(self) -> tuple[Dataset, tuple[FeedbackReceipt, ...]]:
        return self.store.snapshot(), self.store.pending_feedback()

    def supervisor_report(self) -> SupervisorReport:
        data, pending = self.report()
        groups: dict[tuple[str, str], list[int]] = {}
        for order in data.work_orders:
            counts = groups.setdefault((order.equipment_id, order.fix_id), [0, 0])
            counts[0 if order.outcome == "worked" else 1] += 1
        return SupervisorReport(persisted_work_orders=len(data.work_orders), pending_events=pending,
            histories=tuple(HistorySummary(equipment_id=eq, fix_id=fix,
                outcomes=OutcomeCounts(worked=counts[0], did_not_work=counts[1]))
                for (eq, fix), counts in sorted(groups.items())))

    def close(self) -> None:
        for adapter in (self._retrieval, self.explanation, self.graph, self.store):
            if adapter is not None and hasattr(adapter, "close"):
                try:
                    adapter.close()
                except Exception:
                    pass


def create_application(env_file: Path, graph_backend: str = "auto") -> Application:
    from dotenv import dotenv_values

    from src.adapters.fireworks_explanation import FireworksExplanationAdapter
    from src.adapters.supabase_store import SupabaseStore, load_supabase_settings
    from src.graph import select_graph
    from src.adapters.pdf_manuals import SyntheticPDFAdapter

    local = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
    key = os.environ.get("FIREWORKS_API_KEY", local.get("FIREWORKS_API_KEY"))
    model = os.environ.get("FIREWORKS_MODEL", local.get("FIREWORKS_MODEL"))
    ca = os.environ.get("SSL_CERT_FILE", local.get("SSL_CERT_FILE"))
    pdf_manifest = os.environ.get("LINEMEDIC_PDF_MANIFEST", local.get("LINEMEDIC_PDF_MANIFEST"))
    baseline = load_dataset(Path(__file__).resolve().parents[1] / "data")
    parsed_pdf = None
    if pdf_manifest:
        path = Path(pdf_manifest)
        if not path.is_absolute():
            path = env_file.parent / path
        parsed_pdf = SyntheticPDFAdapter().parse(path, baseline)
        baseline = parsed_pdf.dataset
    if not key or not model:
        raise AdapterUnavailable("Configure FIREWORKS_API_KEY and FIREWORKS_MODEL server-side.")
    store = SupabaseStore(load_supabase_settings(env_file))
    graph = None
    explanation = None
    app = None
    try:
        graph = select_graph(graph_backend, env_file)
        explanation = FireworksExplanationAdapter(key, model, Path(ca) if ca else None)
        app = Application(baseline, store, graph, explanation, env_file)
        app.pdf_dataset = parsed_pdf
        app.start()
        return app
    except Exception:
        if app is not None:
            app.close()
        else:
            for adapter in (store, graph, explanation):
                if adapter is not None:
                    adapter.close()
        raise AdapterUnavailable("Services unavailable. Check Supabase settings/schema, chat model, and graph configuration.") from None
