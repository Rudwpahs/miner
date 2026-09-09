from __future__ import annotations

import html
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.sources.base import AdapterBatch

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class CrossrefAdapter:
    name = "crossref"
    endpoint = "https://api.crossref.org/works"

    def __init__(self, client: httpx.Client | None = None, query: str = "basketball") -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=10.0, follow_redirects=True)
        self.query = query

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        if checkpoint.adapter != self.name:
            raise ValueError("checkpoint adapter must be crossref")
        if limit < 1:
            raise ValueError("limit must be >= 1")

        params: dict[str, str | int] = {
            "query.bibliographic": self.query,
            "rows": limit,
            "select": "DOI,title,author,published-online,published-print,abstract,URL",
        }
        if checkpoint.cursor:
            params["cursor"] = checkpoint.cursor
        else:
            params["cursor"] = "*"

        response: httpx.Response | None = None
        for attempt in range(3):
            response = self.client.get(
                self.endpoint,
                params=params,
                headers={
                    "User-Agent": "BasketballKnowledgeMiner/0.1 (public metadata collector)"
                },
            )
            if response.status_code == 429:
                return AdapterBatch(
                    records=[],
                    next_checkpoint=checkpoint,
                    rate_limited=True,
                    retry_after=response.headers.get("Retry-After"),
                )
            if response.status_code < 500:
                break
            if attempt < 2:
                time.sleep(1 if attempt == 0 else 2)

        assert response is not None
        response.raise_for_status()
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise TypeError("Crossref response missing message object")

        raw_items = message.get("items", [])
        if not isinstance(raw_items, list):
            raise TypeError("Crossref message.items must be a list")

        records: list[SourceRecord] = []
        errors = 0
        for item in raw_items:
            if len(records) >= limit:
                break
            try:
                record = self._record_from_item(item)
            except (TypeError, ValueError):
                errors += 1
                continue
            records.append(record)

        next_cursor = message.get("next-cursor")
        next_checkpoint = Checkpoint(
            adapter=self.name,
            last_checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            cursor=str(next_cursor) if next_cursor else checkpoint.cursor,
            last_stable_id_hash=checkpoint.last_stable_id_hash,
        )
        return AdapterBatch(
            records=records,
            next_checkpoint=next_checkpoint,
            error_count=errors,
        )

    @staticmethod
    def _record_from_item(item: Any) -> SourceRecord:
        if not isinstance(item, dict):
            raise TypeError("Crossref item must be an object")
        doi = str(item.get("DOI", "")).strip()
        titles = item.get("title")
        title = str(titles[0]).strip() if isinstance(titles, list) and titles else ""
        if not doi or not title:
            raise ValueError("Crossref item missing DOI or title")

        authors: list[str] = []
        for author in item.get("author", []) or []:
            if not isinstance(author, dict):
                continue
            name = " ".join(
                part
                for part in (
                    str(author.get("given", "")).strip(),
                    str(author.get("family", "")).strip(),
                )
                if part
            )
            if name:
                authors.append(name)

        published_at = CrossrefAdapter._published_date(item)
        abstract = item.get("abstract")
        summary = CrossrefAdapter._clean_abstract(str(abstract)) if abstract else None

        return SourceRecord(
            adapter="crossref",
            source_type="academic",
            stable_id=doi,
            url=f"https://doi.org/{doi}",
            title=title,
            authors=authors,
            published_at=published_at,
            summary=summary,
        )

    @staticmethod
    def _published_date(item: dict[str, Any]) -> str | None:
        for key in ("published-online", "published-print"):
            value = item.get(key)
            if not isinstance(value, dict):
                continue
            parts = value.get("date-parts")
            if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
                continue
            numbers = [int(value) for value in parts[0][:3]]
            if not numbers:
                continue
            year = numbers[0]
            month = numbers[1] if len(numbers) > 1 else 1
            day = numbers[2] if len(numbers) > 2 else 1
            return f"{year:04d}-{month:02d}-{day:02d}"
        return None

    @staticmethod
    def _clean_abstract(value: str) -> str:
        without_tags = _TAG_RE.sub(" ", value)
        return _WS_RE.sub(" ", html.unescape(without_tags)).strip()
