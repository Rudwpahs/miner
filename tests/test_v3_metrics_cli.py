import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from basketball_miner.distill_v3.metrics import (
    DailyMetrics,
    audit_due,
    check_release_invariants,
)
from basketball_miner.distill_v3.models import DailyAuditRecord

SEOUL = ZoneInfo("Asia/Seoul")
FIXTURES = Path(__file__).parent / "fixtures" / "v3"


def test_hard_release_invariants_are_machine_readable_and_ordered():
    metrics = DailyMetrics(
        date="2026-09-14",
        raw_to_training_bypass=1,
        raw_to_canonical_bypass=1,
        canonical_without_judge=1,
    )
    assert check_release_invariants(metrics) == [
        "raw_to_training_bypass",
        "raw_to_canonical_bypass",
        "canonical_without_judge",
    ]


def test_all_zero_hard_invariants_pass():
    assert check_release_invariants(DailyMetrics(date="2026-09-14")) == []


def test_backlog_counts_cannot_be_negative():
    with pytest.raises(ValidationError):
        DailyMetrics(date="2026-09-14", backlog_by_stage={"TRIAGE": -1})


def test_previous_seoul_day_is_due_without_audit_record():
    due = audit_due(
        now=datetime.fromisoformat("2026-09-14T01:10:00+09:00"),
        last_audit=None,
        timezone=SEOUL,
    )
    assert due is not None
    assert due.isoformat() == "2026-09-13"


def test_completed_previous_day_audit_satisfies_daily_accounting():
    due = audit_due(
        now=datetime.fromisoformat("2026-09-14T01:10:00+09:00"),
        last_audit=DailyAuditRecord(
            audit_date="2026-09-13",
            status="COMPLETED",
            recorded_at="2026-09-14T00:10:00+09:00",
        ),
        timezone=SEOUL,
    )
    assert due is None


def test_blocked_previous_day_audit_also_satisfies_daily_accounting():
    due = audit_due(
        now=datetime.fromisoformat("2026-09-14T01:10:00+09:00"),
        last_audit=DailyAuditRecord(
            audit_date="2026-09-13",
            status="BLOCKED",
            recorded_at="2026-09-14T00:10:00+09:00",
            reason_code="SOURCE_UNAVAILABLE",
        ),
        timezone=SEOUL,
    )
    assert due is None


def test_older_audit_does_not_hide_previous_day_due_date():
    due = audit_due(
        now=datetime.fromisoformat("2026-09-14T01:10:00+09:00"),
        last_audit=DailyAuditRecord(
            audit_date="2026-09-12",
            status="COMPLETED",
            recorded_at="2026-09-13T00:10:00+09:00",
        ),
        timezone=SEOUL,
    )
    assert due is not None
    assert due.isoformat() == "2026-09-13"


def test_v3_dry_run_is_repeatable_and_side_effect_free(tmp_path: Path):
    state_dir = tmp_path / "state"
    command = [
        sys.executable,
        "scripts/run_distill_v3.py",
        "--inbox-jsonl",
        str(FIXTURES / "inbox.jsonl"),
        "--state-dir",
        str(state_dir),
        "--batch-size",
        "2",
        "--dry-run",
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload == second_payload
    assert first_payload == {
        "duplicates": 1,
        "invalid_records": 0,
        "raw_records": 3,
        "status": "ok",
        "terminal_processed": 0,
        "triage_batches": 1,
        "triage_candidates": 2,
        "valid_candidates": 3,
        "would_write_state": False,
    }
    assert not state_dir.exists()


def test_v3_cli_refuses_persistent_mode(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_distill_v3.py",
            "--inbox-jsonl",
            str(FIXTURES / "inbox.jsonl"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--dry-run" in result.stderr
    assert not (tmp_path / "state").exists()
