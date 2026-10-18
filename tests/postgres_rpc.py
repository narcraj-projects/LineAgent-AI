"""Actual local PostgreSQL functions behind MOCK HTTP, never labeled Supabase live."""

import json
import subprocess

import httpx
from pydantic import SecretStr

from src.adapters.supabase_store import RPCS, SupabaseSettings, SupabaseStore


def execute_sql(container, statement, role="service_role"):
    if not container.startswith("linemedic-prompt5-tests-") or role not in ("service_role", "anon", "authenticated", "linemedic_test"):
        raise ValueError("Only the isolated milestone test database is allowed")
    result = subprocess.run(["docker", "exec", "-i", container, "psql", "-X", "-q", "-At",
        "-v", "ON_ERROR_STOP=1", "-U", "linemedic_test", "-d", "linemedic_test"],
        input=f"SET ROLE {role};\n{statement};\n", capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def store_for_container(container):
    arguments = {
        "seed": ("p_dataset", "p_digest"), "snapshot": (), "history": ("p_equipment_id", "p_fix_id"),
        "exact_sources": ("p_alarm_code", "p_equipment_id"), "record_feedback": ("p_event",),
        "pending_feedback": (), "ack_sync": ("p_event_id", "p_success"),
    }

    def handler(request):
        name = request.url.path.rsplit("linemedic_", 1)[-1]
        params = json.loads(request.content)
        assert name in RPCS and set(params) == set(arguments[name])
        expression = ','.join(literal(params[key]) for key in arguments[name])
        try:
            text = execute_sql(container, f"SELECT public.linemedic_{name}({expression})")
            return httpx.Response(200, json=json.loads(text) if text else None)
        except RuntimeError:
            return httpx.Response(400, json={"message": "local PostgreSQL rejected the test transaction"})

    return SupabaseStore(SupabaseSettings(url="https://local-postgres-mock-http.invalid", service_role_key=SecretStr("fake-test-service-key")),
        client=httpx.Client(transport=httpx.MockTransport(handler)))
