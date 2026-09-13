import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
MINE_WORKFLOW = ROOT / ".github" / "workflows" / "mine.yml"


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflows_do_not_use_pull_request_target():
    combined = _workflow_text(TEST_WORKFLOW) + _workflow_text(MINE_WORKFLOW)
    assert "pull_request_target" not in combined


def test_routine_ci_is_read_only_and_has_no_export_secret():
    text = _workflow_text(TEST_WORKFLOW)
    assert "pull_request:" in text
    assert "contents: read" in text
    assert "HOOPHUB_MINER_TOKEN" not in text
    assert "pytest -q" in text
    assert "ruff check src tests scripts" in text


def test_miner_runs_every_three_hours_without_pr_trigger():
    text = _workflow_text(MINE_WORKFLOW)
    assert 'cron: "17 */3 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "contents: read" in text
    assert "HOOPHUB_MINER_TOKEN" in text
    assert "contents: write" in text


def test_miner_uses_40x_inspection_budget_without_increasing_schedule_frequency():
    text = _workflow_text(MINE_WORKFLOW)
    assert text.count("--budget 20000") == 2
    assert "--budget 500" not in text
    assert 'cron: "17 */3 * * *"' in text


def test_all_external_actions_are_pinned_to_commit_shas():
    combined = _workflow_text(TEST_WORKFLOW) + _workflow_text(MINE_WORKFLOW)
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", combined, flags=re.MULTILINE)
    assert uses
    for action in uses:
        assert re.search(r"@[0-9a-f]{40}$", action), action


def test_no_shell_command_echoes_export_secret():
    text = _workflow_text(MINE_WORKFLOW)
    assert not re.search(r"(?im)^\s*run:.*(?:echo|printf).*HOOPHUB_MINER_TOKEN", text)


def test_public_workflows_have_no_self_hosted_gpu_execution():
    combined = _workflow_text(TEST_WORKFLOW) + _workflow_text(MINE_WORKFLOW)
    assert "self-hosted" not in combined
    assert "cuda" not in combined.casefold()
    assert "formquant" not in combined.casefold()
