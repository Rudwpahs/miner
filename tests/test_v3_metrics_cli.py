from datetime import datetime
from zoneinfo import ZoneInfo

from basketball_miner.distill_v3.metrics import (
    DailyMetrics,
    audit_due,
    check_release_invariants,
)
from basketball_miner.distill_v3.models import DailyAuditRecord

SEOUL = ZoneInfo("Asia/Seoul")


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
