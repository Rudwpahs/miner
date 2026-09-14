from __future__ import annotations

from copy import deepcopy

import pytest

from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.materialize import StagingBlob, materialize_staging
from basketball_miner.distill_v3.models import BatchRecord, CandidateStageState


def _ids(count: int, offset: int = 1) -> list[str]:
    return [f"CAND-{number:016x}" for number in range(offset, offset + count)]


def _fps(count: int, offset: int = 1) -> list[str]:
    return [f"{number:064x}" for number in range(offset, offset + count)]


def _batch(stage: str, count: int = 1, *, offset: int = 1, suffix: str = "abcdefabcdef") -> BatchRecord:
    return BatchRecord(
        batch_id=f"V3-{stage}-{suffix}",
        stage=stage,
        candidate_ids=_ids(count, offset),
        priority=85,
        created_at="2026-09-14T00:00:00Z",
        input_fingerprints=_fps(count, offset),
        status="PENDING",
    )


def _ledger(batch: BatchRecord, source_types: list[str | None] | None = None) -> DistillLedger:
    source_types = source_types or ["academic"] * len(batch.candidate_ids)
    states = {}
    for candidate_id, fingerprint, source_type in zip(
        batch.candidate_ids,
        batch.input_fingerprints,
        source_types,
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


def _record(candidate_id: str, stage: str, decision: str) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_id": candidate_id,
        "stage": stage,
        "decision": decision,
        "reason_code": "CONTRACT_TEST",
    }
    if stage == "AUDIT" and decision in {"CREATE", "SUPPORT", "REFINE", "CONTRADICT"}:
        row.update(
            knowledge_unit_id=f"KU-{candidate_id[-4:]}",
            concept_id=f"CONCEPT-{candidate_id[-12:]}",
            concept_action=decision,
        )
    return row


def _blob(batch: BatchRecord, records: list[dict[str, object]], *, sha_char: str = "a") -> StagingBlob:
    return StagingBlob(
        path=f"ml/coach/miner-data/v3/staging/{batch.stage.lower()}/2026/09/14/run.jsonl",
        sha=sha_char * 40,
        payload={
            "run_id": "RUN-CONTRACT",
            "batch_id": batch.batch_id,
            "stage": batch.stage,
            "worker": "GPT-V3",
            "created_at": "2026-09-14T00:10:00Z",
            "input_fingerprints": batch.input_fingerprints,
            "records": records,
        },
    )


def _materialize(batch: BatchRecord, records: list[dict[str, object]], *, ledger: DistillLedger | None = None):
    return materialize_staging(
        staging_blobs=[_blob(batch, records)],
        source_batches={batch.batch_id: batch},
        existing_ledger=ledger or _ledger(batch),
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
    )


@pytest.mark.parametrize(
    ("stage", "decision", "expected_next", "terminal"),
    [
        ("DEEP", "REJECT", None, True),
        ("JUDGE", "REVIEW", "REVIEW", False),
        ("JUDGE", "REJECT", None, True),
        ("REVIEW", "PROPOSE_ACCEPT", "JUDGE", False),
        ("REVIEW", "REJECT", None, True),
    ],
)
def test_remaining_stage_transitions_are_explicit(stage: str, decision: str, expected_next: str | None, terminal: bool):
    batch = _batch(stage)
    candidate_id = batch.candidate_ids[0]
    result = _materialize(batch, [_record(candidate_id, stage, decision)])

    if expected_next is None:
        assert result.next_batches == ()
    else:
        assert [item.stage for item in result.next_batches] == [expected_next]
    assert (candidate_id in result.ledger.terminal_candidate_ids) is terminal


@pytest.mark.parametrize("decision", ["CREATE", "SUPPORT", "REFINE", "CONTRADICT"])
def test_all_audit_concept_decisions_complete_shadow_only(decision: str):
    batch = _batch("AUDIT")
    candidate_id = batch.candidate_ids[0]
    result = _materialize(batch, [_record(candidate_id, "AUDIT", decision)])

    assert result.next_batches == ()
    state = result.ledger.candidate_states[candidate_id]
    assert (state.stage, state.status) == ("AUDIT", "COMPLETE")


