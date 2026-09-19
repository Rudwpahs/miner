import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
MINE_WORKFLOW = ROOT / ".github" / "workflows" / "mine.yml"
DISTILL_V3_WORKFLOW = ROOT / ".github" / "workflows" / "distill_v3_prepare.yml"
DASHBOARD_WORKFLOW = ROOT / ".github" / "workflows" / "dashboard.yml"
DISTILL_V3_SCRIPT = ROOT / "scripts" / "run_distill_v3_prepare.py"


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflows_do_not_use_pull_request_target():
    combined = (
        _workflow_text(TEST_WORKFLOW)
        + _workflow_text(MINE_WORKFLOW)
        + _workflow_text(DISTILL_V3_WORKFLOW)
        + _workflow_text(DASHBOARD_WORKFLOW)
    )
    assert "pull_request_target" not in combined


def test_routine_ci_is_read_only_and_has_no_export_secret():
    text = _workflow_text(TEST_WORKFLOW)
    assert "pull_request:" in text
    assert "contents: read" in text
    assert "HOOPHUB_MINER_TOKEN" not in text
    assert "pytest -q" in text
    assert "ruff check src tests scripts" in text


def test_miner_runs_every_twenty_minutes_without_pr_trigger():
    text = _workflow_text(MINE_WORKFLOW)
    assert 'cron: "7,27,47 * * * *"' in text
    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "contents: read" in text
    assert "HOOPHUB_MINER_TOKEN" in text
    assert "contents: write" in text


def test_miner_uses_40x_inspection_budget_with_quota_frequency():
    text = _workflow_text(MINE_WORKFLOW)
    assert text.count("--budget 20000") == 2
    assert "--budget 500" not in text
    assert 'cron: "7,27,47 * * * *"' in text
    assert "collection_stats.json" in text


def test_v3_shadow_workflow_runs_hourly_and_has_no_pr_trigger():
    text = _workflow_text(DISTILL_V3_WORKFLOW)
    assert 'cron: "42 * * * *"' in text
    assert "workflow_dispatch:" in text
    assert "write_shadow:" in text
    assert "default: false" in text
    assert "pull_request:" not in text
    assert "pull_request_target" not in text
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "timeout-minutes: 15" in text
    assert "group: formpath-distillation-v3-prepare" in text
    assert "cancel-in-progress: false" in text


def test_v3_shadow_workflow_is_cpu_only_and_targets_private_shadow_repo():
    text = _workflow_text(DISTILL_V3_WORKFLOW)
    assert "runs-on: ubuntu-latest" in text
    assert "self-hosted" not in text
    assert "cuda" not in text.casefold()
    assert "formquant" not in text.casefold()
    assert "qlora" not in text.casefold()
    assert "Rudwpahs/hoopDB" in text
    assert "--branch main" in text
    assert "scripts/run_distill_v3_prepare.py" in text
    assert "--dry-run" in text
    assert "--write-shadow" in text
    assert "HOOPHUB_MINER_TOKEN" in text


def test_v3_shadow_workflow_uses_combined_materialize_then_prepare_cycle():
    workflow = _workflow_text(DISTILL_V3_WORKFLOW)
    script = _workflow_text(DISTILL_V3_SCRIPT)
    assert workflow.count("scripts/run_distill_v3_prepare.py") == 2
    assert "run_remote_v3_cycle" in script
    assert "run_remote_shadow" not in script


def test_v3_shadow_workflow_checks_out_main_without_persisted_credentials():
    text = _workflow_text(DISTILL_V3_WORKFLOW)
    assert "ref: main" in text
    assert "persist-credentials: false" in text
    assert 'python-version: "3.12"' in text


def test_all_external_actions_are_pinned_to_commit_shas():
    combined = (
        _workflow_text(TEST_WORKFLOW)
        + _workflow_text(MINE_WORKFLOW)
        + _workflow_text(DISTILL_V3_WORKFLOW)
        + _workflow_text(DASHBOARD_WORKFLOW)
    )
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", combined, flags=re.MULTILINE)
    external = [action for action in uses if not action.startswith("./")]
    local = [action for action in uses if action.startswith("./")]
    assert external
    for action in external:
        assert re.search(r"@[0-9a-f]{40}$", action), action
    assert set(local) <= {"./.github/workflows/dashboard.yml"}


def test_no_shell_command_echoes_export_secret():
    combined = (
        _workflow_text(MINE_WORKFLOW)
        + _workflow_text(DISTILL_V3_WORKFLOW)
        + _workflow_text(DASHBOARD_WORKFLOW)
    )
    assert not re.search(r"(?im)^\s*run:.*(?:echo|printf).*HOOPHUB_MINER_TOKEN", combined)


def test_public_workflows_have_no_self_hosted_gpu_execution():
    combined = (
        _workflow_text(TEST_WORKFLOW)
        + _workflow_text(MINE_WORKFLOW)
        + _workflow_text(DISTILL_V3_WORKFLOW)
        + _workflow_text(DASHBOARD_WORKFLOW)
    )
    assert "self-hosted" not in combined
    assert "cuda" not in combined.casefold()
    assert "formquant" not in combined.casefold()
    assert "qlora" not in combined.casefold()
