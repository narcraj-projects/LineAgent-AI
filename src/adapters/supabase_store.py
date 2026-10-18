"""Service-role Supabase PostgREST RPC adapter. Never execute arbitrary SQL."""

import hashlib
import json
import os
from pathlib import Path
import ssl
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import ConfigDict, SecretStr, TypeAdapter

from src.adapters.base import AdapterUnavailable
from src.data_models import Dataset, WorkOrder
from src.feedback_models import FeedbackEvent, FeedbackReceipt, validate_feedback
from src.graph_models import FixHistory
from src.schemas import Contract, DiagnosisRequest, Evidence, NonEmpty

RPCS = frozenset({"seed", "snapshot", "history", "exact_sources", "record_feedback", "pending_feedback", "ack_sync"})


class SupabaseSettings(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    url: NonEmpty
    service_role_key: SecretStr
    ca_file: Path | None = None


def load_supabase_settings(env_file: Path | None = None) -> SupabaseSettings:
    from dotenv import dotenv_values

    local = dotenv_values(env_file, interpolate=False) if env_file and env_file.is_file() else {}
    url = os.environ.get("SUPABASE_URL", local.get("SUPABASE_URL"))
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", local.get("SUPABASE_SERVICE_ROLE_KEY"))
    ca = os.environ.get("SSL_CERT_FILE", local.get("SSL_CERT_FILE"))
    if not url or not url.strip() or not key or not key.strip():
        raise AdapterUnavailable("Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY server-side.")
    if key.startswith("sb_publishable_"):
        raise AdapterUnavailable("Use a backend sb_secret_ key or legacy service_role key; publishable keys cannot access feedback functions.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise AdapterUnavailable("SUPABASE_URL must be a credential-free HTTPS project origin.")
    return SupabaseSettings(url=url.rstrip('/'), service_role_key=SecretStr(key), ca_file=Path(ca) if ca else None)


class SupabaseStore:
    provider = "supabase:live"

    def __init__(self, settings: SupabaseSettings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=False,
            verify=ssl.create_default_context(cafile=str(settings.ca_file)) if settings.ca_file else True)

    def _rpc(self, name: str, parameters: dict[str, Any]) -> Any:
        if name not in RPCS:
            raise ValueError("RPC is not allowlisted")
        key = self._settings.service_role_key.get_secret_value()
        # Legacy service_role JWTs use Authorization; new sb_secret keys use apikey
        # without pretending the opaque secret is a JWT.
        headers = {"apikey": key, "Content-Type": "application/json"}
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"
        try:
            result = self._client.post(f"{self._settings.url}/rest/v1/rpc/linemedic_{name}",
                                       headers=headers, json=parameters)
            result.raise_for_status()
            return result.json()
        except Exception:
            raise AdapterUnavailable("Supabase operation unavailable; check configuration and the schema migration.") from None

    def seed(self, data: Dataset) -> None:
        data = Dataset.model_validate_json(data.model_dump_json())
        # Only the immutable factory baseline is seeded, never a stale snapshot
        # that could overwrite newly persisted feedback.
        from src.adapters.graph_common import node_records

        tuple(node_records(data))
        payload = data.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        result = self._rpc("seed", {"p_dataset": payload, "p_digest": digest})
        if result != {"baseline_work_orders": len(data.work_orders)}:
            raise AdapterUnavailable("Supabase did not acknowledge the complete baseline.")

    def snapshot(self) -> Dataset:
        result = self._rpc("snapshot", {})
        try:
            return Dataset.model_validate_json(json.dumps(result))
        except Exception:
            raise AdapterUnavailable("Supabase snapshot is absent or fails synthetic integrity checks.") from None

    def retrieve(self, request: DiagnosisRequest) -> tuple[Evidence, ...]:
        result = self._rpc("exact_sources", {"p_alarm_code": request.alarm_code, "p_equipment_id": request.equipment_id})
        return TypeAdapter(tuple[Evidence, ...]).validate_json(json.dumps(result))

    def fix_history(self, equipment_id: str, fix_id: str) -> FixHistory:
        result = self._rpc("history", {"p_equipment_id": equipment_id, "p_fix_id": fix_id})
        orders = TypeAdapter(tuple[WorkOrder, ...]).validate_json(json.dumps(result))
        return FixHistory(equipment_id=equipment_id, fix_id=fix_id, work_orders=orders, rate_basis="supabase_work_orders")

    def record_feedback(self, event: FeedbackEvent) -> FeedbackReceipt:
        event = validate_feedback(self.snapshot(), event)
        result = FeedbackReceipt.model_validate_json(json.dumps(self._rpc("record_feedback", {"p_event": event.model_dump(mode="json")})))
        if result.event != event:
            raise AdapterUnavailable("Supabase feedback acknowledgement conflicts with the submitted event.")
        return result

    def pending_feedback(self) -> tuple[FeedbackReceipt, ...]:
        result = TypeAdapter(tuple[FeedbackReceipt, ...]).validate_json(json.dumps(self._rpc("pending_feedback", {})))
        if any(r.sync_state != "pending" for r in result) or len({r.event.event_id for r in result}) != len(result):
            raise AdapterUnavailable("Supabase returned invalid outbox state.")
        return result

    def acknowledge_sync(self, event_id: UUID, success: bool) -> FeedbackReceipt:
        result = FeedbackReceipt.model_validate_json(json.dumps(self._rpc("ack_sync", {"p_event_id": str(event_id), "p_success": success})))
        if result.event.event_id != event_id or (success and result.sync_state != "synced"):
            raise AdapterUnavailable("Supabase returned an invalid graph-sync acknowledgement.")
        return result

    def close(self) -> None:
        self._client.close()
