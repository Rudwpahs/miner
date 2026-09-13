from basketball_miner.models import CandidateRecord, RunCounters, SourceRecord


def test_candidate_rejects_non_https_url():
    source = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1234/example",
        url="https://doi.org/10.1234/example",
        title="Basketball shooting biomechanics",
        authors=["A. Author"],
        published_at="2026-09-01",
        summary="Release mechanics in basketball shooting.",
    )
    payload = source.model_dump()
    payload["url"] = "http://example.com/paper"
    try:
        CandidateRecord.model_validate(
            {
                **payload,
                "candidate_id": "CAND-0123456789abcdef",
                "canonical_hash": "0" * 64,
                "topic_codes": ["SHOOTING"],
                "relevance_signals": ["basketball"],
                "provenance": "LINKED",
                "warnings": [],
                "discovered_at": "2026-09-09T00:00:00Z",
            }
        )
    except ValueError:
        return
    raise AssertionError("non-HTTPS candidate URL must be rejected")


def test_candidate_accepts_https_and_forbids_unknown_fields():
    candidate = CandidateRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1234/example",
        url="https://doi.org/10.1234/example",
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
        discovered_at="2026-09-09T00:00:00Z",
    )
    assert candidate.provenance == "LINKED"

    payload = candidate.model_dump(mode="json")
    payload["private_claim"] = "must never be accepted"
    try:
        CandidateRecord.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("unknown fields must be rejected")


def test_existing_candidate_json_remains_backward_compatible():
    candidate = CandidateRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1234/example",
        url="https://doi.org/10.1234/example",
        title="Basketball shooting biomechanics",
        authors=[],
        candidate_id="CAND-0123456789abcdef",
        canonical_hash="0" * 64,
        provenance="LINKED",
        discovered_at="2026-09-09T00:00:00Z",
    )
    assert candidate.supersedes_candidate_id is None


def test_candidate_can_record_corrected_source_lineage():
    candidate = CandidateRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1234/corrected",
        url="https://doi.org/10.1234/corrected",
        title="Basketball shooting biomechanics",
        authors=[],
        candidate_id="CAND-fedcba9876543210",
        canonical_hash="1" * 64,
        provenance="LINKED",
        warnings=["CORRECTED_SOURCE"],
        discovered_at="2026-09-09T00:00:00Z",
        supersedes_candidate_id="CAND-0123456789abcdef",
    )
    assert candidate.supersedes_candidate_id == "CAND-0123456789abcdef"


def test_identity_counters_default_to_zero():
    counters = RunCounters()
    assert counters.identity_verified == 0
    assert counters.identity_variants == 0
    assert counters.identity_ambiguous == 0
    assert counters.identity_mismatches == 0
    assert counters.identity_unverified == 0
    assert counters.identity_collisions == 0
