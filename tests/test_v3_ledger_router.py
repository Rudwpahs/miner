import json
from pathlib import Path

from basketball_miner.distill_v3.ledger import (
    DistillLedger,
    ledger_payload,
    seed_from_v2_history,
)
from basketball_miner.distill_v3.router import route_candidate
from basketball_miner.models import CandidateRecord

FIXTURES = Path(__file__).parent / "fixtures" / "v3"


def load_candidates() -> list[CandidateRecord]:
    return [
        CandidateRecord.model_validate(json.loads(line))
        for line in (FIXTURES / "inbox.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_new_candidate_routes_to_triage():
    candidate = load_candidates()[0]
    result = route_candidate(candidate, DistillLedger())
    assert (result.route, result.reason_code) == ("TRIAGE", "NEW_SEMANTIC_CANDIDATE")


def test_same_normalized_source_is_duplicate_after_first_route_recorded():
    candidate_a, candidate_same_source, _ = load_candidates()
    ledger = DistillLedger()
    first = route_candidate(candidate_a, ledger)
    ledger.record_route(candidate_a, first)
    second = route_candidate(candidate_same_source, ledger)
    assert (second.route, second.reason_code) == ("DUPLICATE", "EXACT_SOURCE")


def test_processed_blob_is_idempotent():
    ledger = DistillLedger()
    blob_sha = "a" * 40
    assert ledger.mark_blob_processed(blob_sha) is True
    assert ledger.mark_blob_processed(blob_sha) is False


def test_v2_manifest_seeds_blob_and_keeps_current_review_nonterminal():
    manifest = json.loads((FIXTURES / "v2_manifest.json").read_text(encoding="utf-8"))
    ledger = seed_from_v2_history(
        DistillLedger(),
        accepted_rows=[],
        review_rows=[{"candidate_id": "CAND-1111111111111111"}],
        manifests=[manifest],
    )
    assert "a" * 40 in ledger.processed_blob_shas
    assert "CAND-1111111111111111" in ledger.review_candidate_ids
    assert "CAND-1111111111111111" not in ledger.terminal_candidate_ids
    assert "CAND-2222222222222222" in ledger.terminal_candidate_ids


def test_accepted_history_wins_over_review_history():
    candidate_id = "CAND-3333333333333333"
    ledger = seed_from_v2_history(
        DistillLedger(),
        accepted_rows=[{"source_candidate_id": candidate_id}],
        review_rows=[{"candidate_id": candidate_id}],
        manifests=[],
    )
    assert candidate_id in ledger.terminal_candidate_ids
    assert candidate_id not in ledger.review_candidate_ids


def test_active_review_candidate_is_not_sent_back_to_triage():
    candidate = load_candidates()[2]
    ledger = DistillLedger(review_candidate_ids={candidate.candidate_id})
    result = route_candidate(candidate, ledger)
    assert (result.route, result.reason_code) == ("TERMINAL_PROCESSED", "ACTIVE_REVIEW")


def test_ledger_payload_is_deterministic_and_json_safe():
    ledger = DistillLedger(
        processed_blob_shas={"b" * 40, "a" * 40},
        terminal_candidate_ids={"CAND-bbbbbbbbbbbbbbbb", "CAND-aaaaaaaaaaaaaaaa"},
    )
    payload = ledger_payload(ledger)
    assert payload["processed_blob_shas"] == ["a" * 40, "b" * 40]
    assert payload["terminal_candidate_ids"] == [
        "CAND-aaaaaaaaaaaaaaaa",
        "CAND-bbbbbbbbbbbbbbbb",
    ]
