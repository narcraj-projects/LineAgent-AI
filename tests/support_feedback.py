"""Explicit in-memory test doubles. These do not establish Supabase persistence."""

from src.agent_models import Explanation
from src.feedback_models import FeedbackReceipt, validate_feedback
from src.graph_models import FixHistory
from src.retrieval.corpus import build_corpus
from src.schemas import Evidence


class EchoExplanation:
    provider = "test-double:echo-no-live-call"
    model = "test-double"

    def explain(self, facts, evidence):
        return Explanation(facts=facts)


class MemoryStore:
    provider = "test-double:memory-not-supabase"

    def __init__(self, baseline, state=None):
        self.baseline = baseline
        self.state = state if state is not None else {}
        self.fail_write = False
        self.fail_ack = False
        self.commit_then_raise = False

    def seed(self, data):
        assert data == self.baseline

    def snapshot(self):
        orders = {w.work_order_id: w for w in self.baseline.work_orders}
        for receipt in self.state.values():
            orders[receipt.event.work_order.work_order_id] = receipt.event.work_order
        return self.baseline.model_copy(update={"work_orders": tuple(orders[k] for k in sorted(orders))})

    def fix_history(self, equipment_id, fix_id):
        return FixHistory(equipment_id=equipment_id, fix_id=fix_id, work_orders=tuple(
            w for w in self.snapshot().work_orders if w.equipment_id == equipment_id and w.fix_id == fix_id))

    def retrieve(self, request):
        return tuple(Evidence(source_id=d.source_id, source_kind=d.source_kind, text=d.text, provider=self.provider)
            for d in build_corpus(self.snapshot()) if (not request.equipment_id or request.equipment_id in d.equipment_ids)
            and (not request.alarm_code or request.alarm_code in d.alarm_codes))

    def record_feedback(self, event):
        if self.fail_write:
            raise RuntimeError("test failure before persistence")
        validate_feedback(self.snapshot(), event)
        existing = self.state.get(event.event_id)
        if existing and existing.event != event:
            raise ValueError("test idempotency conflict")
        if existing is None:
            self.state[event.event_id] = FeedbackReceipt(event=event, sync_state="pending", attempts=0)
        if self.commit_then_raise:
            raise RuntimeError("test ambiguous network acknowledgement")
        return self.state[event.event_id]

    def pending_feedback(self):
        return tuple(r for r in self.state.values() if r.sync_state == "pending")

    def acknowledge_sync(self, event_id, success):
        if self.fail_ack:
            raise RuntimeError("test outbox acknowledgement unavailable")
        receipt = self.state[event_id]
        if receipt.sync_state == "pending":
            receipt = receipt.model_copy(update={"sync_state": "synced" if success else "pending", "attempts": receipt.attempts + 1})
            self.state[event_id] = receipt
        return receipt

    def close(self):
        pass
