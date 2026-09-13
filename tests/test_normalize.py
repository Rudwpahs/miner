from basketball_miner.models import SourceRecord
from basketball_miner.normalize import canonicalize_url, fingerprint, stable_candidate_id


def _record(**overrides):
    payload = {
        "adapter": "crossref",
        "source_type": "academic",
        "stable_id": "10.1/X",
        "url": "https://Example.org/a?utm_source=x&id=7#section",
        "title": "  Basketball   Shooting  ",
        "authors": ["A. Author"],
        "published_at": "2026-09-01",
        "summary": None,
    }
    payload.update(overrides)
    return SourceRecord(**payload)


def test_canonicalize_url_removes_tracking_and_fragment():
    assert canonicalize_url("https://Example.org/a?utm_source=x&id=7#section") == "https://example.org/a?id=7"


def test_candidate_id_is_deterministic_and_uses_lowercase_stable_id():
    left = stable_candidate_id(_record(stable_id="10.1/X"))
    right = stable_candidate_id(_record(stable_id="10.1/x"))
    assert left == right
    assert left[0].startswith("CAND-")
    assert len(left[1]) == 64


def test_same_doi_metadata_canonicalization_keeps_candidate_id():
    observed = _record(
        stable_id="10.1000/example",
        url="https://doi.org/10.1000/example",
        title="Basketball Passing",
    )
    canonical = _record(
        stable_id="10.1000/example",
        url="https://doi.org/10.1000/example",
        title="Basketball Passing: A Biomechanical Study",
        authors=["Canonical Author"],
    )

    assert stable_candidate_id(observed) == stable_candidate_id(canonical)


def test_corrected_doi_creates_new_candidate_id():
    wrong = _record(
        stable_id="10.1000/wrong",
        url="https://doi.org/10.1000/wrong",
    )
    corrected = _record(
        stable_id="10.1000/right",
        url="https://doi.org/10.1000/right",
    )

    assert stable_candidate_id(wrong) != stable_candidate_id(corrected)


def test_fingerprint_normalizes_title_author_and_year():
    a = _record(title="Basketball   Shooting", authors=[" A. Author "], published_at="2026-09-01")
    b = _record(title="basketball shooting", authors=["a. author"], published_at="2026-01-15")
    assert fingerprint(a) == fingerprint(b)
