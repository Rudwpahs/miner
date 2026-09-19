from pathlib import Path


def test_miner_schedule_is_every_twenty_minutes():
    text = Path(".github/workflows/mine.yml").read_text(encoding="utf-8")
    assert 'cron: "7,27,47 * * * *"' in text
    assert "1659" not in text


def test_dashboard_schedule_is_every_five_minutes_and_reusable():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert 'cron: "*/5 * * * *"' in text
    assert "workflow_call:" in text


def test_status_build_precedes_pages_upload():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert text.index("build_dashboard_status.py") < text.index("upload-pages-artifact")


def test_dashboard_workflow_never_copies_private_candidate_tree_to_site():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert "ml/coach/miner-data/inbox" not in text
    assert "Rudwpahs/hoopDB" not in text
    assert "_site/status.json" in text


def test_source_workflows_refresh_dashboard_after_success():
    mine = Path(".github/workflows/mine.yml").read_text(encoding="utf-8")
    prepare = Path(".github/workflows/distill_v3_prepare.yml").read_text(encoding="utf-8")
    assert "refresh-dashboard:" in mine
    assert "needs: persist-state" in mine
    assert "refresh-dashboard:" in prepare
    assert "needs: prepare-shadow" in prepare
