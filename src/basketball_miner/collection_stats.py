from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from basketball_miner.models import SourceRecord
from basketball_miner.normalize import fingerprint

_KST = ZoneInfo("Asia/Seoul")
INBOX_ROOT = "ml/coach/miner-data/inbox"
CANDIDATE_RE = re.compile(r"^CAND-[0-9a-f]{16}$")


class MinerTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_target: int = Field(gt=0, le=100_000)
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    burst_daily_target: int | None = Field(default=None, gt=0, le=100_000)
    burst_start_date: date | None = None
    burst_end_date_exclusive: date | None = None

    @model_validator(mode="after")
    def validate_burst_window(self) -> MinerTargetConfig:
        values = (
            self.burst_daily_target,
            self.burst_start_date,
            self.burst_end_date_exclusive,
        )
        configured = [value is not None for value in values]
        if any(configured) and not all(configured):
            raise ValueError(
                "burst_daily_target, burst_start_date, and burst_end_date_exclusive "
                "must be set together"
            )
        if (
            self.burst_start_date is not None
            and self.burst_end_date_exclusive is not None
            and self.burst_start_date >= self.burst_end_date_exclusive
        ):
            raise ValueError("burst_start_date must be before burst_end_date_exclusive")
        return self


class CollectionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    date: str
    today_collected: int = Field(ge=0)
    collected_total: int = Field(ge=0)
    daily_counts: dict[str, int] = Field(default_factory=dict)
    last_miner_run_at: str | None = None
    reconciled_through_at: str | None = None

    @classmethod
    def empty(cls, date: str) -> CollectionStats:
        return cls(date=date, today_collected=0, collected_total=0)


def _today(now: datetime) -> str:
    return now.astimezone(_KST).date().isoformat()


def _pruned_counts(values: dict[str, int]) -> dict[str, int]:
    for count in values.values():
        if count < 0:
            raise ValueError("daily counts must be non-negative")
    return {key: values[key] for key in sorted(values)[-7:]}


def _aware_timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _utc_dates(start: datetime, end: datetime):
    current = start.astimezone(UTC).date()
    final = end.astimezone(UTC).date()
    while current <= final:
        yield current
        current += timedelta(days=1)


def _selected_source(row: dict) -> SourceRecord:
    payload = {name: row[name] for name in SourceRecord.model_fields if name in row}
    return SourceRecord.model_validate(payload)


def _restore_seen_state(
    row: dict,
    *,
    seen_hashes: set[str] | None,
    seen_crossref_dois: set[str] | None,
) -> None:
    if seen_hashes is None and seen_crossref_dois is None:
        return
    source = _selected_source(row)
    if seen_hashes is not None:
        seen_hashes.add(fingerprint(source))
    if seen_crossref_dois is not None and source.adapter == "crossref":
        seen_crossref_dois.add(source.stable_id.casefold().strip())


def _parse_row(line: str, *, path: str) -> dict:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid inbox JSONL: {path}") from exc
    if not isinstance(row, dict):
        raise TypeError(f"inbox row must be an object: {path}")
    candidate_id = row.get("candidate_id")
    if not isinstance(candidate_id, str) or CANDIDATE_RE.fullmatch(candidate_id) is None:
        raise ValueError(f"invalid candidate_id in inbox: {path}")
    return row


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


def effective_daily_target(config: MinerTargetConfig, stats: CollectionStats) -> int:
    if (
        config.burst_daily_target is None
        or config.burst_start_date is None
        or config.burst_end_date_exclusive is None
    ):
        return config.daily_target
    stats_date = date.fromisoformat(stats.date)
    if config.burst_start_date <= stats_date < config.burst_end_date_exclusive:
        return config.burst_daily_target
    return config.daily_target


def remaining_target(config: MinerTargetConfig, stats: CollectionStats) -> int:
    return max(0, effective_daily_target(config, stats) - stats.today_collected)


def apply_export(stats: CollectionStats, exported: int, run_at: datetime) -> CollectionStats:
    if exported < 0:
        raise ValueError("exported must be non-negative")
    local_run_at = run_at.astimezone(_KST)
    today = local_run_at.date().isoformat()
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
            "last_miner_run_at": local_run_at.isoformat(),
            "reconciled_through_at": local_run_at.isoformat(),
        }
    )


