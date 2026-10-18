"""Execute real migration/RLS/transaction logic on isolated local PostgreSQL.

HTTP transport is mocked; this does NOT verify a hosted Supabase connection.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.adapters.base import AdapterUnavailable
from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.application import Application
from src.feedback import FeedbackService
from src.feedback_models import new_feedback
from src.retrieval.corpus import load_dataset
from src.schemas import DiagnosisRequest
from tests.postgres_rpc import execute_sql, literal, store_for_container
from tests.support_feedback import EchoExplanation

CONTAINER = os.environ.get("LINEMEDIC_TEST_POSTGRES_CONTAINER")
pytestmark = pytest.mark.skipif(not CONTAINER, reason="Local PostgreSQL transaction tests not opted in; set LINEMEDIC_TEST_POSTGRES_CONTAINER to an isolated linemedic-prompt5-tests-* container.")


@pytest.fixture
def pg():
    execute_sql(CONTAINER, "TRUNCATE public.linemedic_outbox,public.linemedic_feedback,public.linemedic_work_orders,public.linemedic_catalog", "linemedic_test")
    data = load_dataset(Path("data"))
    store = store_for_container(CONTAINER)
    store.seed(data)
    yield data, store
    store.close()


def make_event(data):
    return new_feedback(data, "SYN-ALM-001", "SYN-FIX-001", "SYN-ACTOR-002", "did_not_work")


def test_real_sql_seed_idempotency_history_and_exact_sources(pg):
    data, store = pg
    store.seed(data)
    assert store.snapshot() == data
    sources = store.retrieve(DiagnosisRequest(query="lookup", alarm_code="SYN-ALM-001", equipment_id="SYN-EQ-001"))
    assert {s.source_id for s in sources} >= {"SYN-SEC-001", "SYN-WO-001", "SYN-WO-002"}
    assert store.fix_history("SYN-EQ-001", "SYN-FIX-001").outcomes.success_rate == 1
    assert store.retrieve(DiagnosisRequest(query="lookup", alarm_code="' OR 1=1 --")) == ()


def test_feedback_survives_actual_fresh_python_process_and_rerates(pg):
    data, store = pg
    event = make_event(data)
    assert store.record_feedback(event).sync_state == "pending"
    store.close()
    script = """
import json,sys
from pathlib import Path
from tests.postgres_rpc import store_for_container
from tests.support_feedback import EchoExplanation
from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.application import Application
from src.retrieval.corpus import load_dataset
from src.schemas import DiagnosisRequest
app=Application(load_dataset(Path('data')),store_for_container(sys.argv[1]),NetworkXGraphAdapter(),EchoExplanation())
app.start()
result=app.diagnose(DiagnosisRequest(query='History SYN-ALM-001'))
data,pending=app.report()
print(json.dumps({'status':result.response.status,'worked':result.response.candidates[0].outcomes.worked,
 'failed':result.response.candidates[0].outcomes.did_not_work,'rate':result.response.candidates[0].outcomes.success_rate,
 'count':len(data.work_orders),'pending':len(pending),'sources':result.response.candidates[0].evidence_source_ids}))
app.close()
"""
    clean = {k: v for k, v in os.environ.items() if not any(token in k for token in ("SUPABASE", "FIREWORKS", "NEO4J", "PINECONE"))}
    result = subprocess.run([sys.executable, "-c", script, CONTAINER], capture_output=True, text=True, env=clean, timeout=45)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == "supported"
    assert observed["count"] == 51 and observed["worked"] == 2 and observed["failed"] == 1
    assert observed["rate"] == 2/3 and observed["pending"] == 0
    assert event.work_order.work_order_id in observed["sources"]
    Path("evals/results").mkdir(exist_ok=True)
    Path("evals/results/postgres-restart-observed.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")


def test_concurrent_duplicate_events_commit_once(pg):
    data, store = pg
    event = make_event(data)

    def write(_):
        client = store_for_container(CONTAINER)
        try:
            return client.record_feedback(event)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(write, range(4)))
    assert all(r.event == event for r in receipts)
    assert len(store.snapshot().work_orders) == 51
    assert len(store.pending_feedback()) == 1


def test_outbox_failure_rolls_back_feedback_and_work_order(pg):
    data, store = pg
    execute_sql(CONTAINER, """
CREATE FUNCTION public.linemedic_test_fail_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'intentional transaction rollback test'; END $$;
CREATE TRIGGER linemedic_test_outbox_failure BEFORE INSERT ON public.linemedic_outbox
FOR EACH ROW EXECUTE FUNCTION public.linemedic_test_fail_outbox()
""", "linemedic_test")
    try:
        with pytest.raises(AdapterUnavailable):
            store.record_feedback(make_event(data))
        assert len(store.snapshot().work_orders) == 50
        assert store.pending_feedback() == ()
        assert execute_sql(CONTAINER, "SELECT count(*) FROM public.linemedic_feedback") == "0"
    finally:
        execute_sql(CONTAINER, "DROP TRIGGER linemedic_test_outbox_failure ON public.linemedic_outbox; DROP FUNCTION public.linemedic_test_fail_outbox()", "linemedic_test")


def test_server_rejects_invalid_actor_even_when_client_validation_bypassed(pg):
    data, store = pg
    event = make_event(data).model_dump(mode="json")
    event["work_order"]["actor_id"] = "SYN-ACTOR-001"
    with pytest.raises(RuntimeError):
        execute_sql(CONTAINER, f"SELECT public.linemedic_record_feedback({literal(event)})")
    assert len(store.snapshot().work_orders) == 50


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_browser_table_or_rpc_access(pg, role):
    with pytest.raises(RuntimeError):
        execute_sql(CONTAINER, "SELECT * FROM public.linemedic_work_orders", role)
    with pytest.raises(RuntimeError):
        execute_sql(CONTAINER, "SELECT public.linemedic_snapshot()", role)


def test_pending_graph_failure_recovers_after_new_application(pg):
    data, store = pg
    graph = NetworkXGraphAdapter()
    graph.seed(data)
    graph.upsert_work_order = lambda _: (_ for _ in ()).throw(RuntimeError("test graph outage"))
    event = make_event(data)
    assert FeedbackService(store, graph).submit(event).sync_state == "pending"
    fresh = Application(data, store_for_container(CONTAINER), NetworkXGraphAdapter(), EchoExplanation())
    fresh.start()
    try:
        assert not fresh.store.pending_feedback()
        assert fresh.graph.fix_history("SYN-EQ-001", "SYN-FIX-001").outcomes.success_rate == 2/3
        assert fresh.store.record_feedback(event).sync_state == "synced"
    finally:
        fresh.close()
        graph.close()
