"""Local FastAPI façade. No services connect on import or application startup."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
from threading import RLock

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api_models import (Catalog, ConnectionRequest, DiagnosisInput, DiagnosisOutput, EvaluationMode,
    EvaluationOutput, FeedbackPreparation, FeedbackSubmission, ServiceStatus, SupervisorAction, SyncOutput)
from src.application import Application, create_application
from src.adapters.pdf_manuals import SyntheticPDFAdapter
from src.adapters.pdf_preview import render_verified_page
from src.feedback_models import FeedbackEvent, FeedbackReceipt, SupervisorReport
from src.deployment import load_deployment_settings
from src.retrieval.corpus import load_dataset
from src.retrieval.pdf_metadata import build_pdf_metadata
from src.schemas import DiagnosisRequest

ROOT = Path(__file__).resolve().parents[1]
Factory = Callable[[str], Application]


def live_factory(backend: str) -> Application:
    return create_application(ROOT / ".env", backend)


class Runtime:
    def __init__(self, factory: Factory) -> None:
        self.factory = factory
        self.application: Application | None = None
        self.lock = RLock()

    def required(self) -> Application:
        if self.application is None:
            raise HTTPException(503, "Connect live services before requesting this operation.")
        return self.application

    def status(self) -> ServiceStatus:
        app = self.application
        return ServiceStatus(connected=app is not None,
            graph_provider=app.graph.status.provider if app else None,
            graph_mode=app.graph.status.mode if app else None,
            vector_namespace=app._retrieval.index.namespace if app and app._retrieval else None,
            pdf_mode=bool(app and app.pdf_dataset))

    def close(self) -> None:
        if self.application:
            self.application.close()
            self.application = None


def read_evaluation(root: Path) -> EvaluationOutput:
    directory = root / "evals/results"
    pointer = directory / "latest-frozen30.txt"
    if not pointer.is_file():
        directory = root / "evals/published"
        pointer = directory / "latest-frozen30.txt"
    if not pointer.is_file():
        return EvaluationOutput(status="not_run")
    name = pointer.read_text(encoding="utf-8").strip()
    if not name or Path(name).name != name or not (directory / name).resolve().is_relative_to(directory.resolve()):
        raise HTTPException(503, "Evaluation pointer failed validation.")
    try:
        summary = json.loads((directory / name / "aggregate.json").read_bytes())
        manifest = json.loads((directory / name / "manifest.json").read_bytes())
        modes = tuple(EvaluationMode(mode=s["mode"], attempted=s["attempted"], skipped=s["skipped"], passed=s["passed"],
            top1_correct=s["top1_fix"]["correct"], top1_n=s["top1_fix"]["n"],
            citation_correct=s["citation_validity_answered"]["correct"], citation_n=s["citation_validity_answered"]["n"],
            refusals_correct=s["refusal_correctness"]["correct"], refusals_n=s["refusal_correctness"]["n"],
            recall_at_5=s["retrieval_recall_at_5"]["mean"], p50_ms=s["latency_ms"]["p50"],
            p95_ms=s["latency_ms"]["p95"], english_p95_ms=s["latency_ms"]["english_p95"],
            failed_ids=tuple(s["failed_ids"]), cost_usd=s["cost_usd_total"]) for s in summary)
        return EvaluationOutput(status="measured", run_id=name, started_at=manifest["started_at"],
            pdf_backed="pdf_pipeline" in manifest, modes=modes)
    except Exception:
        raise HTTPException(503, "Saved evaluation summary is unavailable or invalid.") from None


def create_api(factory: Factory = live_factory, root: Path = ROOT) -> FastAPI:
    runtime = Runtime(factory)
    deployment = load_deployment_settings()

    @asynccontextmanager
    async def lifespan(server: FastAPI) -> AsyncIterator[None]:
        yield
        runtime.close()

    server = FastAPI(title="LineMedic synthetic demo API", version="0.1.0", lifespan=lifespan)
    server.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver",
        "healthcheck.railway.app", *([deployment.domain] if deployment.domain else [])])
    server.state.runtime = runtime

    @server.middleware("http")
    async def local_origin(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        allowed = {"http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"}
        allowed.add(str(request.base_url).rstrip("/"))
        if deployment.domain:
            allowed.add('https://' + deployment.domain)
        if deployment.enabled:
            if not deployment.ready:
                return JSONResponse({'detail': 'Hosted demo settings are incomplete.'}, status_code=503)
            if request.url.path != '/api/health' and not deployment.authorized(request.headers.get('authorization', '')):
                return JSONResponse({'detail': 'Demo access credentials required.'}, status_code=401,
                    headers={'WWW-Authenticate': 'Basic realm="LineMedic synthetic demo", charset="UTF-8"',
                             'Cache-Control': 'no-store'})
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") not in (*allowed, None):
            return JSONResponse({"detail": "Foreign-origin write requests are disabled for this local demo."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @server.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid request fields or values."}, status_code=422)

    @server.exception_handler(Exception)
    async def controlled_error(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Service unavailable or response failed validation; details withheld."}, status_code=503)

    @server.get("/api/status")
    def status() -> ServiceStatus:
        with runtime.lock:
            return runtime.status()

    @server.get('/api/health')
    def health() -> dict[str, str]:
        return {'status': 'ok', 'scope': 'HTTP process only; provider readiness requires connection'}

    @server.get("/api/catalog")
    def catalog() -> Catalog:
        baseline = load_dataset(root / "data")
        packet = SyntheticPDFAdapter().parse(root / "output/pdf/manifest.json", baseline)
        return Catalog(alarms=baseline.alarms, equipment=baseline.equipment, manuals=packet.dataset.manual_sections,
            pdf_files=packet.manifest.files, citations=packet.manifest.citations,
            counts={"alarms": len(baseline.alarms), "equipment": len(baseline.equipment),
                "manual_sections": len(packet.chunks), "pdf_files": len(packet.manifest.files),
                "baseline_work_orders": len(baseline.work_orders)})

    @server.post("/api/connection")
    def connect(body: ConnectionRequest) -> ServiceStatus:
        with runtime.lock:
            if runtime.application is None:
                try:
                    runtime.application = runtime.factory(body.graph_backend)
                except Exception:
                    raise HTTPException(503, "Could not connect configured services. Check server settings and schema.") from None
            return runtime.status()

    @server.delete("/api/connection")
    def disconnect() -> ServiceStatus:
        with runtime.lock:
            runtime.close()
            return runtime.status()

    @server.post("/api/diagnosis")
    def diagnose(body: DiagnosisInput) -> DiagnosisOutput:
        with runtime.lock:
            application = runtime.required()
            request = DiagnosisRequest(query=body.query, alarm_code=body.alarm_code)
            mode = None if body.mode == "auto" else body.mode
            result = application.diagnose(request, retrieval_mode=mode)
            metadata = build_pdf_metadata(application.pdf_dataset) if application.pdf_dataset else {}
            return DiagnosisOutput(run=result, source_metadata=tuple(metadata[e.source_id] for e in result.response.evidence
                if e.source_id in metadata))

    @server.post("/api/feedback/prepare")
    def prepare_feedback(body: FeedbackPreparation) -> FeedbackEvent:
        if body.role != "technician":
            raise HTTPException(403, "Select the technician demo role to record a synthetic observation.")
        with runtime.lock:
            try:
                return runtime.required().new_observation(body.alarm_code, body.fix_id, "SYN-ACTOR-002", body.outcome)
            except ValueError:
                raise HTTPException(400, "Feedback context failed validation.") from None

    @server.post("/api/feedback")
    def submit_feedback(body: FeedbackSubmission) -> FeedbackReceipt:
        if body.role != "technician":
            raise HTTPException(403, "Technician demo role required.")
        with runtime.lock:
            try:
                event = FeedbackEvent.model_validate_json(body.event_json)
                return runtime.required().submit_feedback(event)
            except ValueError:
                raise HTTPException(400, "Feedback event failed validation.") from None

    @server.get("/api/supervisor")
    def supervisor() -> SupervisorReport:
        with runtime.lock:
            return runtime.required().supervisor_report()

    @server.post("/api/graph/retry")
    def retry_graph(body: SupervisorAction) -> SyncOutput:
        if body.role != "supervisor":
            raise HTTPException(403, "Supervisor demo role required.")
        with runtime.lock:
            return SyncOutput(receipts=runtime.required().retry_sync())

    @server.get("/api/evaluation")
    def evaluation() -> EvaluationOutput:
        return read_evaluation(root)

    @server.get("/api/manuals/{filename}")
    def manual(filename: str) -> FileResponse:
        baseline = load_dataset(root / "data")
        manifest_path = root / "output/pdf/manifest.json"
        packet = SyntheticPDFAdapter().parse(manifest_path, baseline)
        file = next((f for f in packet.manifest.files if f.filename == filename), None)
        if file is None:
            raise HTTPException(404, "Unknown synthetic manual.")
        path = manifest_path.parent / file.filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != file.sha256:
            raise HTTPException(409, "PDF content changed; reconnect to a verified packet.")
        return FileResponse(path, media_type="application/pdf", content_disposition_type="inline", filename=file.filename)

    @server.get("/api/manuals/{filename}/pages/{page_number}.png")
    def manual_page(filename: str, page_number: int) -> Response:
        baseline = load_dataset(root / "data")
        packet = SyntheticPDFAdapter().parse(root / "output/pdf/manifest.json", baseline)
        file = next((f for f in packet.manifest.files if f.filename == filename), None)
        if file is None or not 1 <= page_number <= file.pages:
            raise HTTPException(404, "Unknown synthetic PDF file or physical page.")
        image = render_verified_page(root / "output/pdf" / file.filename, page_number, file.sha256)
        return Response(image, media_type="image/png")

    if (root / "web/dist").is_dir():
        server.mount("/", StaticFiles(directory=root / "web/dist", html=True), name="dashboard")
    return server


app = create_api()
