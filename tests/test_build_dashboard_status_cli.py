import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load_script():
    path = Path("scripts/build_dashboard_status.py")
    spec = importlib.util.spec_from_file_location("build_dashboard_status_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_inputs(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_text('{"daily_target":1659,"timezone":"Asia/Seoul"}', encoding="utf-8")
    state = tmp_path / "collection_stats.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "date": "2026-09-19",
                "today_collected": 100,
                "collected_total": 2000,
                "daily_counts": {"2026-09-19": 100},
                "last_miner_run_at": "2026-09-19T18:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    return target, state


def test_cli_writes_only_validated_public_status(tmp_path, monkeypatch):
    module = load_script()
    target, state = write_inputs(tmp_path)
    output = tmp_path / "site" / "status.json"

    class FakeStore:
        pass

    monkeypatch.setattr(module, "GitHubV3Store", lambda *args, **kwargs: FakeStore())
    monkeypatch.setattr(
        module,
        "build_status_from_store",
        lambda config, stats, store, generated_at: module.PublicStatus(
            generated_at=generated_at,
            daily_target=config.daily_target,
            collected_total=stats.collected_total,
            distillation_pending=1843,
            distillation_success=2,
            today_collected=stats.today_collected,
            last_miner_run_at=stats.last_miner_run_at,
            last_distillation_success_at="2026-09-18T17:34:13Z",
            system_status="DISTILLING",
            history_7d=[],
        ),
    )
    monkeypatch.setenv("HOOPHUB_MINER_TOKEN", "token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_dashboard_status.py",
            "--target-config",
            str(target),
            "--collection-state",
            str(state),
            "--output",
            str(output),
        ],
    )
    assert module.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["distillation_pending"] == 1843
    assert set(payload) == set(module.PublicStatus.model_fields)


def test_cli_failure_does_not_replace_last_good_status(tmp_path, monkeypatch):
    module = load_script()
    target, state = write_inputs(tmp_path)
    output = tmp_path / "site" / "status.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"last_good":true}\n', encoding="utf-8")

    monkeypatch.setattr(module, "GitHubV3Store", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        module,
        "build_status_from_store",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("V3 ledger is missing")),
    )
    monkeypatch.setenv("HOOPHUB_MINER_TOKEN", "token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_dashboard_status.py",
            "--target-config",
            str(target),
            "--collection-state",
            str(state),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(RuntimeError, match="ledger"):
        module.main()
    assert output.read_text(encoding="utf-8") == '{"last_good":true}\n'
