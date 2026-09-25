import json
from pathlib import Path

import httpx

from basketball_miner.models import CandidateRecord, Checkpoint
from basketball_miner.run import run_miner
from basketball_miner.source_identity import CrossrefIdentityVerifier
from basketball_miner.sources.crossref import CrossrefAdapter
from basketball_miner.sources.youtube_rss import YouTubeRssAdapter

FIXTURES = Path(__file__).parent / "fixtures"


class CollectingSink:
    def __init__(self) -> None:
        self.candidates: list[CandidateRecord] = []

    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]):
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


def _exact_crossref_response(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "BASKETBALL.1" in url:
        title = "Basketball jump-shot biomechanics"
        doi = "10.1000/BASKETBALL.1"
        author = {"given": "Ada", "family": "Player"}
        date = [2026, 8, 1]
    elif "BASKETBALL.2" in url:
        title = "Decision making in basketball pick and roll"
        doi = "10.1000/BASKETBALL.2"
        author = {"given": "Bo", "family": "Coach"}
        date = [2025, 12, 15]
    else:
        return httpx.Response(404)
    return httpx.Response(
        200,
        json={
            "message": {
                "DOI": doi,
                "title": [title],
                "author": [author],
                "published-online": {"date-parts": [date]},
                "URL": f"https://doi.org/{doi}",
            }
        },
    )


def _run_fixture_pipeline() -> tuple[object, list[CandidateRecord]]:
    crossref_payload = json.loads(
        (FIXTURES / "crossref.json").read_text(encoding="utf-8")
    )
    youtube_xml = (FIXTURES / "youtube_feed.xml").read_text(encoding="utf-8")
    crossref_calls = 0

    def crossref_handler(request: httpx.Request) -> httpx.Response:
        nonlocal crossref_calls
        crossref_calls += 1
        if crossref_calls == 1:
            return httpx.Response(200, json=crossref_payload)
        return httpx.Response(
            200,
            json={"message": {"items": [], "next-cursor": None}},
        )

    crossref_client = httpx.Client(transport=httpx.MockTransport(crossref_handler))
    identity_client = httpx.Client(transport=httpx.MockTransport(_exact_crossref_response))
    youtube_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=youtube_xml)
        )
    )

    adapters = [
        CrossrefAdapter(client=crossref_client),
        YouTubeRssAdapter(
            channel_ids=("UCBo3XgAVBeE74Zw0T77aDhw",),
            client=youtube_client,
        ),
    ]
    checkpoints = {
        "crossref": Checkpoint(adapter="crossref"),
        "youtube_rss": Checkpoint(adapter="youtube_rss"),
    }
    sink = CollectingSink()
    counters = run_miner(
        adapters,
        sink,
        budget=10,
        checkpoints=checkpoints,
        seen_hashes=set(),
        run_id="RUN-E2E-FIXTURE",
        identity_verifier=CrossrefIdentityVerifier(
            client=identity_client,
            sleep_fn=lambda seconds: None,
        ),
    )
    return counters, sink.candidates


def test_fixture_pipeline_is_bounded_metadata_only_and_deterministic():
    first_counters, first_candidates = _run_fixture_pipeline()
    second_counters, second_candidates = _run_fixture_pipeline()

    assert first_counters.inspected <= 10
    assert first_counters.exported == len(first_candidates)
    assert first_counters.model_dump() == second_counters.model_dump()
    assert first_counters.identity_verified == 2
    assert first_candidates

    first_ids = [candidate.candidate_id for candidate in first_candidates]
    second_ids = [candidate.candidate_id for candidate in second_candidates]
    assert first_ids == second_ids
    assert all(candidate_id.startswith("CAND-") for candidate_id in first_ids)

    forbidden_fields = {"transcript", "full_text", "raw_video", "video_bytes"}
    for candidate in first_candidates:
        payload = candidate.model_dump(mode="json")
        assert forbidden_fields.isdisjoint(payload)
        assert str(candidate.url).startswith("https://")
