import pytest

from basketball_miner.distill_v3.paths import (
    assert_v3_write_path,
    ledger_path,
    metrics_path,
    queue_path,
    staging_path,
)


def test_v3_write_guard_accepts_only_v3_root():
    path = "ml/coach/miner-data/v3/ledgers/distill.json"
    assert assert_v3_write_path(path) == path
    with pytest.raises(ValueError):
        assert_v3_write_path("ml/coach/miner-data/distilled/accepted/2026/09/14.jsonl")


def test_v3_write_guard_rejects_traversal_and_backslash():
    for path in (
        "ml/coach/miner-data/v3/../distilled/x",
        "ml/coach/miner-data/v3\\queues\\triage\\x.json",
        "/ml/coach/miner-data/v3/queues/triage/x.json",
    ):
        with pytest.raises(ValueError):
            assert_v3_write_path(path)


def test_queue_path_is_stage_scoped_and_rejects_bad_batch_id():
    assert queue_path("TRIAGE", "V3-TRIAGE-0123456789ab") == (
        "ml/coach/miner-data/v3/queues/triage/V3-TRIAGE-0123456789ab.json"
    )
    with pytest.raises(ValueError):
        queue_path("TRIAGE", "V3-DEEP-0123456789ab")
    with pytest.raises(ValueError):
        queue_path("TRIAGE", "../escape")


def test_partitioned_paths_use_year_month_day():
    assert staging_path("JUDGE", "2026-09-14", "RUN-abc123") == (
        "ml/coach/miner-data/v3/staging/judge/2026/09/14/RUN-abc123.jsonl"
    )
    assert metrics_path("2026-09-14") == "ml/coach/miner-data/v3/metrics/2026/09/14.json"
    assert ledger_path() == "ml/coach/miner-data/v3/ledgers/distill.json"


def test_staging_run_id_rejects_path_syntax():
    with pytest.raises(ValueError):
        staging_path("DEEP", "2026-09-14", "../RUN")
