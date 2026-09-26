from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from basketball_miner.models import Checkpoint
from basketball_miner.sources.crossref import CrossrefAdapter
from basketball_miner.sources.youtube_rss import (
    YouTubeRssAdapter,
    load_channel_ids,
    load_legacy_users,
)


def _clean_abstract_length(item: dict[str, Any]) -> int:
    abstract = item.get("abstract")
    if not abstract:
        return 0
    return len(CrossrefAdapter._clean_abstract(str(abstract)))


def diagnose_crossref(cursor: str | None, pages: int, rows: int) -> dict[str, Any]:
    client = httpx.Client(timeout=20.0, follow_redirects=True)
    adapter = CrossrefAdapter(client=client)
    reason_counts: Counter[str] = Counter()
    validation_locations: Counter[str] = Counter()
    raw_total = 0
    parsed_total = 0
    examples: list[dict[str, Any]] = []

    current_cursor = cursor or "*"
    for page in range(pages):
        params: dict[str, str | int] = {
            "query.bibliographic": "basketball",
            "rows": rows,
            "select": "DOI,title,author,published-online,published-print,abstract,URL",
            "cursor": current_cursor,
        }
        response = client.get(
            adapter.endpoint,
            params=params,
            headers={"User-Agent": "BasketballKnowledgeMiner/0.1 (diagnostic)"},
        )
        response.raise_for_status()
        payload = response.json()
        message = payload["message"]
        raw_items = message.get("items", [])
        raw_total += len(raw_items)

        for item in raw_items:
            item_reasons: list[str] = []
            if not isinstance(item, dict):
                item_reasons.append("item_not_object")
            else:
                doi = str(item.get("DOI", "")).strip()
                titles = item.get("title")
                title = str(titles[0]).strip() if isinstance(titles, list) and titles else ""
                authors = item.get("author", []) or []
                abstract_len = _clean_abstract_length(item)

                if not doi:
                    item_reasons.append("missing_doi")
                if not title:
                    item_reasons.append("missing_title")
                if len(doi) > 500:
                    item_reasons.append("doi_gt_500")
                if len(title) > 500:
                    item_reasons.append("title_gt_500")
                if isinstance(authors, list) and len(authors) > 32:
                    item_reasons.append("authors_gt_32")
                if abstract_len > 1200:
                    item_reasons.append("abstract_gt_1200")

            try:
                adapter._record_from_item(item)
                parsed_total += 1
            except ValidationError as exc:
                item_reasons.append("pydantic_validation_error")
                for err in exc.errors():
                    loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
                    validation_locations[loc] += 1
            except (TypeError, ValueError) as exc:
                item_reasons.append(f"parse_exception:{type(exc).__name__}")

            if item_reasons:
                for reason in sorted(set(item_reasons)):
                    reason_counts[reason] += 1
                if len(examples) < 12:
                    if isinstance(item, dict):
                        titles = item.get("title")
                        title = str(titles[0]).strip() if isinstance(titles, list) and titles else ""
                        authors = item.get("author", []) or []
                        examples.append(
                            {
                                "doi": str(item.get("DOI", ""))[:160],
                                "title": title[:180],
                                "title_length": len(title),
                                "author_count": len(authors) if isinstance(authors, list) else None,
                                "abstract_length": _clean_abstract_length(item),
                                "reasons": sorted(set(item_reasons)),
                            }
                        )
                    else:
                        examples.append({"reasons": sorted(set(item_reasons))})

        next_cursor = message.get("next-cursor")
        print(
            json.dumps(
                {
                    "page": page + 1,
                    "raw": len(raw_items),
                    "cumulative_raw": raw_total,
                    "cumulative_parsed": parsed_total,
                    "cumulative_failures": raw_total - parsed_total,
                },
                sort_keys=True,
            )
        )
        if not next_cursor or not raw_items:
            break
        current_cursor = str(next_cursor)

    return {
        "raw_total": raw_total,
        "parsed_total": parsed_total,
        "failed_total": raw_total - parsed_total,
        "failure_rate": round((raw_total - parsed_total) / raw_total, 4) if raw_total else None,
        "reason_counts": dict(reason_counts.most_common()),
        "validation_locations": dict(validation_locations.most_common()),
        "examples": examples,
    }


def diagnose_youtube(config_path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for channel_id in load_channel_ids(config_path):
        adapter = YouTubeRssAdapter((channel_id,))
        batch = adapter.fetch(Checkpoint(adapter="youtube_rss"), 100)
        results.append(
            {
                "source": f"channel_id:{channel_id}",
                "raw_count": batch.raw_count,
                "records": len(batch.records),
                "error_count": batch.error_count,
                "rate_limited": batch.rate_limited,
            }
        )
    for user in load_legacy_users(config_path):
        adapter = YouTubeRssAdapter((), legacy_users=(user,))
        batch = adapter.fetch(Checkpoint(adapter="youtube_rss"), 100)
        results.append(
            {
                "source": f"user:{user}",
                "raw_count": batch.raw_count,
                "records": len(batch.records),
                "error_count": batch.error_count,
                "rate_limited": batch.rate_limited,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--youtube-config", type=Path, default=Path("config/youtube_channels.json"))
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--rows", type=int, default=100)
    args = parser.parse_args()

    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    cursor = state.get("cursor")
    print("CROSSREF_DIAGNOSTIC")
    print(json.dumps(diagnose_crossref(cursor, args.pages, args.rows), indent=2, sort_keys=True))
    print("YOUTUBE_DIAGNOSTIC")
    print(json.dumps(diagnose_youtube(args.youtube_config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
