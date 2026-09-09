import json
from pathlib import Path

import httpx

from basketball_miner.models import Checkpoint
from basketball_miner.sources.crossref import CrossrefAdapter


FIXTURE = Path(__file__).parent / "fixtures" / "crossref.json"


def test_crossref_maps_valid_rows_and_skips_malformed():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.crossref.org"
        assert "basketball" in str(request.url)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    batch = CrossrefAdapter(client=client).fetch(Checkpoint(adapter="crossref"), limit=2)

    assert len(batch.records) == 2
    assert batch.error_count == 1
    assert batch.records[0].stable_id == "10.1000/BASKETBALL.1"
    assert str(batch.records[0].url) == "https://doi.org/10.1000/BASKETBALL.1"
    assert batch.records[0].summary == "Release angle and velocity in basketball shooting."
    assert batch.next_checkpoint.cursor == "next-page"
    assert batch.rate_limited is False


def test_crossref_never_exceeds_limit():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    batch = CrossrefAdapter(client=client).fetch(Checkpoint(adapter="crossref"), limit=1)
    assert len(batch.records) == 1


def test_crossref_marks_429_rate_limited_without_advancing_cursor():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "60"})
        )
    )
    checkpoint = Checkpoint(adapter="crossref", cursor="same")
    batch = CrossrefAdapter(client=client).fetch(checkpoint, limit=10)
    assert batch.rate_limited is True
    assert batch.next_checkpoint == checkpoint
    assert batch.retry_after == "60"
