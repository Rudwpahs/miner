import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from basketball_miner.models import RunCounters

KST = ZoneInfo("Asia/Seoul")


def load_script():
    spec = importlib.util.spec_from_file_location("run_miner_script", Path("scripts/run_miner.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_state(root: Path, today: int, total: int = 200) -> None:
    root.mkdir(parents=True)
    current_date = datetime.now(KST).date().isoformat()
    (root / "crossref.json").write_text('{"adapter":"crossref"}\n', encoding="utf-8")
    (root / "youtube_rss.json").write_text('{"adapter":"youtube_rss"}\n', encoding="utf-8")
    (root / "seen_hashes.jsonl").write_text("", encoding="utf-8")
    (root / "seen_crossref_dois.jsonl").write_text("", encoding="utf-8")
    (root / "collection_stats.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "date": current_date,
                "today_collected": today,
                "collected_total": total,
                "daily_counts": {current_date: today},
                "last_miner_run_at": None,
            }
        ),
        encoding="utf-8",
    )


def write_target(path: Path, target: int) -> None:
    path.write_text(
        json.dumps({"daily_target": target, "timezone": "Asia/Seoul"}),
        encoding="utf-8",
    )


def patch_adapters(module, monkeypatch):
    monkeypatch.setattr(module, "CrossrefAdapter", lambda: object())
    monkeypatch.setattr(
        module,
        "YouTubeRssAdapter",
        lambda ids, *, legacy_users=(): (ids, legacy_users),
    )
    monkeypatch.setattr(module, "CrossrefIdentityVerifier", lambda: object())


def test_export_run_passes_only_remaining_quota(tmp_path, monkeypatch):
    module = load_script()
    state = tmp_path / "state"
    next_state = tmp_path / "next"
    target = tmp_path / "target.json"
    write_state(state, 91)
    write_target(target, 100)
    captured = {}
    monkeypatch.setattr(
        module,
        "run_miner",
        lambda *args, **kwargs: captured.update(kwargs) or RunCounters(exported=9),
    )
    monkeypatch.setattr(module, "ensure_private_repo", lambda *args, **kwargs: None)
    patch_adapters(module, monkeypatch)
    monkeypatch.setenv("HOOPHUB_MINER_TOKEN", "token")
    monkeypatch.setenv("HOOPHUB_TARGET_REPO", "Rudwpahs/hoopDB")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_miner.py",
            "--state-dir",
            str(state),
            "--next-state-dir",
            str(next_state),
            "--target-config",
            str(target),
        ],
    )
    assert module.main() == 0
    assert captured["max_exports"] == 9
    assert captured["budget"] == 20_000
    saved = json.loads((next_state / "collection_stats.json").read_text(encoding="utf-8"))
    assert saved["today_collected"] == 100


def test_until_target_removes_inspection_ceiling_and_uses_large_source_chunk(
    tmp_path, monkeypatch
):
    module = load_script()
    state = tmp_path / "state"
    next_state = tmp_path / "next"
    target = tmp_path / "target.json"
    write_state(state, 91)
    write_target(target, 100)
    captured = {}
    monkeypatch.setattr(
        module,
        "run_miner",
        lambda *args, **kwargs: captured.update(kwargs) or RunCounters(exported=9),
    )
    monkeypatch.setattr(module, "ensure_private_repo", lambda *args, **kwargs: None)
    patch_adapters(module, monkeypatch)
    monkeypatch.setenv("HOOPHUB_MINER_TOKEN", "token")
    monkeypatch.setenv("HOOPHUB_TARGET_REPO", "Rudwpahs/hoopDB")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_miner.py",
            "--state-dir",
            str(state),
            "--next-state-dir",
            str(next_state),
            "--target-config",
            str(target),
            "--until-target",
        ],
    )

    assert module.main() == 0
    assert captured["budget"] is None
    assert captured["chunk_size"] == 100
    assert captured["max_exports"] == 9


def test_build_adapters_includes_legacy_youtube_user(tmp_path, monkeypatch):
    module = load_script()
    config = tmp_path / "youtube.json"
    config.write_text(
        json.dumps(
            {
                "channels": [
                    {"channel_id": "UC0000000000000000000001", "label": "one"},
                    {"user": "TheHoopDoctors", "label": "legacy"},
                ]
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    class FakeYouTube:
        def __init__(self, ids, *, legacy_users=()):
            captured["ids"] = ids
            captured["legacy_users"] = legacy_users

    monkeypatch.setattr(module, "CrossrefAdapter", lambda: object())
    monkeypatch.setattr(module, "YouTubeRssAdapter", FakeYouTube)

    module._build_adapters(config)

    assert captured["ids"] == ("UC0000000000000000000001",)
    assert captured["legacy_users"] == ("TheHoopDoctors",)


def test_target_reached_does_not_call_run_miner_or_adapters(tmp_path, monkeypatch):
    module = load_script()
    state = tmp_path / "state"
    next_state = tmp_path / "next"
    target = tmp_path / "target.json"
    write_state(state, 100)
    write_target(target, 100)
    monkeypatch.setattr(
        module,
        "run_miner",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("run_miner called")),
    )
    monkeypatch.setattr(
        module,
        "CrossrefAdapter",
        lambda: (_ for _ in ()).throw(AssertionError("adapter created")),
    )
    monkeypatch.setattr(
        module,
        "CrossrefIdentityVerifier",
        lambda: (_ for _ in ()).throw(AssertionError("verifier created")),
    )
    monkeypatch.setattr(module, "ensure_private_repo", lambda *args, **kwargs: None)
    monkeypatch.setenv("HOOPHUB_MINER_TOKEN", "token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_miner.py",
            "--state-dir",
            str(state),
            "--next-state-dir",
            str(next_state),
            "--target-config",
            str(target),
        ],
    )
    assert module.main() == 0
    assert (next_state / "collection_stats.json").exists()


def test_no_export_dry_run_does_not_write_collection_stats(tmp_path, monkeypatch):
    module = load_script()
    state = tmp_path / "state"
    next_state = tmp_path / "next"
    write_state(state, 50)
    monkeypatch.setattr(module, "run_miner", lambda *args, **kwargs: RunCounters())
    patch_adapters(module, monkeypatch)
    monkeypatch.delenv("HOOPHUB_MINER_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_miner.py",
            "--state-dir",
            str(state),
            "--next-state-dir",
            str(next_state),
            "--no-export",
        ],
    )
    assert module.main() == 0
    assert not next_state.exists()
