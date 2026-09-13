import pytest
from pydantic import ValidationError

from basketball_miner.distill_v3.models import AuditPromotion, BatchRecord, SemanticResult


def test_triage_cannot_confirm():
    with pytest.raises(ValidationError):
        SemanticResult(
            candidate_id="CAND-0123456789abcdef",
            stage="TRIAGE",
            decision="CONFIRM",
            reason_code="illegal",
        )


def test_review_proposal_is_legal_but_not_canonical():
    result = SemanticResult(
        candidate_id="CAND-0123456789abcdef",
        stage="REVIEW",
        decision="PROPOSE_ACCEPT",
        reason_code="source-resolved",
        knowledge_unit_id="KU-TEST-001",
    )
    assert result.decision == "PROPOSE_ACCEPT"


def test_batch_rejects_empty_candidates():
    with pytest.raises(ValidationError):
        BatchRecord(
            batch_id="V3-TRIAGE-0123456789ab",
            stage="TRIAGE",
            candidate_ids=[],
            priority=50,
            created_at="2026-09-14T00:00:00Z",
            input_fingerprints=[],
            status="PENDING",
        )


def test_batch_rejects_duplicate_candidate_ids():
    candidate_id = "CAND-0123456789abcdef"
    with pytest.raises(ValidationError):
        BatchRecord(
            batch_id="V3-TRIAGE-0123456789ab",
            stage="TRIAGE",
            candidate_ids=[candidate_id, candidate_id],
            priority=50,
            created_at="2026-09-14T00:00:00Z",
            input_fingerprints=["0" * 64, "0" * 64],
            status="PENDING",
        )


def test_batch_rejects_fingerprint_count_mismatch():
    with pytest.raises(ValidationError):
        BatchRecord(
            batch_id="V3-TRIAGE-0123456789ab",
            stage="TRIAGE",
            candidate_ids=["CAND-0123456789abcdef"],
            priority=50,
            created_at="2026-09-14T00:00:00Z",
            input_fingerprints=[],
            status="PENDING",
        )


def test_batch_rejects_stage_mismatch_with_batch_id():
    with pytest.raises(ValidationError, match="batch_id stage"):
        BatchRecord(
            batch_id="V3-DEEP-0123456789ab",
            stage="TRIAGE",
            candidate_ids=["CAND-0123456789abcdef"],
            priority=50,
            created_at="2026-09-14T00:00:00Z",
            input_fingerprints=["0" * 64],
            status="PENDING",
        )


def test_audit_promotion_rejects_nonconfirm():
    with pytest.raises(ValidationError):
        AuditPromotion(
            candidate_id="CAND-0123456789abcdef",
            knowledge_unit_id="KU-TEST-001",
            concept_id="CONCEPT-0123456789ab",
            judge_decision="REVIEW",
            concept_action="CREATE",
            canonical_date="2026-09-14",
        )
