from basketball_miner.models import CandidateRecord, RunCounters


def _candidate(**overrides) -> CandidateRecord:
    payload = {
        "adapter": "crossref",
        "source_type": "academic",
        "stable_id": "10.1234/example",
        "url": "https://doi.org/10.1234/example",
        "title": "Basketball shooting biomechanics",
        "authors": ["A. Author"],
        "published_at": "2026-09-01",
        "summary": "Release mechanics in basketball shooting.",
        "candidate_id": "CAND-0123456789abcdef",
        "canonical_hash": "0" * 64,
        "topic_codes": ["SHOOTING"],
        "relevance_signals": ["basketball"],
        "provenance": "LINKED",
        "warnings": [],
        "discovered_at": "2026-09-09T00:00:00Z",
    }
    payload.update(overrides)
    return CandidateRecord(**payload)


def test_candidate_rejects_non_https_url():
    payload = _candidate().model_dump(mode="json")
    payload["url"] = "http://example.com/paper"
    try:
        CandidateRecord.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("non-HTTPS candidate URL must be rejected")


def test_candidate_accepts_https_and_forbids_unknown_fields():
    candidate = _candidate()
    assert candidate.provenance == "LINKED"

    payload = candidate.model_dump(mode="json")
    payload["private_claim"] = "must never be accepted"
    try:
        CandidateRecord.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("unknown fields must be rejected")


def test_candidate_lineage_field_is_optional_and_backward_compatible():
    old_payload = _candidate().model_dump(mode="json", exclude={"supersedes_candidate_id"})
    restored = CandidateRecord.model_validate(old_payload)
    corrected = _candidate(
        candidate_id="CAND-fedcba9876543210",
        supersedes_candidate_id="CAND-0123456789abcdef",
        warnings=["CORRECTED_SOURCE"],
    )

    assert restored.supersedes_candidate_id is None
    assert corrected.supersedes_candidate_id == "CAND-0123456789abcdef"


def test_run_counters_identity_fields_default_to_zero():
    counters = RunCounters()

    assert counters.identity_verified == 0
    assert counters.identity_variants == 0
    assert counters.identity_ambiguous == 0
    assert counters.identity_mismatches == 0
    assert counters.identity_unverified == 0
    assert counters.identity_collisions == 0
