from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from .ledger import DistillLedger
from .metrics import DailyMetrics
from .models import BatchRecord, SemanticResult, ShadowAuditResult, Stage
from .queue import build_batches, priority_for

_STAGING_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_STAGE_BATCH_SIZE: dict[Stage, int] = {
    "TRIAGE": 100,
    "DEEP": 30,
    "JUDGE": 30,
    "REVIEW": 20,
    "AUDIT": 30,
}
_DESTINATION_ORDER: tuple[Stage, ...] = ("DEEP", "JUDGE", "REVIEW", "AUDIT")


@dataclass(frozen=True)
class StagingBlob:
    path: str
    sha: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("staging path must not be blank")
        if not _STAGING_SHA_RE.fullmatch(self.sha):
            raise ValueError("staging sha must be a 40-character lowercase hex SHA")


@dataclass(frozen=True)
class StageMaterialization:
    ledger: DistillLedger
    next_batches: tuple[BatchRecord, ...]
    processed_staging_shas: tuple[str, ...]
    completed_batch_ids: tuple[str, ...]
    metrics: DailyMetrics


def _parse_records(stage: Stage, payload: dict[str, object]) -> list[SemanticResult | ShadowAuditResult]:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("staging records must be a list")
    parsed: list[SemanticResult | ShadowAuditResult] = []
    for row in raw_records:
        if not isinstance(row, dict):
            raise ValueError("staging records must contain objects")
        try:
            parsed.append(
                ShadowAuditResult.model_validate(row)
                if stage == "AUDIT"
                else SemanticResult.model_validate(row)
            )
        except ValidationError as exc:
            raise ValueError("invalid staging record") from exc
    return parsed


