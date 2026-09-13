from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.materialize import StagingBlob, materialize_staging
from basketball_miner.distill_v3.models import BatchRecord, CandidateStageState


def _batch(stage: str, candidate_ids: list[str], fingerprints: list[str]) -> BatchRecord:
    suffix = {
        "TRIAGE": "111111111111",
        "DEEP": "222222222222",
        "JUDGE": "333333333333",
        "REVIEW": "444444444444",
        "AUDIT": "555555555555",
    }[stage]
    return BatchRecord(
        batch_id=f"V3-{stage}-{suffix}",
        stage=stage,
        candidate_ids=candidate_ids,
        priority=85,
        created_at="2026-09-14T00:00:00Z",
        input_fingerprints=fingerprints,
        status="PENDING",
    )


def _ledger(batch: BatchRecord, source_type: str = "academic") -> DistillLedger:
    states = {}
    for candidate_id, fingerprint in zip(
        batch.candidate_ids,
        batch.input_fingerprints,
        strict=True,
    ):
        states[candidate_id] = CandidateStageState(
            candidate_id=candidate_id,
            source_fingerprint=fingerprint,
            source_type=source_type,
            stage=batch.stage,
            status="PENDING",
            batch_id=batch.batch_id,
            updated_at="2026-09-14T00:00:00Z",
        )
    return DistillLedger(candidate_states=states)


def _blob(batch: BatchRecord, records: list[dict], sha_char: str = "a") -> StagingBlob:
    return StagingBlob(
        path=f"ml/coach/miner-data/v3/staging/{batch.stage.lower()}/2026/09/14/run.jsonl",
        sha=sha_char * 40,
        payload={
            "run_id": "RUN-20260914-001",
            "batch_id": batch.batch_id,
            "stage": batch.stage,
            "worker": "GPT-V3",
            "created_at": "2026-09-14T00:10:00Z",
            "input_fingerprints": batch.input_fingerprints,
            "records": records,
        },
    )


def _result(batch: BatchRecord, records: list[dict]):
    return materialize_staging(
        staging_blobs=[_blob(batch, records)],
        source_batches={batch.batch_id: batch},
        existing_ledger=_ledger(batch),
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
    )


def test_triage_deep_pending_creates_deep_queue():
    candidate_id = "CAND-1111111111111111"
    batch = _batch("TRIAGE", [candidate_id], ["1" * 64])
    result = _result(
        batch,
        [{
            "candidate_id": candidate_id,
            "stage": "TRIAGE",
            "decision": "DEEP_PENDING",
            "reason_code": "RELEVANT",
        }],
    )
    assert len(result.next_batches) == 1
    assert result.next_batches[0].stage == "DEEP"
    assert result.next_batches[0].candidate_ids == [candidate_id]
    state = result.ledger.candidate_states[candidate_id]
    assert (state.stage, state.status) == ("DEEP", "PENDING")
    assert batch.batch_id in result.ledger.completed_batch_ids
    assert "a" * 40 in result.ledger.processed_staging_shas


def test_triage_reject_and_duplicate_are_terminal():
    ids = ["CAND-1111111111111111", "CAND-2222222222222222"]
    batch = _batch("TRIAGE", ids, ["1" * 64, "2" * 64])
    result = _result(
        batch,
        [
            {"candidate_id": ids[0], "stage": "TRIAGE", "decision": "REJECT", "reason_code": "OFF_TOPIC"},
            {"candidate_id": ids[1], "stage": "TRIAGE", "decision": "DUPLICATE", "reason_code": "SEMANTIC_DUP"},
        ],
    )
    assert result.next_batches == ()
    assert set(ids) <= result.ledger.terminal_candidate_ids


def test_deep_propose_accept_routes_to_judge_and_review_routes_to_review():
    ids = ["CAND-1111111111111111", "CAND-2222222222222222"]
    batch = _batch("DEEP", ids, ["1" * 64, "2" * 64])
    result = _result(
        batch,
        [
            {"candidate_id": ids[0], "stage": "DEEP", "decision": "PROPOSE_ACCEPT", "reason_code": "SUPPORTED"},
            {"candidate_id": ids[1], "stage": "DEEP", "decision": "REVIEW", "reason_code": "AMBIGUOUS"},
        ],
    )
    assert [item.stage for item in result.next_batches] == ["JUDGE", "REVIEW"]


def test_judge_confirm_routes_only_to_shadow_audit():
    candidate_id = "CAND-1111111111111111"
    batch = _batch("JUDGE", [candidate_id], ["1" * 64])
    result = _result(
        batch,
        [{
            "candidate_id": candidate_id,
            "stage": "JUDGE",
            "decision": "CONFIRM",
            "reason_code": "SOURCE_MATCH",
            "knowledge_unit_id": "KU-1",
            "concept_id": "CONCEPT-111111111111",
            "concept_action": "CREATE",
        }],
    )
    assert len(result.next_batches) == 1
    assert result.next_batches[0].stage == "AUDIT"
    assert result.ledger.candidate_states[candidate_id].stage == "AUDIT"


def test_review_repeat_parks_without_immediate_requeue():
    candidate_id = "CAND-1111111111111111"
    batch = _batch("REVIEW", [candidate_id], ["1" * 64])
    result = _result(
        batch,
        [{
            "candidate_id": candidate_id,
            "stage": "REVIEW",
            "decision": "REVIEW",
            "reason_code": "SOURCE_STILL_UNAVAILABLE",
        }],
    )
    assert result.next_batches == ()
    assert candidate_id in result.ledger.parked_review_candidate_ids
    assert candidate_id in result.ledger.review_candidate_ids


def test_shadow_audit_support_completes_without_next_queue():
    candidate_id = "CAND-1111111111111111"
    batch = _batch("AUDIT", [candidate_id], ["1" * 64])
    result = _result(
        batch,
        [{
            "candidate_id": candidate_id,
            "stage": "AUDIT",
            "decision": "SUPPORT",
            "reason_code": "SUPPORTS_CONCEPT",
            "knowledge_unit_id": "KU-1",
            "concept_id": "CONCEPT-111111111111",
            "concept_action": "SUPPORT",
        }],
    )
    assert result.next_batches == ()
    state = result.ledger.candidate_states[candidate_id]
    assert (state.stage, state.status) == ("AUDIT", "COMPLETE")
