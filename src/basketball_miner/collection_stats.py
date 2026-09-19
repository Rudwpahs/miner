from __future__ import annotations

from datetime import datetime
import json
import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

_KST = ZoneInfo("Asia/Seoul")
INBOX_ROOT = "ml/coach/miner-data/inbox"
CANDIDATE_RE = re.compile(r"^CAND-[0-9a-f]{16}$")


class MinerTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_target: int = Field(gt=0, le=100_000)
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"


class CollectionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    date: str
    today_collected: int = Field(ge=0)
    collected_total: int = Field(ge=0)
    daily_counts: dict[str, int] = Field(default_factory=dict)
    last_miner_run_at: str | None = None

    @classmethod
    def empty(cls, date: str) -> "CollectionStats":
        return cls(date=date, today_collected=0, collected_total=0)


def _today(now: datetime) -> str:
    return now.astimezone(_KST).date().isoformat()


def _pruned_counts(values: dict[str, int]) -> dict[str, int]:
    for count in values.values():
        if count < 0:
            raise ValueError("daily counts must be non-negative")
    return {key: values[key] for key in sorted(values)[-7:]}


def load_target_config(path: Path) -> MinerTargetConfig:
    return MinerTargetConfig.model_validate_json(path.read_text(encoding="utf-8"))


def load_collection_stats(path: Path, now: datetime) -> CollectionStats:
    today = _today(now)
    if not path.exists():
        return CollectionStats.empty(today)
    stats = CollectionStats.model_validate_json(path.read_text(encoding="utf-8"))
    if stats.date != today:
        return stats.model_copy(
            update={
                "date": today,
                "today_collected": 0,
                "daily_counts": _pruned_counts(stats.daily_counts),
            }
        )
    return stats.model_copy(update={"daily_counts": _pruned_counts(stats.daily_counts)})


def remaining_target(config: MinerTargetConfig, stats: CollectionStats) -> int:
    return max(0, config.daily_target - stats.today_collected)


def apply_export(stats: CollectionStats, exported: int, run_at: datetime) -> CollectionStats:
    if exported < 0:
        raise ValueError("exported must be non-negative")
    today = _today(run_at)
    current = stats
    if stats.date != today:
        current = stats.model_copy(update={"date": today, "today_collected": 0})
    today_collected = current.today_collected + exported
    daily_counts = dict(current.daily_counts)
    daily_counts[today] = today_collected
    return current.model_copy(
        update={
            "today_collected": today_collected,
            "collected_total": current.collected_total + exported,
            "daily_counts": _pruned_counts(daily_counts),
            "last_miner_run_at": run_at.astimezone(_KST).isoformat(),
        }
    )


def bootstrap_collection_stats(store, now: datetime) -> CollectionStats:
    today = _today(now)
    seen: set[str] = set()
    daily_counts: dict[str, int] = {}

    def walk(path: str):
        for entry in store.list_dir(path):
            if entry.type == "dir":
                yield from walk(entry.path)
            elif entry.type == "file" and entry.name.endswith(".jsonl"):
                yield entry.path

    for path in sorted(walk(INBOX_ROOT)):
        parts = path.split("/")
        try:
            year_index = parts.index("inbox") + 1
            date = "-".join(parts[year_index : year_index + 3])
        except (ValueError, IndexError) as exc:
            raise ValueError(f"invalid inbox path: {path}") from exc
        remote = store.read_file(path)
        if remote is None:
            raise RuntimeError(f"inbox file disappeared: {path}")
        try:
            text = remote.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"inbox file is not UTF-8: {path}") from exc
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid inbox JSONL: {path}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"inbox row must be an object: {path}")
            candidate_id = row.get("candidate_id")
            if not isinstance(candidate_id, str) or CANDIDATE_RE.fullmatch(candidate_id) is None:
                raise ValueError(f"invalid candidate_id in inbox: {path}")
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            daily_counts[date] = daily_counts.get(date, 0) + 1

    pruned = _pruned_counts(daily_counts)
    return CollectionStats(
        date=today,
        today_collected=pruned.get(today, 0),
        collected_total=len(seen),
        daily_counts=pruned,
    )
