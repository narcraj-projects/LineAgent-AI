"""Scaffold remains synthetic-only and refuses without a verified pipeline."""

import pytest
from pydantic import ValidationError

from src.adapters.base import AdapterUnavailable
from src.adapters.unavailable import UnavailableRetrievalAdapter
from src.config import Settings
from src.safety import scaffold_refusal
from src.schemas import DiagnosisRequest


def test_scaffold_refuses_without_candidates_or_fabricated_evidence():
    response = scaffold_refusal()
    assert response.status == "refused"
    assert response.synthetic is True
    assert response.candidates == ()
    assert response.evidence == ()
    assert "unavailable" in response.message


def test_settings_cannot_enable_real_data():
    with pytest.raises(ValidationError):
        Settings(data_mode="production")


def test_unavailable_provider_raises_instead_of_faking_results():
    adapter = UnavailableRetrievalAdapter("pinecone")
    assert adapter.status.state == "unavailable"
    with pytest.raises(AdapterUnavailable, match="not implemented"):
        adapter.retrieve(DiagnosisRequest(query="Synthetic test alarm"))
