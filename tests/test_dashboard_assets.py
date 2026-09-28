from pathlib import Path

ASSETS = ["dashboard/index.html", "dashboard/app.js", "dashboard/styles.css"]


def test_dashboard_has_status_page_information_architecture():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert "HoopHub Status" in html
    assert 'id="summary-status"' in html
    assert 'id="summary-title"' in html
    assert 'id="system-status-list"' in html
    assert 'id="miner-state"' in html
    assert 'id="distillation-state"' in html
    assert 'id="corpus-state"' in html
    assert 'id="incident-panel"' in html
    assert 'id="history-strip"' in html


def test_dashboard_has_component_metrics_and_nullable_targets():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="miner-today"' in html
    assert 'id="miner-total"' in html
    assert 'id="miner-last"' in html
    assert 'id="distillation-pending"' in html
    assert 'id="distillation-last"' in html
    assert 'id="corpus-total"' in html
    assert 'id="generated-at"' in html


def test_dashboard_uses_same_origin_status_only():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert 'fetch(`status.json?ts=${Date.now()}`' in js
    assert "api.github.com" not in js
    assert "HOOPHUB_MINER_TOKEN" not in js


def test_dashboard_renders_nullable_values_as_unavailable():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert 'return "확인 불가"' in js
    assert "status.distillation.pending" in js
    assert "status.corpus.accepted_total" in js


def test_dashboard_has_summary_and_incident_state_copy():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert "정상 운영 중" in js
    assert "일부 처리 지연" in js
    assert "일부 시스템 장애" in js
    assert "상태 갱신 지연" in js
    assert "STATE_UNAVAILABLE" in js


def test_refresh_failure_marks_stale_without_clearing_last_metrics():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert "function renderError" in js
    assert 'renderSummary("STALE")' in js
    assert 'textContent = "갱신 실패"' in js
    assert "replaceChildren()" not in js.split("function renderError", 1)[1].split("async function refresh", 1)[0]


def test_target_is_not_hardcoded_in_assets():
    text = "\n".join(Path(path).read_text(encoding="utf-8") for path in ASSETS)
    assert "1659" not in text


def test_public_assets_contain_no_private_candidate_fields():
    text = "\n".join(Path(path).read_text(encoding="utf-8") for path in ASSETS)
    for forbidden in ("candidate_id", "canonical_hash", "source_url", "HOOPHUB_MINER_TOKEN"):
        assert forbidden not in text


def test_styles_use_compact_light_status_page_layout():
    css = Path("dashboard/styles.css").read_text(encoding="utf-8")
    assert "max-width: 880px" in css
    assert ".status-row" in css
    assert ".summary-panel" in css
    assert ".status-dot" in css
    assert "radial-gradient" not in css
