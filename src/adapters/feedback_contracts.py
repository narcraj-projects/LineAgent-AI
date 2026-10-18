"""Persistence must precede graph synchronization; no local production substitute."""

from typing import Protocol
from uuid import UUID

from src.adapters.diagnosis_contracts import SQLEvidenceAdapter
from src.data_models import Dataset
from src.feedback_models import FeedbackEvent, FeedbackReceipt


class FeedbackStore(SQLEvidenceAdapter, Protocol):
    def seed(self, data: Dataset) -> None: ...

    def snapshot(self) -> Dataset: ...

    def record_feedback(self, event: FeedbackEvent) -> FeedbackReceipt: ...

    def pending_feedback(self) -> tuple[FeedbackReceipt, ...]: ...

    def acknowledge_sync(self, event_id: UUID, success: bool) -> FeedbackReceipt: ...

    def close(self) -> None: ...