def _validate_envelope(blob: StagingBlob, batch: BatchRecord) -> list[SemanticResult | ShadowAuditResult]:
    payload = blob.payload
    if payload.get("batch_id") != batch.batch_id:
        raise ValueError("staging batch_id does not match source batch")
    if payload.get("stage") != batch.stage:
        raise ValueError("staging stage does not match source batch")
    fingerprints = payload.get("input_fingerprints")
    if fingerprints != batch.input_fingerprints:
        raise ValueError("staging fingerprints do not match source batch")
    for required in ("run_id", "worker", "created_at"):
        value = payload.get(required)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"staging {required} must be a nonblank string")

    records = _parse_records(batch.stage, payload)
    record_ids = [record.candidate_id for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("staging candidate IDs must be unique")
    if set(record_ids) != set(batch.candidate_ids):
        raise ValueError("staging candidate set does not match source batch")
    if any(record.stage != batch.stage for record in records):
        raise ValueError("staging record stage does not match source batch")
    return records


def _destination(stage: Stage, decision: str) -> Stage | None:
    mapping: dict[tuple[Stage, str], Stage | None] = {
        ("TRIAGE", "REJECT"): None,
        ("TRIAGE", "DUPLICATE"): None,
        ("TRIAGE", "DEEP_PENDING"): "DEEP",
        ("DEEP", "PROPOSE_ACCEPT"): "JUDGE",
        ("DEEP", "REVIEW"): "REVIEW",
        ("DEEP", "REJECT"): None,
        ("JUDGE", "CONFIRM"): "AUDIT",
        ("JUDGE", "REVIEW"): "REVIEW",
        ("JUDGE", "REJECT"): None,
        ("REVIEW", "PROPOSE_ACCEPT"): "JUDGE",
        ("REVIEW", "REVIEW"): None,
        ("REVIEW", "REJECT"): None,
        ("AUDIT", "CREATE"): None,
        ("AUDIT", "SUPPORT"): None,
        ("AUDIT", "REFINE"): None,
        ("AUDIT", "CONTRADICT"): None,
        ("AUDIT", "REVIEW"): None,
        ("AUDIT", "BLOCKED"): None,
    }
    try:
        return mapping[(stage, decision)]
    except KeyError as exc:
        raise ValueError(f"illegal materializer transition: {stage}/{decision}") from exc


def _is_terminal(stage: Stage, decision: str) -> bool:
    return decision in {"REJECT", "DUPLICATE"} and stage != "AUDIT"


def _is_parked(stage: Stage, decision: str) -> bool:
    return (stage == "REVIEW" and decision in {"REVIEW", "BLOCKED"}) or (
        stage == "AUDIT" and decision in {"REVIEW", "BLOCKED"}
    )


def materialize_staging(
    *,
    staging_blobs: list[StagingBlob],
    source_batches: dict[str, BatchRecord],
    existing_ledger: DistillLedger,
    run_date: str,
    created_at: str,
) -> StageMaterialization:
    ledger = existing_ledger.model_copy(deep=True)
    processed: list[str] = []
    completed: list[str] = []
    destination_members: dict[Stage, list[str]] = {stage: [] for stage in _DESTINATION_ORDER}
    fingerprints: dict[str, str] = {}
    priorities: dict[str, int] = {}
    terminalized = 0
    parked = 0
    advanced = 0
    materialized_files = 0

    for blob in sorted(staging_blobs, key=lambda item: (item.path, item.sha)):
        if blob.sha in ledger.processed_staging_shas:
            continue
        batch_id = blob.payload.get("batch_id")
        if not isinstance(batch_id, str) or batch_id not in source_batches:
            raise ValueError("staging references unknown source batch")
        if batch_id in ledger.completed_batch_ids:
            raise ValueError("completed batch has a new unprocessed staging blob")
        batch = source_batches[batch_id]
        records = _validate_envelope(blob, batch)

        by_id = {record.candidate_id: record for record in records}
        for candidate_id, source_fingerprint in zip(
            batch.candidate_ids,
            batch.input_fingerprints,
            strict=True,
        ):
            state = ledger.candidate_states.get(candidate_id)
            if state is None:
                raise ValueError("candidate state is missing")
            if state.stage != batch.stage:
                raise ValueError("candidate state stage does not match source batch")
            if state.source_fingerprint != source_fingerprint:
                raise ValueError("candidate state fingerprint does not match source batch")

            record = by_id[candidate_id]
            destination = _destination(batch.stage, record.decision)
            if _is_terminal(batch.stage, record.decision):
                state.status = "COMPLETE"
                state.batch_id = batch.batch_id
                state.updated_at = created_at
                ledger.terminal_candidate_ids.add(candidate_id)
                ledger.review_candidate_ids.discard(candidate_id)
                ledger.parked_review_candidate_ids.discard(candidate_id)
                terminalized += 1
                continue

            if _is_parked(batch.stage, record.decision):
                state.stage = "REVIEW"
                state.status = "COMPLETE"
                state.batch_id = batch.batch_id
                state.updated_at = created_at
                ledger.review_candidate_ids.add(candidate_id)
                ledger.parked_review_candidate_ids.add(candidate_id)
                parked += 1
                continue

            if batch.stage == "AUDIT":
                state.status = "COMPLETE"
                state.batch_id = batch.batch_id
                state.updated_at = created_at
                continue

            if destination is None:
                raise ValueError("nonterminal transition is missing a destination")
            if state.source_type is None:
                raise ValueError("advancing candidate is missing source_type")
            state.stage = destination
            state.status = "PENDING"
            state.batch_id = None
            state.updated_at = created_at
            ledger.review_candidate_ids.discard(candidate_id)
            ledger.parked_review_candidate_ids.discard(candidate_id)
            if destination == "REVIEW":
                ledger.review_candidate_ids.add(candidate_id)
            destination_members[destination].append(candidate_id)
            fingerprints[candidate_id] = source_fingerprint
            priorities[candidate_id] = priority_for(
                state.source_type,
                destination,
                is_review=destination == "REVIEW",
            )
            advanced += 1

        ledger.processed_staging_shas.add(blob.sha)
        ledger.completed_batch_ids.add(batch.batch_id)
        processed.append(blob.sha)
        completed.append(batch.batch_id)
        materialized_files += 1

    next_batches: list[BatchRecord] = []
    counts_by_stage: dict[str, int] = {}
    for stage in _DESTINATION_ORDER:
        members = destination_members[stage]
        if not members:
            continue
        batches = build_batches(
            stage,
            members,
            fingerprints,
            batch_size=_STAGE_BATCH_SIZE[stage],
            priorities=priorities,
            created_at=created_at,
        )
        next_batches.extend(batches)
        counts_by_stage[stage] = len(batches)
        for batch in batches:
            for candidate_id in batch.candidate_ids:
                ledger.candidate_states[candidate_id].batch_id = batch.batch_id

    metrics = DailyMetrics(
        date=run_date,
        staging_files_seen=len(staging_blobs),
        staging_files_materialized=materialized_files,
        batches_completed=len(completed),
        candidates_advanced=advanced,
        terminalized_candidates=terminalized,
        parked_review_candidates=parked,
        next_batches_created_by_stage=counts_by_stage,
        review_queue_size=len(ledger.review_candidate_ids),
    )
    return StageMaterialization(
        ledger=ledger,
        next_batches=tuple(next_batches),
        processed_staging_shas=tuple(sorted(processed)),
        completed_batch_ids=tuple(sorted(completed)),
        metrics=metrics,
    )
