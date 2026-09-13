from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.materialize import StagingBlob, materialize_staging
from basketball_miner.distill_v3.models import BatchRecord, CandidateStageState


def _ids(count: int, offset: int = 1) -> list[str]:
    return [f"CAND-{number:016x}" for number in range(offset, offset + count)]


def _fps(count: int, offset: int = 1) -> list[str]:
    return [f"{number:064x}" for number in range(offset, offset + count)]


def _batch(stage: str, candidate_ids: list[str], fingerprints: list[str], suffix: str) -> BatchRecord:
    return BatchRecord(
        batch_id=f"V3-{stage}-{suffix}",
        stage=stage,
        candidate_ids=candidate_ids,
        priority=85,
        created_at="2026-09-14T00:00:00Z",
        input_fingerprints=fingerprints,
        status="PENDING",
    )


def _ledger(*batches: BatchRecord) -> DistillLedger:
    states = {}
    for batch in batches:
        for candidate_id, fingerprint in zip(
            batch.candidate_ids,
            batch.input_fingerprints,
            strict=True,
        ):
            states[candidate_id] = CandidateStageState(
                candidate_id=candidate_id,
                source_fingerprint=fingerprint,
                source_type="academic",
                stage=batch.stage,
                status="PENDING",
                batch_id=batch.batch_id,
                updated_at="2026-09-14T00:00:00Z",
            )
    return DistillLedger(candidate_states=states)


def _blob(batch: BatchRecord, decision: str, sha_char: str) -> StagingBlob:
    records = [
        {
            "candidate_id": candidate_id,
            "stage": batch.stage,
            "decision": decision,
            "reason_code": "TEST",
        }
        for candidate_id in batch.candidate_ids
    ]
    return StagingBlob(
        path=f"ml/coach/miner-data/v3/staging/{batch.stage.lower()}/2026/09/14/{sha_char}.jsonl",
        sha=sha_char * 40,
        payload={
            "run_id": f"RUN-{sha_char}",
            "batch_id": batch.batch_id,
            "stage": batch.stage,
            "worker": "GPT-V3",
            "created_at": "2026-09-14T00:10:00Z",
            "input_fingerprints": batch.input_fingerprints,
            "records": records,
        },
    )


def test_deep_destination_batches_are_capped_at_30_with_stable_ids():
    ids = _ids(61)
    fps = _fps(61)
    source = _batch("TRIAGE", ids, fps, "111111111111")
    result = materialize_staging(
        staging_blobs=[_blob(source, "DEEP_PENDING", "a")],
        source_batches={source.batch_id: source},
        existing_ledger=_ledger(source),
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
    )
    deep = [batch for batch in result.next_batches if batch.stage == "DEEP"]
    assert [len(batch.candidate_ids) for batch in deep] == [30, 30, 1]
    assert len({batch.batch_id for batch in deep}) == 3


def test_review_destination_batches_are_capped_at_20():
    ids = _ids(41, offset=200)
    fps = _fps(41, offset=200)
    source = _batch("DEEP", ids, fps, "222222222222")
    result = materialize_staging(
        staging_blobs=[_blob(source, "REVIEW", "b")],
        source_batches={source.batch_id: source},
        existing_ledger=_ledger(source),
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
    )
    review = [batch for batch in result.next_batches if batch.stage == "REVIEW"]
    assert [len(batch.candidate_ids) for batch in review] == [20, 20, 1]


def test_staging_discovery_order_does_not_change_output():
    triage = _batch("TRIAGE", _ids(4, 400), _fps(4, 400), "aaaaaaaaaaaa")
    deep = _batch("DEEP", _ids(4, 500), _fps(4, 500), "bbbbbbbbbbbb")
    blobs = [
        _blob(triage, "DEEP_PENDING", "c"),
        _blob(deep, "PROPOSE_ACCEPT", "d"),
    ]
    source_batches = {triage.batch_id: triage, deep.batch_id: deep}
    ledger = _ledger(triage, deep)
    forward = materialize_staging(
        staging_blobs=blobs,
        source_batches=source_batches,
        existing_ledger=ledger,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
    )
    reverse = materialize_staging(
        staging_blobs=list(reversed(blobs)),
        source_batches=source_batches,
        existing_ledger=ledger,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
    )
    assert [batch.model_dump(mode="json") for batch in forward.next_batches] == [
        batch.model_dump(mode="json") for batch in reverse.next_batches
    ]
    assert forward.ledger.model_dump(mode="json") == reverse.ledger.model_dump(mode="json")
