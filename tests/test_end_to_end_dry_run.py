import json
from pathlib import Path

import httpx

from basketball_miner.models import CandidateRecord, Checkpoint
from basketball_miner.run import run_miner
from basketball_miner.sources.crossref import CrossrefAdapter
from basketball_miner.sources.youtube_rss import YouTubeRssAdapter

FIXTURES = Path(__file__).parent / "fixtures"


class CollectingSink:
    def __init__(self) -> None:
        self.candidates: list[CandidateRecord] = []

    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]):
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


def _run_fixture_pipeline() -> tuple[object, list[CandidateRecord]]:
    crossref_payload = json.loads(
        (FIXTURES / "crossref.json").read_text(encoding="utf-8")
    )
    youtube_xml = (FIXTURES / "youtube_feed.xml").read_text(encoding="utf-8")

    crossref_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=crossref_payload)
        )
    )
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
    )
    return counters, sink.candidates


def test_fixture_pipeline_is_bounded_metadata_only_and_deterministic():
    first_counters, first_candidates = _run_fixture_pipeline()
    second_counters, second_candidates = _run_fixture_pipeline()

    assert first_counters.inspected <= 10
    assert first_counters.exported == len(first_candidates)
    assert first_counters.model_dump() == second_counters.model_dump()
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
