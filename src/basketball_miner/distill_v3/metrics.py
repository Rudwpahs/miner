from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from .models import DailyAuditRecord


class DailyMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    raw_candidates: int = Field(default=0, ge=0)
    unique_candidates: int = Field(default=0, ge=0)
    exact_duplicates: int = Field(default=0, ge=0)
    invalid_records: int = Field(default=0, ge=0)
    triage_processed: int = Field(default=0, ge=0)
    triage_escalated: int = Field(default=0, ge=0)
    deep_processed: int = Field(default=0, ge=0)
    deep_proposed_accept: int = Field(default=0, ge=0)
    judge_confirm: int = Field(default=0, ge=0)
    judge_review: int = Field(default=0, ge=0)
    judge_reject: int = Field(default=0, ge=0)
    review_queue_size: int = Field(default=0, ge=0)
    review_resolved: int = Field(default=0, ge=0)
    concept_create: int = Field(default=0, ge=0)
    concept_support: int = Field(default=0, ge=0)
    concept_refine: int = Field(default=0, ge=0)
    concept_contradict: int = Field(default=0, ge=0)
    oldest_unprocessed_candidate_age_seconds: int = Field(default=0, ge=0)
    backlog_by_stage: dict[str, int] = Field(default_factory=dict)
    lease_retries: int = Field(default=0, ge=0)
    audit_duplicate_leakage: int = Field(default=0, ge=0)
    raw_to_training_bypass: int = Field(default=0, ge=0)
    raw_to_canonical_bypass: int = Field(default=0, ge=0)
    canonical_accepts: int = Field(default=0, ge=0)
    canonical_without_judge: int = Field(default=0, ge=0)
    simultaneous_valid_lease_conflicts: int = Field(default=0, ge=0)


_INVARIANT_FIELDS = (
    "raw_to_training_bypass",
    "raw_to_canonical_bypass",
    "canonical_without_judge",
    "simultaneous_valid_lease_conflicts",
    "audit_duplicate_leakage",
)


def check_release_invariants(metrics: DailyMetrics) -> list[str]:
    return [field for field in _INVARIANT_FIELDS if getattr(metrics, field) != 0]


def audit_due(
    *,
    now: datetime,
    last_audit: DailyAuditRecord | None,
    timezone: ZoneInfo,
) -> date | None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    previous_local_date = now.astimezone(timezone).date() - timedelta(days=1)
    if last_audit is not None:
        audited_date = date.fromisoformat(last_audit.audit_date)
        if audited_date == previous_local_date and last_audit.status in {"COMPLETED", "BLOCKED"}:
            return None
    return previous_local_date
