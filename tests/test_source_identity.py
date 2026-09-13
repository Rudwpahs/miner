import httpx
import pytest

from basketball_miner import source_identity as identity
from basketball_miner.models import SourceRecord


def make_source(
    *,
    doi: str = "10.1000/example",
    title: str = "Basketball shooting biomechanics",
    author: str = "Ada Player",
    year: int = 2026,
) -> SourceRecord:
    return SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id=doi,
        url=f"https://doi.org/{doi}",
        title=title,
        authors=[author],
        published_at=f"{year:04d}-01-01",
        summary=None,
    )


def exact_payload(
    *,
    doi: str = "10.1000/example",
    title: str = "Basketball shooting biomechanics",
    family: str = "Player",
    year: int = 2026,
):
    return {
        "message": {
            "DOI": doi,
            "title": [title],
            "author": [{"given": "Ada", "family": family}],
            "published-online": {"date-parts": [[year, 1, 1]]},
            "URL": f"https://doi.org/{doi}",
        }
    }


def test_source_identity_public_api_exists():
    expected = {
        "normalize_title",
        "compare_titles",
        "CrossrefIdentityVerifier",
        "find_identity_collisions",
    }
    assert all(hasattr(identity, name) for name in expected)


def test_normalize_title_handles_unicode_punctuation_case_and_html():
    left = identity.normalize_title('Basketball—Shot: “Form” &amp; Timing')
    right = identity.normalize_title("basketball shot form & timing")
    assert left == right


def test_compare_titles_exact_after_normalization():
    result = identity.compare_titles(
        'Basketball—Shot: “Form”',
        "basketball shot form",
    )
    assert result.decision == "EXACT_MATCH"


def test_compare_titles_allows_long_subtitle_variant():
    result = identity.compare_titles(
        "Decision making in basketball pick and roll",
        "Decision making in basketball pick and roll: temporal constraints and passing choices",
    )
    assert result.decision == "HIGH_CONFIDENCE_VARIANT"


def test_compare_titles_is_strict_for_short_titles():
    result = identity.compare_titles("Basketball defense", "Basketball defensive")
    assert result.decision == "AMBIGUOUS"


def test_compare_titles_rejects_material_mismatch():
    result = identity.compare_titles(
        "Basketball jump shot release biomechanics",
        "Molecular signaling pathways in cardiac tissue",
    )
    assert result.decision == "MISMATCH"


def test_compare_titles_downgrades_fuzzy_match_on_author_conflict():
    result = identity.compare_titles(
        "Decision making in basketball pick and roll",
        "Decision making in basketball pick and roll: a temporal analysis",
        observed_authors=["Ada Player"],
        canonical_authors=["Bo Coach"],
        observed_published_at="2026-01-01",
        canonical_published_at="2026-01-01",
    )
    assert result.decision == "AMBIGUOUS"


def test_compare_titles_downgrades_fuzzy_match_on_year_conflict():
    result = identity.compare_titles(
        "Decision making in basketball pick and roll",
        "Decision making in basketball pick and roll: a temporal analysis",
        observed_authors=["Ada Player"],
        canonical_authors=["Ada Player"],
        observed_published_at="2022-01-01",
        canonical_published_at="2026-01-01",
    )
    assert result.decision == "AMBIGUOUS"


def test_crossref_verifier_returns_canonical_exact_record():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=exact_payload())
        )
    )
    verifier = identity.CrossrefIdentityVerifier(client=client)
    result = verifier.verify(make_source())
    assert result.decision == "EXACT_MATCH"
    assert result.source.title == "Basketball shooting biomechanics"
    assert result.warnings == ()


def test_crossref_verifier_preserves_mismatch_but_uses_canonical_metadata():
    payload = exact_payload(title="Basketball defensive closeout biomechanics")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    verifier = identity.CrossrefIdentityVerifier(client=client)
    result = verifier.verify(make_source(title="Basketball passing decision making"))
    assert result.decision == "MISMATCH"
    assert result.source.title == "Basketball defensive closeout biomechanics"
    assert "DOI_TITLE_MISMATCH" in result.warnings
    assert "DOI_CANONICAL_METADATA_USED" in result.warnings


def test_crossref_verifier_429_is_unverified_and_non_throwing():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(429))
    )
    verifier = identity.CrossrefIdentityVerifier(client=client)
    observed = make_source()
    result = verifier.verify(observed)
    assert result.decision == "UNVERIFIED"
    assert result.source == observed
    assert "DOI_IDENTITY_UNVERIFIED" in result.warnings


def test_multisource_synthesis_requires_supporting_source():
    verifier = identity.CrossrefIdentityVerifier(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    )
    with pytest.raises(ValueError):
        verifier.verify(make_source(), mode="MULTISOURCE_SYNTHESIS", supporting_sources=())

    result = verifier.verify(
        make_source(),
        mode="MULTISOURCE_SYNTHESIS",
        supporting_sources=("https://doi.org/10.1000/support",),
    )
    assert result.decision == "NOT_APPLICABLE"


def test_find_identity_collisions_flags_both_duplicate_metadata_dois():
    records = [
        make_source(doi="10.1000/a"),
        make_source(doi="10.1000/b"),
        make_source(doi="10.1000/c", title="Basketball defensive closeout"),
    ]
    assert identity.find_identity_collisions(records) == {0, 1}
