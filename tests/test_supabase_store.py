"""Offline HTTP contracts; never claim these mocks exercised hosted Supabase."""

import json
from pathlib import Path

import httpx
from pydantic import SecretStr
import pytest

from src.adapters.base import AdapterUnavailable
from src.adapters.supabase_store import SupabaseSettings, SupabaseStore, load_supabase_settings
from src.feedback_models import FeedbackReceipt, new_feedback
from src.retrieval.corpus import load_dataset


@pytest.mark.parametrize("key,has_authorization", [("sb_secret_fake-test-value", False), ("legacy-fake-jwt", True)])
def test_keys_stay_in_server_headers_and_rpc_arguments_are_typed(key, has_authorization):
    data = load_dataset(Path("data"))

    def handler(request):
        assert request.url.path == "/rest/v1/rpc/linemedic_snapshot"
        assert request.headers["apikey"] == key
        assert ("authorization" in request.headers) == has_authorization
        assert key not in str(request.url) and key not in request.content.decode()
        assert json.loads(request.content) == {}
        return httpx.Response(200, json=data.model_dump(mode="json"))

    store = SupabaseStore(SupabaseSettings(url="https://synthetic-test.invalid", service_role_key=SecretStr(key)),
        httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        assert store.snapshot() == data
        assert key not in repr(store._settings)
        with pytest.raises(ValueError):
            store._rpc("arbitrary_sql", {})
    finally:
        store.close()


def test_provider_errors_are_redacted():
    def handler(request):
        return httpx.Response(403, json={"message": "private-key-never-print"})

    store = SupabaseStore(SupabaseSettings(url="https://synthetic-test.invalid", service_role_key=SecretStr("fake-test-key")),
        httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(AdapterUnavailable) as caught:
            store.snapshot()
        assert "private-key" not in str(caught.value)
    finally:
        store.close()


def test_public_key_fails_configuration_before_network(tmp_path, monkeypatch):
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text("SUPABASE_URL=https://synthetic-test.invalid\nSUPABASE_SERVICE_ROLE_KEY=sb_publishable_fake_test\n", encoding="utf-8")
    with pytest.raises(AdapterUnavailable, match="backend sb_secret_") as caught:
        load_supabase_settings(path)
    assert "sb_publishable_fake_test" not in str(caught.value)


@pytest.mark.parametrize("url", ["http://project.invalid", "https://user:secret@project.invalid", "https://project.invalid/rest/v1", "https://project.invalid?key=secret"])
def test_configuration_rejects_unsafe_url_without_echoing_values(tmp_path, monkeypatch, url):
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(f"SUPABASE_URL={url}\nSUPABASE_SERVICE_ROLE_KEY=fake-secret\n", encoding="utf-8")
    with pytest.raises(AdapterUnavailable) as caught:
        load_supabase_settings(path)
    assert "secret" not in str(caught.value)


def test_mismatched_feedback_acknowledgement_is_rejected():
    data = load_dataset(Path("data"))
    event = new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", "worked")
    other = new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", "worked")

    def handler(request):
        if request.url.path.endswith("snapshot"):
            return httpx.Response(200, json=data.model_dump(mode="json"))
        return httpx.Response(200, json=FeedbackReceipt(event=other, sync_state="pending", attempts=0).model_dump(mode="json"))

    store = SupabaseStore(SupabaseSettings(url="https://synthetic-test.invalid", service_role_key=SecretStr("fake-test-key")),
        httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(AdapterUnavailable):
            store.record_feedback(event)
    finally:
        store.close()