@pytest.mark.parametrize("decision", ["REVIEW", "BLOCKED"])
def test_audit_review_and_blocked_are_parked_without_next_queue(decision: str):
    batch = _batch("AUDIT")
    candidate_id = batch.candidate_ids[0]
    result = _materialize(batch, [_record(candidate_id, "AUDIT", decision)])

    assert result.next_batches == ()
    assert candidate_id in result.ledger.parked_review_candidate_ids


def test_destination_priority_is_recomputed_from_each_preserved_source_type():
    batch = _batch("TRIAGE", count=2)
    candidate_a, candidate_b = batch.candidate_ids
    ledger = _ledger(batch, ["interview", "official"])
    result = _materialize(
        batch,
        [
            _record(candidate_a, "TRIAGE", "DEEP_PENDING"),
            _record(candidate_b, "TRIAGE", "DEEP_PENDING"),
        ],
        ledger=ledger,
    )

    deep = result.next_batches[0]
    assert deep.candidate_ids == [candidate_b, candidate_a]
    assert deep.priority == 93
    assert result.ledger.candidate_states[candidate_a].source_type == "interview"
    assert result.ledger.candidate_states[candidate_b].source_type == "official"


def test_missing_source_type_on_advancing_legacy_state_fails_closed():
    batch = _batch("TRIAGE")
    ledger = _ledger(batch, [None])
    before = deepcopy(ledger.model_dump(mode="json"))

    with pytest.raises(ValueError, match="source_type"):
        _materialize(
            batch,
            [_record(batch.candidate_ids[0], "TRIAGE", "DEEP_PENDING")],
            ledger=ledger,
        )
    assert ledger.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("destination", "source_stage", "decision"),
    [
        ("JUDGE", "DEEP", "PROPOSE_ACCEPT"),
        ("AUDIT", "JUDGE", "CONFIRM"),
    ],
)
def test_judge_and_audit_destination_batches_are_capped_at_30(
    destination: str,
    source_stage: str,
    decision: str,
):
    batch = _batch(source_stage, count=61, offset=100)
    records = [_record(candidate_id, source_stage, decision) for candidate_id in batch.candidate_ids]
    result = _materialize(batch, records)
    batches = [item for item in result.next_batches if item.stage == destination]

    assert [len(item.candidate_ids) for item in batches] == [30, 30, 1]
    assert len({item.batch_id for item in batches}) == 3


def test_unknown_source_batch_fails_atomically():
    batch = _batch("TRIAGE")
    ledger = _ledger(batch)
    before = deepcopy(ledger.model_dump(mode="json"))

    with pytest.raises(ValueError, match="unknown source batch"):
        materialize_staging(
            staging_blobs=[_blob(batch, [_record(batch.candidate_ids[0], "TRIAGE", "DEEP_PENDING")])],
            source_batches={},
            existing_ledger=ledger,
            run_date="2026-09-14",
            created_at="2026-09-14T00:20:00Z",
        )
    assert ledger.model_dump(mode="json") == before


def test_staging_stage_mismatch_fails_atomically():
    batch = _batch("TRIAGE")
    ledger = _ledger(batch)
    before = deepcopy(ledger.model_dump(mode="json"))
    blob = _blob(batch, [_record(batch.candidate_ids[0], "TRIAGE", "DEEP_PENDING")])
    blob.payload["stage"] = "DEEP"

    with pytest.raises(ValueError, match="staging stage"):
        materialize_staging(
            staging_blobs=[blob],
            source_batches={batch.batch_id: batch},
            existing_ledger=ledger,
            run_date="2026-09-14",
            created_at="2026-09-14T00:20:00Z",
        )
    assert ledger.model_dump(mode="json") == before


def test_record_stage_mismatch_fails_atomically():
    batch = _batch("TRIAGE")
    ledger = _ledger(batch)
    before = deepcopy(ledger.model_dump(mode="json"))
    records = [_record(batch.candidate_ids[0], "DEEP", "REVIEW")]

    with pytest.raises(ValueError, match="record stage"):
        _materialize(batch, records, ledger=ledger)
    assert ledger.model_dump(mode="json") == before


def test_illegal_decision_fails_entire_blob():
    batch = _batch("TRIAGE")
    ledger = _ledger(batch)
    before = deepcopy(ledger.model_dump(mode="json"))

    with pytest.raises(ValueError, match="invalid staging record"):
        _materialize(
            batch,
            [_record(batch.candidate_ids[0], "TRIAGE", "CONFIRM")],
            ledger=ledger,
        )
    assert ledger.model_dump(mode="json") == before
