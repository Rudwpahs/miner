from pathlib import Path


ASSETS = ["dashboard/index.html", "dashboard/app.js", "dashboard/styles.css"]


def test_dashboard_has_three_headline_metrics():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="collected-total"' in html
    assert 'id="distillation-pending"' in html
    assert 'id="distillation-success"' in html


def test_dashboard_uses_same_origin_status_only():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert 'fetch(`status.json?ts=${Date.now()}`' in js
    assert "api.github.com" not in js
    assert "HOOPHUB_MINER_TOKEN" not in js


def test_target_is_not_hardcoded_in_assets():
    text = "\n".join(Path(path).read_text(encoding="utf-8") for path in ASSETS)
    assert "1659" not in text


def test_public_assets_contain_no_private_candidate_fields():
    text = "\n".join(Path(path).read_text(encoding="utf-8") for path in ASSETS)
    for forbidden in ("candidate_id", "canonical_hash", "source_url", "HOOPHUB_MINER_TOKEN"):
        assert forbidden not in text


def test_dashboard_has_visible_stale_and_generated_at_states():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert 'id="generated-at"' in html
    assert 'id="system-status"' in html
    assert "function renderError" in js
    assert "STALE" in js
