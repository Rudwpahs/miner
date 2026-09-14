from pathlib import Path

import pytest

from basketball_miner.distill_v3.audit import apply_concept_action, validate_promotion
from basketball_miner.distill_v3.models import AuditPromotion, SemanticResult
from basketball_miner.distill_v3.staging import (
    read_jsonl,
    write_immutable_json,
    write_immutable_jsonl,
)


def judge_result() -> SemanticResult:
    return SemanticResult(
        candidate_id="CAND-0123456789abcdef",
        stage="JUDGE",
        decision="CONFIRM",
        reason_code="source-supported",
        knowledge_unit_id="KU-TEST-001",
        concept_id="CONCEPT-0123456789ab",
        concept_action="CREATE",
    )


def test_immutable_file_allows_identical_retry_but_refuses_changed_overwrite(tmp_path: Path):
    path = tmp_path / "run.json"
    write_immutable_json(path, {"status": "COMPLETE"})
    original = path.read_bytes()
    write_immutable_json(path, {"status": "COMPLETE"})
    assert path.read_bytes() == original
    with pytest.raises(FileExistsError):
        write_immutable_json(path, {"status": "FAILED"})


def test_jsonl_round_trip_is_canonical(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    rows = [{"b": 2, "a": 1}, {"candidate_id": "CAND-0123456789abcdef"}]
    write_immutable_jsonl(path, rows)
    assert read_jsonl(path) == rows
    assert path.read_bytes().endswith(b"\n")


def test_deep_proposal_cannot_promote_without_judge():
    result = SemanticResult(
        candidate_id="CAND-0123456789abcdef",
        stage="DEEP",
        decision="PROPOSE_ACCEPT",
        reason_code="supported",
        knowledge_unit_id="KU-TEST-001",
    )
    with pytest.raises(ValueError, match="Judge CONFIRM"):
        validate_promotion(
            result,
            knowledge_unit_id="KU-TEST-001",
            concept_id="CONCEPT-0123456789ab",
            concept_action="CREATE",
            canonical_date="2026-09-14",
        )


def test_judge_confirmation_promotes_matching_identifiers():
    promotion = validate_promotion(
        judge_result(),
        knowledge_unit_id="KU-TEST-001",
        concept_id="CONCEPT-0123456789ab",
        concept_action="CREATE",
        canonical_date="2026-09-14",
    )
    assert promotion.judge_decision == "CONFIRM"


def make_promotion(action: str, ku: str = "KU-TEST-001") -> AuditPromotion:
    return AuditPromotion(
        candidate_id="CAND-0123456789abcdef",
        knowledge_unit_id=ku,
        concept_id="CONCEPT-0123456789ab",
        judge_decision="CONFIRM",
        concept_action=action,
        canonical_date="2026-09-14",
    )


def test_create_requires_no_existing_concept():
    concept = apply_concept_action(None, make_promotion("CREATE"))
    assert concept["primary_knowledge_unit_id"] == "KU-TEST-001"
    with pytest.raises(ValueError, match="already exists"):
        apply_concept_action(concept, make_promotion("CREATE", "KU-TEST-002"))


@pytest.mark.parametrize(
    ("action", "field"),
    [
        ("SUPPORT", "supporting_knowledge_unit_ids"),
        ("REFINE", "refinement_knowledge_unit_ids"),
        ("CONTRADICT", "contradicting_knowledge_unit_ids"),
    ],
)
def test_noncreate_actions_require_existing_matching_concept(action: str, field: str):
    with pytest.raises(ValueError, match="existing concept"):
        apply_concept_action(None, make_promotion(action, "KU-TEST-002"))

    concept = apply_concept_action(None, make_promotion("CREATE"))
    updated = apply_concept_action(concept, make_promotion(action, "KU-TEST-002"))
    updated_twice = apply_concept_action(updated, make_promotion(action, "KU-TEST-002"))
    assert updated_twice[field] == ["KU-TEST-002"]
