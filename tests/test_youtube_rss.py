import json
from pathlib import Path

import httpx

from basketball_miner.models import Checkpoint
from basketball_miner.sources.youtube_rss import YouTubeRssAdapter, load_channel_ids

CONFIG = Path(__file__).parents[1] / "config" / "youtube_channels.json"
FIXTURE = Path(__file__).parent / "fixtures" / "youtube_feed.xml"


def test_load_channel_ids_reads_only_allowlisted_ids():
    channel_ids = load_channel_ids(CONFIG)
    assert channel_ids == (
        "UCBo3XgAVBeE74Zw0T77aDhw",
        "UCtInrnU3QbWqFGsdKT1GZtg",
    )


def test_youtube_rss_maps_coaching_and_interview_without_transcript():
    xml = FIXTURE.read_text(encoding="utf-8")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.params["channel_id"])
        return httpx.Response(200, text=xml)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = YouTubeRssAdapter(
        channel_ids=("UCBo3XgAVBeE74Zw0T77aDhw",),
        client=client,
    )
    batch = adapter.fetch(Checkpoint(adapter="youtube_rss"), limit=10)

    assert requested == ["UCBo3XgAVBeE74Zw0T77aDhw"]
    assert [record.source_type for record in batch.records] == ["coaching", "interview"]
    assert [record.stable_id for record in batch.records] == ["coach123", "interview456"]
    assert str(batch.records[0].url) == "https://www.youtube.com/watch?v=coach123"
    assert batch.records[0].summary == "Short coaching description only."
    payload = json.dumps([record.model_dump(mode="json") for record in batch.records])
    assert "transcript" not in payload.lower()
    assert "video_bytes" not in payload.lower()


def test_youtube_rss_skips_failed_channel_and_continues_to_next():
    xml = FIXTURE.read_text(encoding="utf-8")
    failed_channel = "UC0000000000000000000000"
    working_channel = "UCBo3XgAVBeE74Zw0T77aDhw"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        channel_id = request.url.params["channel_id"]
        requested.append(channel_id)
        if channel_id == failed_channel:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=xml)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = YouTubeRssAdapter(
        channel_ids=(failed_channel, working_channel),
        client=client,
    )
    batch = adapter.fetch(Checkpoint(adapter="youtube_rss"), limit=10)

    assert requested == [failed_channel, working_channel]
    assert batch.error_count == 1
    assert [record.stable_id for record in batch.records] == ["coach123", "interview456"]
