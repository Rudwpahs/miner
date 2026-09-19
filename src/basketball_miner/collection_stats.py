from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

_KST = ZoneInfo("Asia/Seoul")


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
