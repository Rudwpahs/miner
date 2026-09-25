from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx

from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.sources.base import AdapterBatch

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

_COACHING_TERMS = (
    "coach",
    "coaching",
    "drill",
    "training",
    "technique",
    "footwork",
    "shooting",
    "breakdown",
    "explains",
)
_INTERVIEW_TERMS = (
    "interview",
    "mic'd up",
    "mic’d up",
    "conversation",
    "talks",
)


def _load_allowlist(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    channels = payload.get("channels") if isinstance(payload, dict) else None
    if not isinstance(channels, list):
        raise TypeError("youtube allowlist channels must be a list")
    result: list[dict[str, object]] = []
    for item in channels:
        if not isinstance(item, dict):
            raise TypeError("youtube allowlist item must be an object")
        has_channel_id = bool(str(item.get("channel_id", "")).strip())
        has_user = bool(str(item.get("user", "")).strip())
        if has_channel_id == has_user:
            raise ValueError("youtube allowlist item must contain exactly one of channel_id or user")
        result.append(item)
    return result


def load_channel_ids(path: Path) -> tuple[str, ...]:
    ids: list[str] = []
    for item in _load_allowlist(path):
        channel_id = str(item.get("channel_id", "")).strip()
        if not channel_id:
            continue
        if not channel_id.startswith("UC"):
            raise ValueError("youtube channel_id must start with UC")
        ids.append(channel_id)
    return tuple(ids)


def load_legacy_users(path: Path) -> tuple[str, ...]:
    users: list[str] = []
    for item in _load_allowlist(path):
        user = str(item.get("user", "")).strip()
        if user:
            users.append(user)
    return tuple(users)


class YouTubeRssAdapter:
    name = "youtube_rss"
    endpoint = "https://www.youtube.com/feeds/videos.xml"

    def __init__(
        self,
        channel_ids: tuple[str, ...],
        client: httpx.Client | None = None,
        *,
        legacy_users: tuple[str, ...] = (),
    ) -> None:
        self.channel_ids = channel_ids
        self.legacy_users = legacy_users
        self.client = client or httpx.Client(timeout=10.0, follow_redirects=True)

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        if checkpoint.adapter != self.name:
            raise ValueError("checkpoint adapter must be youtube_rss")
        if limit < 1:
            raise ValueError("limit must be >= 1")

        records: list[SourceRecord] = []
        errors = 0
        raw_count = 0
        sources = [
            ("channel_id", channel_id, channel_id) for channel_id in self.channel_ids
        ] + [("user", user, None) for user in self.legacy_users]

        for param_name, source_value, expected_channel_id in sources:
            if len(records) >= limit:
                break
            response = self.client.get(self.endpoint, params={param_name: source_value})
            if response.status_code == 429:
                return AdapterBatch(
                    records=records,
                    next_checkpoint=checkpoint,
                    rate_limited=True,
                    error_count=errors,
                    retry_after=response.headers.get("Retry-After"),
                    has_more=False,
                    raw_count=raw_count,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                errors += 1
                continue
            try:
                root = ET.fromstring(response.text)
            except ET.ParseError:
                errors += 1
                continue

            entries = root.findall(f"{_ATOM}entry")
            raw_count += len(entries)
            for entry in entries:
                if len(records) >= limit:
                    break
                try:
                    record = self._record_from_entry(entry, expected_channel_id)
                except (TypeError, ValueError):
                    errors += 1
                    continue
                if record is not None:
                    records.append(record)

        next_checkpoint = Checkpoint(
            adapter=self.name,
            last_checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            cursor=checkpoint.cursor,
            last_stable_id_hash=checkpoint.last_stable_id_hash,
        )
        return AdapterBatch(
            records=records,
            next_checkpoint=next_checkpoint,
            error_count=errors,
            has_more=False,
            raw_count=raw_count,
        )

    @staticmethod
    def _record_from_entry(
        entry: ET.Element,
        expected_channel_id: str | None,
    ) -> SourceRecord | None:
        video_id = (entry.findtext(f"{_YT}videoId") or "").strip()
        channel_id = (entry.findtext(f"{_YT}channelId") or "").strip()
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        published = (entry.findtext(f"{_ATOM}published") or "").strip() or None
        author_node = entry.find(f"{_ATOM}author/{_ATOM}name")
        author = (author_node.text or "").strip() if author_node is not None else ""
        description = entry.findtext(f"{_MEDIA}group/{_MEDIA}description")
        summary = description.strip()[:1200] if description and description.strip() else None

        if not video_id or not title:
            raise ValueError("youtube entry missing stable identity")
        if expected_channel_id is not None and channel_id != expected_channel_id:
            raise ValueError("youtube entry channel mismatch")

        source_type = YouTubeRssAdapter._source_type(title)
        if source_type is None:
            return None

        return SourceRecord(
            adapter="youtube_rss",
            source_type=source_type,
            stable_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=title,
            authors=[author] if author else [],
            published_at=published,
            summary=summary,
        )

    @staticmethod
    def _source_type(title: str) -> str | None:
        lowered = title.casefold()
        if any(term in lowered for term in _INTERVIEW_TERMS):
            return "interview"
        if any(term in lowered for term in _COACHING_TERMS):
            return "coaching"
        return None
