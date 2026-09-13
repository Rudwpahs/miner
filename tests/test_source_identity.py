import httpx
import pytest

from basketball_miner.models import SourceRecord
from basketball_miner.source_identity import (
    CrossrefIdentityVerifier,
    IdentityDecision,
    ValidationMode,
    compare_titles,
    normalize_title,
)


def make_source(**overrides) -> SourceRecord:
    payload = {
        "adapter": "crossref",
        "source_type": "academic",
        "stable_id": "10.1000/example",
        "url": "https://doi.org/10.1000/example",
        "title": "Effects of defensive pressure on basketball passing",
        "authors": ["Ada Player"],
        "published_at": "2026-01-01",
        "summary": None,
    }
    payload.update(overrides)
    return SourceRecord(**payload)


def exact_payload(*, title=None, authors=None, year=2026, doi="10.1000/example"):
    return {
        "message": {
            "DOI": doi,
            "title": [title or "Effects of defensive pressure on basketball passing"],
            "author": authors or [{"given": "Ada", "family": "Player"}],
            "published-online": {"date-parts": [[year, 1, 1]]},
            "URL": f"https://doi.org/{doi}",
        }
    }


def test_normalize_title_collapses_unicode_case_and_punctuation():
    observed = 'Basketball—Jump Shot: “Biomechanics”'
    canonical = 'basketball jump shot biomechanics'

    assert normalize_title(observed) == normalize_title(canonical)
    assert compare_titles(observed, canonical).decision is IdentityDecision.EXACT_MATCH


def test_long_title_with_subtitle_is_high_confidence_variant():
    observed = "Effects of defensive pressure on basketball passing"
    canonical = (
        "Effects of defensive pressure on basketball passing: "
        "accuracy under fatigue"
    )

    assert compare_titles(observed, canonical).decision is IdentityDecision.HIGH_CONFIDENCE_VARIANT


def test_short_title_uses_strict_threshold_and_stays_ambiguous():
    observed = "Elite Basketball Passing"
    canonical = "Elite Basketball Pass"

    assert compare_titles(observed, canonical).decision is IdentityDecision.AMBIGUOUS


def test_materially_different_titles_are_mismatch():
    observed = "Basketball shooting biomechanics"
    canonical = "Nutrition in soccer players"

    assert compare_titles(observed, canonical).decision is IdentityDecision.MISMATCH


def test_middle_similarity_band_is_ambiguous():
    observed = "Effects of defensive pressure on basketball passing"
    canonical = "Effects of defensive pressure during basketball passing practice"

    result = compare_titles(observed, canonical)

    assert result.decision is IdentityDecision.AMBIGUOUS
    assert 0.75 <= result.sequence_ratio < 0.94


def test_exact_doi_lookup_returns_canonical_exact_match():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=exact_payload())
        )
    )
    verifier = CrossrefIdentityVerifier(client=client, sleep_fn=lambda seconds: None)

    result = verifier.verify(make_source())

    assert result.decision is IdentityDecision.EXACT_MATCH
    assert result.canonical_source is not None
    assert result.canonical_source.stable_id == "10.1000/example"
    assert result.warnings == ()


def test_fuzzy_title_match_with_first_author_conflict_is_ambiguous():
    payload = exact_payload(
        title="Effects of defensive pressure on basketball passing: accuracy under fatigue",
        authors=[{"given": "Bo", "family": "Coach"}],
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    verifier = CrossrefIdentityVerifier(client=client, sleep_fn=lambda seconds: None)

    result = verifier.verify(make_source())

    assert result.decision is IdentityDecision.AMBIGUOUS
    assert "DOI_TITLE_AMBIGUOUS" in result.warnings


def test_fuzzy_title_match_with_year_difference_over_one_is_ambiguous():
    payload = exact_payload(
        title="Effects of defensive pressure on basketball passing: accuracy under fatigue",
        year=2023,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    verifier = CrossrefIdentityVerifier(client=client, sleep_fn=lambda seconds: None)

    result = verifier.verify(make_source())

    assert result.decision is IdentityDecision.AMBIGUOUS


def test_429_returns_unverified_without_raising():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(429))
    )
    verifier = CrossrefIdentityVerifier(client=client, sleep_fn=lambda seconds: None)

    result = verifier.verify(make_source())

    assert result.decision is IdentityDecision.UNVERIFIED
    assert result.canonical_source is None
    assert result.warnings == ("DOI_IDENTITY_UNVERIFIED",)


def test_5xx_exhaustion_returns_unverified_and_retries_three_times():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    verifier = CrossrefIdentityVerifier(client=client, sleep_fn=lambda seconds: None)

    result = verifier.verify(make_source())

    assert calls == 3
    assert result.decision is IdentityDecision.UNVERIFIED


def test_multisource_synthesis_is_not_applicable_with_supporting_source():
    verifier = CrossrefIdentityVerifier(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    )

    result = verifier.verify(
        make_source(stable_id="MULTISOURCE_SYNTHESIS_2026-09-13"),
        mode=ValidationMode.MULTISOURCE_SYNTHESIS,
        supporting_sources=("https://doi.org/10.1000/support",),
    )

    assert result.decision is IdentityDecision.NOT_APPLICABLE


def test_multisource_synthesis_requires_supporting_source():
    verifier = CrossrefIdentityVerifier(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    )

    with pytest.raises(ValueError, match="supporting source"):
        verifier.verify(
            make_source(stable_id="MULTISOURCE_SYNTHESIS_2026-09-13"),
            mode=ValidationMode.MULTISOURCE_SYNTHESIS,
        )
