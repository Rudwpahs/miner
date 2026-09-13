import httpx

from basketball_miner.models import SourceRecord
from basketball_miner.source_identity import CrossrefIdentityVerifier, IdentityDecision


def _source() -> SourceRecord:
    return SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/example",
        url="https://doi.org/10.1000/example",
        title="Basketball passing under pressure",
        authors=["Ada Player"],
        published_at="2026-01-01",
        summary=None,
    )


def test_timeout_exhaustion_is_unverified_and_does_not_raise():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    verifier = CrossrefIdentityVerifier(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=lambda seconds: None,
    )

    result = verifier.verify(_source())

    assert calls == 3
    assert result.decision is IdentityDecision.UNVERIFIED
    assert result.warnings == ("DOI_IDENTITY_UNVERIFIED",)


def test_not_found_is_unverified_and_does_not_raise():
    verifier = CrossrefIdentityVerifier(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404))
        ),
        sleep_fn=lambda seconds: None,
    )

    result = verifier.verify(_source())

    assert result.decision is IdentityDecision.UNVERIFIED


def test_malformed_exact_response_is_unverified():
    verifier = CrossrefIdentityVerifier(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"message": {"DOI": "10.1000/example"}})
            )
        ),
        sleep_fn=lambda seconds: None,
    )

    result = verifier.verify(_source())

    assert result.decision is IdentityDecision.UNVERIFIED
