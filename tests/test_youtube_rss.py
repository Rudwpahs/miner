import json
from pathlib import Path

import httpx

from basketball_miner.models import Checkpoint
from basketball_miner.sources.youtube_rss import (
    YouTubeRssAdapter,
    load_channel_ids,
    load_collect_all_channel_ids,
    load_legacy_users,
)

CONFIG = Path(__file__).parents[1] / "config" / "youtube_channels.json"
FIXTURE = Path(__file__).parent / "fixtures" / "youtube_feed.xml"


def test_load_channel_ids_reads_only_allowlisted_ids():
    channel_ids = load_channel_ids(CONFIG)
    assert channel_ids == (
        "UCBo3XgAVBeE74Zw0T77aDhw",
        "UCtInrnU3QbWqFGsdKT1GZtg",
        "UC9vie9VZdTqsxu4_H8asuPw",
        "UCDP7U_0S1zP3AiNqCO6ejuQ",
        "UC0EVRyv6lA6xs-QtFsKqexg",
        "UCe1Ka1W1V_TfRSwE_luacEg",
        "UCXrvAIp3vLwn0sGa4xf6Tyw",
        "UCqjq2Zq6QUwpDR45Ns89YDw",
        "UCspHEavqXfrWe2YFps_4RiQ",
        "UCgP1mu1UXba9r2owhCHo--g",
    )


def test_load_collect_all_channel_ids_reads_channels_that_only_teach():
    assert load_collect_all_channel_ids(CONFIG) == (
        "UCe1Ka1W1V_TfRSwE_luacEg",  # Vision Driven Basketball
        "UCXrvAIp3vLwn0sGa4xf6Tyw",  # Shot Science Basketball
        "UCqjq2Zq6QUwpDR45Ns89YDw",  # ShotMechanics
        "UCspHEavqXfrWe2YFps_4RiQ",  # Coach Dave Love
        "UCgP1mu1UXba9r2owhCHo--g",  # ILoveBasketballTV
    )


def test_load_legacy_users_reads_the_hoop_doctors():
    assert load_legacy_users(CONFIG) == ("TheHoopDoctors",)


def test_youtube_rss_supports_legacy_user_allowlist_entry():
    xml = FIXTURE.read_text(encoding="utf-8")
    requested: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(dict(request.url.params))
        return httpx.Response(200, text=xml)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = YouTubeRssAdapter(
        channel_ids=(),
        legacy_users=("TheHoopDoctors",),
        client=client,
    )
    batch = adapter.fetch(Checkpoint(adapter="youtube_rss"), limit=10)

    assert requested == [{"user": "TheHoopDoctors"}]
    assert [record.stable_id for record in batch.records] == ["coach123", "interview456"]


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


def test_youtube_rss_collect_all_keeps_neutral_title_for_selected_channel():
    channel_id = "UCe1Ka1W1V_TfRSwE_luacEg"
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <yt:videoId>vision123</yt:videoId>
    <yt:channelId>{channel_id}</yt:channelId>
    <title>3 Things I Wish I Knew Earlier</title>
    <author><name>Vision Driven Basketball</name></author>
    <published>2026-09-29T00:00:00+00:00</published>
    <media:group><media:description>Basketball development lesson.</media:description></media:group>
  </entry>
</feed>'''

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    filtered = YouTubeRssAdapter(channel_ids=(channel_id,), client=client)
    collected = YouTubeRssAdapter(
        channel_ids=(channel_id,),
        collect_all_channel_ids=(channel_id,),
        client=client,
    )

    filtered_batch = filtered.fetch(Checkpoint(adapter="youtube_rss"), limit=10)
    collected_batch = collected.fetch(Checkpoint(adapter="youtube_rss"), limit=10)

    assert filtered_batch.records == []
    assert [record.stable_id for record in collected_batch.records] == ["vision123"]
    assert collected_batch.records[0].source_type == "coaching"


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


def _feed(channel_id: str, video_ids: list[str]) -> str:
    entries = "".join(
        f"<entry><yt:videoId>{video_id}</yt:videoId><yt:channelId>{channel_id}</yt:channelId>"
        f"<title>Shooting drill {video_id}</title><author><name>{channel_id}</name></author>"
        "<published>2026-11-01T00:00:00+00:00</published></entry>"
        for video_id in video_ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">'
        f"{entries}</feed>"
    )


CHANNEL_A = "UCaaaaaaaaaaaaaaaaaaaaaa"
CHANNEL_B = "UCbbbbbbbbbbbbbbbbbbbbbb"
CHANNEL_C = "UCcccccccccccccccccccccc"


def _rotating_adapter(requested: list[str]) -> YouTubeRssAdapter:
    feeds = {channel: _feed(channel, [f"{channel[2].lower()}{n}" for n in (1, 2, 3)])
             for channel in (CHANNEL_A, CHANNEL_B, CHANNEL_C)}

    def handler(request: httpx.Request) -> httpx.Response:
        channel_id = request.url.params["channel_id"]
        requested.append(channel_id)
        return httpx.Response(200, text=feeds[channel_id])

    return YouTubeRssAdapter(channel_ids=tuple(feeds), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_youtube_rss_full_limit_resumes_at_the_cut_off_channel_on_the_next_run():
    adapter = _rotating_adapter([])

    first = adapter.fetch(Checkpoint(adapter="youtube_rss"), limit=4)
    second = adapter.fetch(first.next_checkpoint, limit=4)
    third = adapter.fetch(second.next_checkpoint, limit=4)

    assert [r.stable_id for r in first.records] == ["a1", "a2", "a3", "b1"]
    # The channel that was cut off is read first next time, so no channel behind it is starved.
    assert [r.stable_id for r in second.records] == ["b1", "b2", "b3", "c1"]
    assert [r.stable_id for r in third.records] == ["c1", "c2", "c3", "a1"]


def test_youtube_rss_reads_every_channel_once_when_under_the_limit():
    requested: list[str] = []
    adapter = _rotating_adapter(requested)

    batch = adapter.fetch(Checkpoint(adapter="youtube_rss", cursor="2"), limit=50)

    assert requested == [CHANNEL_C, CHANNEL_A, CHANNEL_B]
    assert len(batch.records) == 9
    assert batch.next_checkpoint.cursor == "2"


def test_youtube_rss_tolerates_a_cursor_from_another_channel_list():
    requested: list[str] = []
    adapter = _rotating_adapter(requested)

    adapter.fetch(Checkpoint(adapter="youtube_rss", cursor="7"), limit=50)
    assert requested[0] == CHANNEL_B  # 7 % 3
    requested.clear()
    adapter.fetch(Checkpoint(adapter="youtube_rss", cursor="not-a-number"), limit=50)
    assert requested[0] == CHANNEL_A
