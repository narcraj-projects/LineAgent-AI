"""Supabase-first append-only feedback with idempotent graph retry."""

from src.adapters.contracts import GraphAdapter
from src.adapters.feedback_contracts import FeedbackStore
from src.feedback_models import FeedbackEvent, FeedbackReceipt, validate_feedback


class FeedbackService:
    def __init__(self, store: FeedbackStore, graph: GraphAdapter) -> None:
        self.store, self.graph = store, graph

    def submit(self, event: FeedbackEvent) -> FeedbackReceipt:
        event = validate_feedback(self.store.snapshot(), event)
        # No graph mutation is allowed before the atomic persistence call.
        receipt = self.store.record_feedback(event)
        if receipt.sync_state == "synced":
            return receipt
        return self._sync(receipt)

    def _sync(self, receipt: FeedbackReceipt) -> FeedbackReceipt:
        try:
            self.graph.upsert_work_order(receipt.event.work_order)
            expected = self.store.fix_history(receipt.event.work_order.equipment_id, receipt.event.work_order.fix_id)
            actual = self.graph.fix_history(expected.equipment_id, expected.fix_id)
            if actual.work_orders != expected.work_orders:
                raise ValueError("Graph history disagrees with Supabase SQL")
        except Exception:
            # Even if this acknowledgement fails, the original pending record
            # remains durable and can be retried after a process restart.
            return self.store.acknowledge_sync(receipt.event.event_id, False)
        return self.store.acknowledge_sync(receipt.event.event_id, True)

    def retry_pending(self) -> tuple[FeedbackReceipt, ...]:
        return tuple(self._sync(receipt) for receipt in self.store.pending_feedback())
