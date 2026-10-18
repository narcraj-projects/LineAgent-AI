"""Common adapter status contract and explicit unavailable failure."""

from typing import Literal

from src.schemas import Contract, NonEmpty


class AdapterStatus(Contract):
    provider: NonEmpty
    state: Literal["unavailable"] = "unavailable"
    reason: NonEmpty = "Provider integration is not implemented in the scaffold."


class AdapterUnavailable(RuntimeError):
    """A service cannot run; callers must refuse or label an actual local fallback."""
