"""Read-only evaluation instrumentation; no provider headers or secrets recorded."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from urllib.parse import urlsplit
import ssl

import httpx

from src.adapters.contracts import GraphAdapter
from src.adapters.graph_common import path_evidence
from src.data_models import Dataset, WorkOrder
from src.graph_models import FixHistory, GraphCounts, GraphFixPath
from src.retrieval.dense import DenseRetriever
from src.retrieval.models import SearchHit
from src.schemas import DiagnosisRequest, Evidence


class HTTPObservations:
    """Successful response payloads only; error bodies/headers are withheld."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def observe(self, response: httpx.Response) -> None:
        response.read()
        endpoint = response.request.url.path
        record: dict[str, Any] = {"endpoint": endpoint, "http_status": response.status_code}
        if response.is_success:
            try:
                body = response.json()
                record.update({k: body[k] for k in ("model", "usage", "choices") if k in body})
                if endpoint.endswith("/embeddings"):
                    record["vectors_recorded"] = False
                    record["embedding_rows"] = len(body.get("data", []))
            except Exception:
                record["payload_status"] = "invalid_json"
        self.records.append(record)

    def client(self, ca_file: Path | None = None) -> httpx.Client:
        return httpx.Client(timeout=60, follow_redirects=False,
            verify=ssl.create_default_context(cafile=str(ca_file)) if ca_file else True,
            event_hooks={"response": [self.observe]})


class ObservedRetriever:
    def __init__(self, retriever: DenseRetriever) -> None:
        self.retriever = retriever
        self.hits: tuple[SearchHit, ...] = ()
        self.called = False

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        self.called = True
        query = f"{request.alarm_code} {request.query}" if request.alarm_code else request.query
        self.hits = self.retriever.search(query, top_k=5, equipment_id=request.equipment_id)
        return tuple(Evidence(source_id=h.document.source_id, source_kind=h.document.source_kind,
            text=h.document.text, provider=f"{h.provider}:{h.score_kind}", score=h.score) for h in self.hits)


class FrozenGraph:
    """Live traversal, projected to immutable baseline WO IDs without DB writes.

    Additional feedback remains in the live store. Missing or altered baseline
    rows fail closed; they are never synthesized from the answer key.
    """

    def __init__(self, graph: GraphAdapter, baseline: Dataset) -> None:
        self.graph = graph
        self.status = graph.status
        self.baseline = baseline
        self.paths: list[GraphFixPath] = []
        self.excluded_ids: set[str] = set()

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory:
        actual = self.graph.fix_history(equipment_id, fix_id)
        return self._project(actual)

    def _project(self, actual: FixHistory) -> FixHistory:
        known = {w.work_order_id: w for w in self.baseline.work_orders}
        self.excluded_ids.update(w.work_order_id for w in actual.work_orders if w.work_order_id not in known)
        selected = tuple(w for w in actual.work_orders if w.work_order_id in known)
        expected = tuple(sorted((w for w in self.baseline.work_orders
            if w.equipment_id == actual.equipment_id and w.fix_id == actual.fix_id), key=lambda w: w.work_order_id))
        if tuple(sorted(selected, key=lambda w: w.work_order_id)) != expected:
            raise ValueError("Live graph does not contain the unchanged frozen baseline history")
        return FixHistory(equipment_id=actual.equipment_id, fix_id=actual.fix_id, work_orders=expected)

    def alarm_paths(self, alarm_code: str, equipment_id: str | None = None) -> tuple[GraphFixPath, ...]:
        paths = self.graph.alarm_paths(alarm_code, equipment_id)
        projected = tuple(p.model_copy(update={"history": self._project(p.history)}) for p in paths)
        self.paths.extend(projected)
        return projected

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        if not request.alarm_code:
            return ()
        return path_evidence(self.alarm_paths(request.alarm_code, request.equipment_id))

    def counts(self) -> GraphCounts:
        return self.graph.counts()

    def seed(self, data: Dataset) -> GraphCounts:
        raise ValueError("Frozen evaluation projection is read-only")

    def upsert_work_order(self, order: WorkOrder) -> None:
        raise ValueError("Frozen evaluation projection is read-only")

    def close(self) -> None:
        # The runner owns the underlying adapter lifetime.
        pass


class LangSmithEvaluationTrace:
    """Optional official SDK, imported only when explicitly configured.

    Synchronous writes allow a failed upload to be reported without claiming a
    delivered trace. Only synthetic requests and sanitized evaluation rows leave.
    """

    def __init__(self, values: dict[str, str | None]) -> None:
        self.client: Any = None
        self.project = values.get("LANGSMITH_PROJECT") or "linemedic-synthetic-evaluation"
        self.status = "NOT_RUN: LANGSMITH_TRACING disabled or API key missing"
        if (values.get("LANGSMITH_TRACING") or "").lower() == "true" and values.get("LANGSMITH_API_KEY"):
            try:
                from langsmith import Client
                import requests

                endpoint = values.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com"
                parsed = urlsplit(endpoint)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
                    raise ValueError("LangSmith endpoint must be a credential-free HTTPS URL")
                session = requests.Session()
                if values.get("SSL_CERT_FILE"):
                    session.verify = values["SSL_CERT_FILE"]
                self.client = Client(api_key=values["LANGSMITH_API_KEY"],
                    api_url=endpoint, workspace_id=values.get("LANGSMITH_WORKSPACE_ID") or None,
                    session=session, timeout_ms=15000, auto_batch_tracing=False, omit_traced_runtime_info=True,
                    tracing_sampling_rate=1.0)
                self.status = "configured; uploads not yet attempted"
            except Exception:
                self.status = "NOT_RUN: LangSmith SDK unavailable or configuration invalid"

    def record(self, inputs: dict[str, Any], outputs: dict[str, Any],
               started: datetime, ended: datetime) -> str:
        if self.client is None:
            return self.status
        try:
            self.client.create_run(name="LineMedic frozen evaluation", run_type="chain", id=uuid4(),
                project_name=self.project, inputs=inputs, outputs=outputs,
                start_time=started.astimezone(timezone.utc), end_time=ended.astimezone(timezone.utc),
                tags=["synthetic-only", "frozen-30"])
            return "PASS: synchronous trace upload acknowledged"
        except Exception:
            return "FAIL: LangSmith trace upload failed; provider details withheld"

    def close(self) -> None:
        if self.client:
            self.client.close()
