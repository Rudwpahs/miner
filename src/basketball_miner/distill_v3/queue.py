from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .ids import make_batch_id
from .models import BatchRecord, LeaseRecord, Stage

_SOURCE_BASE = {
    "official": 90,
    "academic": 85,
    "coaching": 65,
    "interview": 55,
}
_STAGE_BONUS = {
    "TRIAGE": 0,
    "DEEP": 3,
    "JUDGE": 5,
    "REVIEW": 10,
    "AUDIT": 10,
}


def priority_for(source_type: str, stage: Stage, *, is_review: bool = False) -> int:
    if is_review:
        return 100
    try:
        base = _SOURCE_BASE[source_type]
    except KeyError as exc:
        raise ValueError(f"unsupported source_type: {source_type}") from exc
    try:
        bonus = _STAGE_BONUS[stage]
    except KeyError as exc:
        raise ValueError(f"unsupported stage: {stage}") from exc
    return min(100, base + bonus)


def build_batches(
    stage: Stage,
    candidate_ids: list[str],
    fingerprints: dict[str, str],
    *,
    batch_size: int,
    priorities: dict[str, int],
    created_at: str,
) -> list[BatchRecord]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if not candidate_ids:
        return []
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate_ids must be unique")

    missing_fingerprints = sorted(set(candidate_ids) - fingerprints.keys())
    if missing_fingerprints:
        raise ValueError(f"missing fingerprint for {missing_fingerprints[0]}")
    missing_priorities = sorted(set(candidate_ids) - priorities.keys())
    if missing_priorities:
        raise ValueError(f"missing priority for {missing_priorities[0]}")

    ordered = sorted(candidate_ids, key=lambda candidate_id: (-priorities[candidate_id], candidate_id))
    batches: list[BatchRecord] = []
    for start in range(0, len(ordered), batch_size):
        members = ordered[start : start + batch_size]
        batches.append(
            BatchRecord(
                batch_id=make_batch_id(stage, members),
                stage=stage,
                candidate_ids=members,
                priority=max(priorities[candidate_id] for candidate_id in members),
                created_at=created_at,
                input_fingerprints=[fingerprints[candidate_id] for candidate_id in members],
                status="PENDING",
            )
        )
    return batches


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def lease_is_active(lease: LeaseRecord, now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC) < _parse_utc(lease.expires_at)


def claim_lease(
    batch: BatchRecord,
    existing: LeaseRecord | None,
    *,
    worker: str,
    now: datetime,
    ttl: timedelta,
) -> LeaseRecord:
    worker = worker.strip()
    if not worker:
        raise ValueError("worker must not be blank")
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    attempt = 1
    if existing is not None:
        if existing.batch_id != batch.batch_id:
            raise ValueError("existing lease belongs to a different batch")
        if lease_is_active(existing, now):
            raise ValueError("active lease already exists")
        attempt = existing.attempt + 1

    return LeaseRecord(
        batch_id=batch.batch_id,
        worker=worker,
        claimed_at=_format_utc(now),
        expires_at=_format_utc(now + ttl),
        attempt=attempt,
    )
