"""Append-only synthetic feedback and durable outbox contracts."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from src.data_models import Dataset, WorkOrder
from src.schemas import Contract, NonEmpty, OutcomeCounts


class FeedbackEvent(Contract):
    event_id: UUID
    submitted_at: AwareDatetime
    work_order: WorkOrder

    @model_validator(mode="after")
    def validate_event(self) -> "FeedbackEvent":
        expected = f"SYN-WO-FB-{self.event_id.hex}"
        if self.work_order.work_order_id != expected:
            raise ValueError("Feedback work-order ID must derive from its event ID")
        if self.work_order.date != self.submitted_at.astimezone(timezone.utc).date():
            raise ValueError("Feedback date must match its UTC timestamp")
        if self.work_order.note != f"Synthetic demo feedback: {self.work_order.outcome}.":
            raise ValueError("Only controlled synthetic feedback notes are supported")
        return self


class FeedbackReceipt(Contract):
    event: FeedbackEvent
    sync_state: Literal["pending", "synced"]
    attempts: int = Field(ge=0)


class HistorySummary(Contract):
    equipment_id: NonEmpty
    fix_id: NonEmpty
    outcomes: OutcomeCounts


class SupervisorReport(Contract):
    persisted_work_orders: int = Field(ge=0)
    pending_events: tuple[FeedbackReceipt, ...]
    histories: tuple[HistorySummary, ...]


def validate_feedback(data: Dataset, event: FeedbackEvent) -> FeedbackEvent:
    event = FeedbackEvent.model_validate_json(event.model_dump_json())
    if any(w.work_order_id == event.work_order.work_order_id and w != event.work_order for w in data.work_orders):
        raise ValueError("Feedback event conflicts with an existing work order")
    # Reuse the complete canonical relationship and technician-role validation.
    orders = {w.work_order_id: w for w in data.work_orders}
    orders[event.work_order.work_order_id] = event.work_order
    Dataset.model_validate_json(data.model_copy(update={"work_orders": tuple(orders.values())}).model_dump_json())
    return event


def new_feedback(data: Dataset, alarm_code: str, fix_id: str, actor_id: str,
                 outcome: Literal["worked", "did_not_work"]) -> FeedbackEvent:
    alarm = next(a for a in data.alarms if a.code == alarm_code)
    event_id = uuid4()
    timestamp = datetime.now(timezone.utc)
    order = WorkOrder(work_order_id=f"SYN-WO-FB-{event_id.hex}", equipment_id=alarm.equipment_id,
        alarm_code=alarm_code, fix_id=fix_id, actor_id=actor_id, outcome=outcome,
        note=f"Synthetic demo feedback: {outcome}.", date=timestamp.date())
    return validate_feedback(data, FeedbackEvent(event_id=event_id, submitted_at=timestamp, work_order=order))