def bootstrap_collection_stats(
    store,
    now: datetime,
    *,
    seen_hashes: set[str] | None = None,
    seen_crossref_dois: set[str] | None = None,
) -> CollectionStats:
    local_now = now.astimezone(_KST)
    today = local_now.date().isoformat()
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
            fallback_date = "-".join(parts[year_index : year_index + 3])
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
            row = _parse_row(line, path=path)
            candidate_id = row["candidate_id"]
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            discovered_raw = row.get("discovered_at")
            if discovered_raw is None:
                discovery_date = fallback_date
            elif isinstance(discovered_raw, str):
                discovery_date = (
                    _aware_timestamp(discovered_raw, field="discovered_at")
                    .astimezone(_KST)
                    .date()
                    .isoformat()
                )
            else:
                raise TypeError(f"invalid discovered_at in inbox: {path}")
            daily_counts[discovery_date] = daily_counts.get(discovery_date, 0) + 1
            _restore_seen_state(
                row,
                seen_hashes=seen_hashes,
                seen_crossref_dois=seen_crossref_dois,
            )

    pruned = _pruned_counts(daily_counts)
    return CollectionStats(
        date=today,
        today_collected=pruned.get(today, 0),
        collected_total=len(seen),
        daily_counts=pruned,
        reconciled_through_at=local_now.isoformat(),
    )


def reconcile_collection_stats(
    store,
    stats: CollectionStats,
    now: datetime,
    *,
    seen_hashes: set[str] | None = None,
    seen_crossref_dois: set[str] | None = None,
) -> CollectionStats:
    local_now = now.astimezone(_KST)
    today = local_now.date().isoformat()
    current = stats
    if current.date != today:
        current = current.model_copy(update={"date": today, "today_collected": 0})

    if current.reconciled_through_at is None:
        return current.model_copy(update={"reconciled_through_at": local_now.isoformat()})

    cutoff = _aware_timestamp(
        current.reconciled_through_at,
        field="reconciled_through_at",
    )
    end = local_now
    if end <= cutoff:
        return current

    seen_candidates: set[str] = set()
    recovered_by_day: dict[str, int] = {}

    for day in _utc_dates(cutoff, end):
        day_path = f"{INBOX_ROOT}/{day:%Y/%m/%d}"
        entries = sorted(store.list_dir(day_path), key=lambda entry: (entry.name, entry.path))
        for entry in entries:
            if entry.type != "file" or not entry.name.endswith(".jsonl"):
                continue
            remote = store.read_file(entry.path)
            if remote is None:
                raise RuntimeError(f"inbox file disappeared: {entry.path}")
            try:
                text = remote.content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"inbox file is not UTF-8: {entry.path}") from exc
            for line in text.splitlines():
                if not line.strip():
                    continue
                row = _parse_row(line, path=entry.path)
                discovered_raw = row.get("discovered_at")
                if not isinstance(discovered_raw, str):
                    raise TypeError(f"missing discovered_at in inbox: {entry.path}")
                discovered = _aware_timestamp(discovered_raw, field="discovered_at")
                if not cutoff < discovered <= end:
                    continue
                candidate_id = row["candidate_id"]
                if candidate_id in seen_candidates:
                    continue
                seen_candidates.add(candidate_id)
                discovery_day = discovered.astimezone(_KST).date().isoformat()
                recovered_by_day[discovery_day] = recovered_by_day.get(discovery_day, 0) + 1
                _restore_seen_state(
                    row,
                    seen_hashes=seen_hashes,
                    seen_crossref_dois=seen_crossref_dois,
                )

    daily_counts = dict(current.daily_counts)
    for count_date, count in recovered_by_day.items():
        daily_counts[count_date] = daily_counts.get(count_date, 0) + count
    recovered_total = len(seen_candidates)
    return current.model_copy(
        update={
            "today_collected": current.today_collected + recovered_by_day.get(today, 0),
            "collected_total": current.collected_total + recovered_total,
            "daily_counts": _pruned_counts(daily_counts),
            "reconciled_through_at": local_now.isoformat(),
        }
    )