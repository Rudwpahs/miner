from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from basketball_miner.collection_stats import (
    CollectionStats,
    apply_export,
    effective_daily_target,
    load_collection_stats,
    load_target_config,
    remaining_target,
)

KST = ZoneInfo("Asia/Seoul")


def test_target_config_loads_operator_value(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text('{"daily_target":1659,"timezone":"Asia/Seoul"}', encoding="utf-8")
    config = load_target_config(path)
    assert config.daily_target == 1659
    assert config.timezone == "Asia/Seoul"


def test_target_config_rejects_non_positive_target(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text('{"daily_target":0,"timezone":"Asia/Seoul"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_config(path)


def test_burst_target_applies_before_end_date(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text(
        '{"daily_target":1659,"timezone":"Asia/Seoul",'
        '"burst_daily_target":100000,"burst_end_date_exclusive":"2026-09-24"}',
        encoding="utf-8",
    )
    config = load_target_config(path)
    stats = CollectionStats(
        date="2026-09-23",
        today_collected=2000,
        collected_total=5000,
        daily_counts={"2026-09-23": 2000},
    )
    assert effective_daily_target(config, stats) == 100000
    assert remaining_target(config, stats) == 98000


def test_burst_target_expires_on_end_date(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text(
        '{"daily_target":1659,"timezone":"Asia/Seoul",'
        '"burst_daily_target":100000,"burst_end_date_exclusive":"2026-09-24"}',
        encoding="utf-8",
    )
    config = load_target_config(path)
    stats = CollectionStats(
        date="2026-09-24",
        today_collected=1600,
        collected_total=7000,
        daily_counts={"2026-09-24": 1600},
    )
    assert effective_daily_target(config, stats) == 1659
    assert remaining_target(config, stats) == 59


def test_burst_config_requires_target_and_end_date_together(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text(
        '{"daily_target":1659,"timezone":"Asia/Seoul","burst_daily_target":100000}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_target_config(path)


def test_remaining_target_never_goes_negative():
    config = load_target_config(Path("config/miner_target.json"))
    stats = CollectionStats(
        date="2026-09-19",
        today_collected=1700,
        collected_total=3000,
        daily_counts={"2026-09-19": 1700},
    )
    assert remaining_target(config, stats) == 0


def test_seoul_midnight_resets_today_only(tmp_path: Path):
    path = tmp_path / "collection_stats.json"
    path.write_text(
        '{"schema_version":1,"date":"2026-09-19","today_collected":1659,'
        '"collected_total":2400,"daily_counts":{"2026-09-19":1659},'
        '"last_miner_run_at":"2026-09-19T23:58:00+09:00"}',
        encoding="utf-8",
    )
    stats = load_collection_stats(path, datetime(2026, 9, 20, 0, 1, tzinfo=KST))
    assert stats.date == "2026-09-20"
    assert stats.today_collected == 0
    assert stats.collected_total == 2400
    assert stats.daily_counts["2026-09-19"] == 1659


def test_negative_persisted_count_is_rejected(tmp_path: Path):
    path = tmp_path / "collection_stats.json"
    path.write_text(
        '{"schema_version":1,"date":"2026-09-19","today_collected":-1,'
        '"collected_total":0,"daily_counts":{},"last_miner_run_at":null}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_collection_stats(path, datetime(2026, 9, 19, 12, 0, tzinfo=KST))


def test_apply_export_updates_and_prunes_history():
    stats = CollectionStats(
        date="2026-09-19",
        today_collected=1,
        collected_total=101,
        daily_counts={
            "2026-09-12": 10,
            "2026-09-13": 11,
            "2026-09-14": 12,
            "2026-09-15": 13,
            "2026-09-16": 14,
            "2026-09-17": 15,
            "2026-09-18": 16,
            "2026-09-19": 1,
        },
    )
    updated = apply_export(stats, 40, datetime(2026, 9, 19, 18, 0, tzinfo=KST))
    assert updated.today_collected == 41
    assert updated.collected_total == 141
    assert updated.daily_counts["2026-09-19"] == 41
    assert len(updated.daily_counts) == 7
    assert "2026-09-12" not in updated.daily_counts
    assert stats.today_collected == 1
