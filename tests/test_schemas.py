"""Structural guardrails, using explicitly synthetic evidence fixtures only."""

import pytest
from pydantic import ValidationError

from src.schemas import (
    DiagnosisRequest, DiagnosisResponse, Evidence, FixCandidate, OutcomeCounts,
)


@pytest.fixture
def evidence():
    return Evidence(
        source_id="SYN-TEST-MANUAL-001", text="Synthetic test evidence only.",
        source_kind="manual", provider="synthetic_test_fixture",
    )


@pytest.fixture
def candidate(evidence):
    return FixCandidate(
        fix_id="SYN-TEST-FIX-001", cause="Synthetic test cause",
        evidence_source_ids=(evidence.source_id,),
        outcomes=OutcomeCounts(worked=0, did_not_work=0),
        safety_warning="Synthetic test fixture; no real maintenance authorization.",
    )


@pytest.mark.parametrize("payload", [
    {"query": ""}, {"query": "   "}, {"query": 123},
    {"query": "synthetic", "unexpected": True},
    {"query": "synthetic", "data_mode": "production"},
])
def test_invalid_request_is_rejected(payload):
    with pytest.raises(ValidationError):
        DiagnosisRequest(**payload)


@pytest.mark.parametrize("worked,failed,expected", [(0, 0, None), (2, 1, 2 / 3)])
def test_rate_is_computed_and_unknown_without_observations(worked, failed, expected):
    assert OutcomeCounts(worked=worked, did_not_work=failed).success_rate == expected


@pytest.mark.parametrize("payload", [
    {"worked": -1, "did_not_work": 0},
    {"worked": 1, "did_not_work": 0, "success_rate": 0.99},
])
def test_invalid_or_supplied_rate_is_rejected(payload):
    with pytest.raises(ValidationError):
        OutcomeCounts(**payload)


def test_candidate_cannot_cite_missing_evidence(candidate):
    with pytest.raises(ValidationError, match="absent from retrieved evidence"):
        DiagnosisResponse(status="supported", message="Synthetic test", candidates=(candidate,))


def test_duplicate_source_ids_are_rejected(evidence):
    with pytest.raises(ValidationError, match="must be unique"):
        DiagnosisResponse(status="refused", message="Synthetic test", evidence=(evidence, evidence))


def test_supported_response_requires_candidates():
    with pytest.raises(ValidationError, match="require grounded candidates"):
        DiagnosisResponse(status="supported", message="Synthetic test")


def test_refusal_cannot_include_candidates(candidate, evidence):
    with pytest.raises(ValidationError, match="Refusals cannot include"):
        DiagnosisResponse(
            status="refused", message="Synthetic test", candidates=(candidate,), evidence=(evidence,),
        )


def test_grounded_synthetic_response_round_trips(candidate, evidence):
    response = DiagnosisResponse(
        status="supported", message="Synthetic test only", candidates=(candidate,), evidence=(evidence,),
    )
    assert DiagnosisResponse.model_validate_json(response.model_dump_json()) == response


def test_response_has_at_most_three_candidates(candidate, evidence):
    with pytest.raises(ValidationError):
        DiagnosisResponse(
            status="supported", message="Synthetic test", candidates=(candidate,) * 4, evidence=(evidence,),
        )


@pytest.mark.parametrize("field,value", [("text", " "), ("synthetic", False), ("score", float("nan"))])
def test_invalid_evidence_is_rejected(evidence, field, value):
    payload = evidence.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)
