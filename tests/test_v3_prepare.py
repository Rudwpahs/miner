from __future__ import annotations

from copy import deepcopy

from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.models import CandidateStageState
from basketball_miner.distill_v3.prepare import InboxBlob, prepare_shadow
from basketball_miner.models import CandidateRecord


def _candidate(
    suffix: str,
    *,
    stable_id: str | None = None,
    canonical_hash: str | None = None,
    source_type: str = "academic",
) -> CandidateRecord:
    candidate_id = f"CAND-{suffix:0>16}"[-21:]
    digest = canonical_hash or (suffix[-1] * 64)
    source_id = stable_id or f"10.1234/{suffix}"
    return CandidateRecord.model_validate(
        {
            "adapter": "crossref",
            "source_type": source_type,
            "stable_id": source_id,
            "url": f"https://doi.org/{source_id}",
            "title": f"Basketball evidence {suffix}",
            "authors": ["A. Author"],
            "published_at": "2026-09-01",
            "summary": "Basketball evidence summary.",
            "candidate_id": candidate_id,
            "canonical_hash": digest,
            "topic_codes": ["SHOOTING"],
            "relevance_signals": ["basketball"],
            "provenance": "LINKED",
            "warnings": [],
            "discovered_at": "2026-09-14T00:00:00Z",
        }
    )


def _blob(sha_char: str, *rows: CandidateRecord | dict) -> InboxBlob:
    return InboxBlob(
        path=f"ml/coach/miner-data/inbox/2026/09/14/{sha_char}.jsonl",
        sha=sha_char * 40,
        candidates=tuple(rows),
    )


def test_v2_history_is_seeded_before_new_candidates_are_routed():
    accepted = _candidate("0000000000000001")
    review = _candidate("0000000000000002")
    result = prepare_shadow(
        inbox_blobs=[_blob("a", accepted, review)],
        existing_ledger=None,
        accepted_rows=[{"source_candidate_id": accepted.candidate_id, "claim": "accepted"}],
        review_rows=[{"candidate_id": review.candidate_id, "claim": "review"}],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )

    assert result.triage_batches == ()
    assert accepted.candidate_id in result.ledger.terminal_candidate_ids
    assert review.candidate_id in result.ledger.review_candidate_ids


def test_processed_blob_sha_is_skipped_and_rerun_is_idempotent():
    candidate = _candidate("0000000000000003")
    ledger = DistillLedger(processed_blob_shas={"b" * 40})
    skipped = prepare_shadow(
        inbox_blobs=[_blob("b", candidate)],
        existing_ledger=ledger,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )
    assert skipped.triage_batches == ()
    assert skipped.processed_blob_shas == ()

    first = prepare_shadow(
        inbox_blobs=[_blob("c", candidate)],
        existing_ledger=None,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )
    rerun = prepare_shadow(
        inbox_blobs=[_blob("c", candidate)],
        existing_ledger=first.ledger,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T01:00:00Z",
    )
    assert len(first.triage_batches) == 1
    assert rerun.triage_batches == ()
    assert rerun.processed_blob_shas == ()


def test_exact_duplicates_are_terminal_and_only_unresolved_candidates_enter_triage():
    original = _candidate("0000000000000004", stable_id="10.1234/shared")
    source_duplicate = _candidate(
        "0000000000000005",
        stable_id="10.1234/SHARED",
        canonical_hash="5" * 64,
    )
    hash_duplicate = _candidate(
        "0000000000000006",
        stable_id="10.1234/distinct",
        canonical_hash=original.canonical_hash,
    )
    result = prepare_shadow(
        inbox_blobs=[_blob("d", original, source_duplicate, hash_duplicate)],
        existing_ledger=None,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )

    assert len(result.triage_batches) == 1
    assert result.triage_batches[0].candidate_ids == [original.candidate_id]
    assert source_duplicate.candidate_id in result.ledger.terminal_candidate_ids
    assert hash_duplicate.candidate_id in result.ledger.terminal_candidate_ids
    assert result.metrics.exact_duplicates == 2


def test_malformed_row_blocks_entire_blob_without_partial_routing():
    valid = _candidate("0000000000000007")
    malformed = valid.model_dump(mode="json")
    malformed["candidate_id"] = "bad-id"
    result = prepare_shadow(
        inbox_blobs=[_blob("e", valid, malformed)],
        existing_ledger=None,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )

    assert result.triage_batches == ()
    assert result.processed_blob_shas == ()
    assert "e" * 40 not in result.ledger.processed_blob_shas
    assert valid.candidate_id not in result.ledger.candidate_states
    assert result.metrics.invalid_records == 1


def test_triage_batches_are_deterministic_and_capped_at_100():
    rows = []
    for number in range(205):
        suffix = f"{number + 256:016x}"
        rows.append(_candidate(suffix, canonical_hash=f"{number + 1:064x}"))

    forward = prepare_shadow(
        inbox_blobs=[_blob("f", *rows)],
        existing_ledger=None,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
        triage_batch_size=100,
    )
    reverse = prepare_shadow(
        inbox_blobs=[_blob("f", *reversed(rows))],
        existing_ledger=None,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
        triage_batch_size=100,
    )

    assert [batch.model_dump() for batch in forward.triage_batches] == [
        batch.model_dump() for batch in reverse.triage_batches
    ]
    assert [len(batch.candidate_ids) for batch in forward.triage_batches] == [100, 100, 5]


def test_concept_index_uses_accepted_and_only_active_review_without_mutating_inputs():
    accepted_candidate = _candidate("0000000000000008")
    review_candidate = _candidate("0000000000000009")
    accepted_rows = [
        {
            "source_candidate_id": accepted_candidate.candidate_id,
            "knowledge_unit_id": "KU-ACCEPTED",
            "claim": "Accepted shooting claim",
            "topic_codes": ["SHOOTING"],
        }
    ]
    review_rows = [
        {
            "candidate_id": accepted_candidate.candidate_id,
            "knowledge_unit_id": "KU-OLD-REVIEW",
            "claim": "Superseded review claim",
        },
        {
            "candidate_id": review_candidate.candidate_id,
            "knowledge_unit_id": "KU-ACTIVE-REVIEW",
            "claim": "Active review claim",
        },
    ]
    before_accepted = deepcopy(accepted_rows)
    before_review = deepcopy(review_rows)

    result = prepare_shadow(
        inbox_blobs=[],
        existing_ledger=None,
        accepted_rows=accepted_rows,
        review_rows=review_rows,
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )

    assert [row.knowledge_unit_id for row in result.concept_index] == [
        "KU-ACCEPTED",
        "KU-ACTIVE-REVIEW",
    ]
    assert accepted_rows == before_accepted
    assert review_rows == before_review


def test_shadow_metrics_keep_all_hard_release_invariants_zero():
    existing = DistillLedger(
        candidate_states={
            "CAND-1111111111111111": CandidateStageState(
                candidate_id="CAND-1111111111111111",
                source_fingerprint="1" * 64,
                stage="REVIEW",
                status="PENDING",
                updated_at="2026-09-14T00:00:00Z",
            )
        }
    )
    result = prepare_shadow(
        inbox_blobs=[],
        existing_ledger=existing,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )

    assert result.metrics.raw_to_training_bypass == 0
    assert result.metrics.raw_to_canonical_bypass == 0
    assert result.metrics.canonical_without_judge == 0
    assert result.metrics.simultaneous_valid_lease_conflicts == 0
    assert result.metrics.audit_duplicate_leakage == 0
