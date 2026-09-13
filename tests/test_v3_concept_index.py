import json
from copy import deepcopy
from pathlib import Path

from basketball_miner.distill_v3.concept_index import (
    build_index,
    claim_signature,
    shortlist,
)

FIXTURES = Path(__file__).parent / "fixtures" / "v3"


def load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_claim_signature_is_case_order_and_noise_stable():
    assert claim_signature("Target, VISIBILITY and target control!") == "control target visibility"


def test_build_index_does_not_mutate_v2_rows():
    accepted = load_jsonl("v2_accepted.jsonl")
    review = load_jsonl("v2_review.jsonl")
    before_accepted = deepcopy(accepted)
    before_review = deepcopy(review)
    index = build_index(accepted, review)
    assert index
    assert accepted == before_accepted
    assert review == before_review


def test_exact_source_outranks_topic_only():
    index = build_index(load_jsonl("v2_accepted.jsonl"), load_jsonl("v2_review.jsonl"))
    rows = shortlist(
        index,
        source_id="10.1519/r-15944.1",
        topic_codes=["PLAYER_PROFILE", "POSITION"],
        claim="elite positional player profile",
        limit=5,
    )
    assert rows[0].normalized_source_id == "10.1519/r-15944.1"
    assert rows[0].knowledge_unit_id == "KU-PROFILE-POSITION-OSTOJIC-2006-001"


def test_shortlist_omits_zero_score_rows_and_respects_limit():
    index = build_index(load_jsonl("v2_accepted.jsonl"), load_jsonl("v2_review.jsonl"))
    rows = shortlist(
        index,
        source_id=None,
        topic_codes=["VISION"],
        claim="visual target",
        limit=1,
    )
    assert len(rows) == 1
    assert "VISION" in rows[0].topic_codes


def test_review_rows_are_indexed_without_inventing_accepted_status():
    index = build_index(load_jsonl("v2_accepted.jsonl"), load_jsonl("v2_review.jsonl"))
    review = [row for row in index if row.status == "REVIEW"]
    assert len(review) == 2
    assert all(row.knowledge_unit_id.startswith("CAND-") for row in review)
