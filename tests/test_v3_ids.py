from basketball_miner.distill_v3.ids import (
    make_batch_id,
    make_concept_id,
    normalize_doi,
    normalized_source_key,
)
from basketball_miner.models import CandidateRecord


def make_candidate(*, stable_id: str, url: str) -> CandidateRecord:
    return CandidateRecord(
        adapter="crossref",
        source_type="academic",
        stable_id=stable_id,
        url=url,
        title="Basketball shooting biomechanics",
        authors=["A. Author"],
        published_at="2026-09-01",
        summary="Release mechanics in basketball shooting.",
        candidate_id="CAND-0123456789abcdef",
        canonical_hash="0" * 64,
        topic_codes=["SHOOTING"],
        relevance_signals=["basketball"],
        provenance="LINKED",
        warnings=[],
        discovered_at="2026-09-14T00:00:00Z",
    )


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1519/R-15944.1") == "10.1519/r-15944.1"
    assert normalize_doi("doi:10.1519/R-15944.1") == "10.1519/r-15944.1"
    assert normalize_doi("not-a-doi") is None


def test_normalized_source_key_prefers_doi():
    candidate = make_candidate(
        stable_id="10.1519/R-15944.1",
        url="https://doi.org/10.1519/R-15944.1",
    )
    assert normalized_source_key(candidate) == "doi:10.1519/r-15944.1"


def test_normalized_source_key_fallback_is_stable_across_tracking_params():
    first = make_candidate(
        stable_id="paper-123",
        url="https://example.org/paper?id=123&utm_source=test",
    )
    second = make_candidate(
        stable_id="paper-123",
        url="https://example.org/paper?id=123",
    )
    assert normalized_source_key(first) == normalized_source_key(second)
    assert normalized_source_key(first).startswith("source:")


def test_batch_id_is_order_independent():
    ids = ["CAND-aaaaaaaaaaaaaaaa", "CAND-bbbbbbbbbbbbbbbb"]
    assert make_batch_id("TRIAGE", ids) == make_batch_id("TRIAGE", list(reversed(ids)))


def test_concept_id_normalizes_topic_order():
    assert make_concept_id(["SHOOTING", "VISION"], "target visibility") == make_concept_id(
        ["vision", "shooting"], "TARGET VISIBILITY"
    )
