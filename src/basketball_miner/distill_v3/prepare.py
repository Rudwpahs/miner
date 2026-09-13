from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from basketball_miner.models import CandidateRecord

from .concept_index import ConceptIndexRecord, build_index
from .ledger import DistillLedger, seed_from_v2_history
from .metrics import DailyMetrics
from .models import BatchRecord
from .queue import build_batches
from .router import route_candidate

_BLOB_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CANDIDATE_ID_RE = re.compile(r"^CAND-[0-9a-f]{16}$")

CandidateInput = CandidateRecord | dict[str, object]


@dataclass(frozen=True)
class InboxBlob:
    path: str
    sha: str
    candidates: tuple[CandidateInput, ...]

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("inbox blob path must not be blank")
        if not _BLOB_SHA_RE.fullmatch(self.sha):
            raise ValueError("inbox blob sha must be a 40-character lowercase hex SHA")


@dataclass(frozen=True)
class ShadowPreparation:
    ledger: DistillLedger
    concept_index: tuple[ConceptIndexRecord, ...]
    triage_batches: tuple[BatchRecord, ...]
    metrics: DailyMetrics
    processed_blob_shas: tuple[str, ...]


def _candidate_id_from_row(row: dict) -> str | None:
    for key in ("source_candidate_id", "candidate_id"):
        value = row.get(key)
        if isinstance(value, str) and _CANDIDATE_ID_RE.fullmatch(value):
            return value
    return None


def _active_review_rows(
    accepted_rows: list[dict],
    review_rows: list[dict],
) -> list[dict]:
    accepted_ids = {
        candidate_id
        for row in accepted_rows
        if (candidate_id := _candidate_id_from_row(row)) is not None
    }
    active: list[dict] = []
    for row in review_rows:
        candidate_id = _candidate_id_from_row(row)
        if candidate_id is not None and candidate_id in accepted_ids:
            continue
        active.append(dict(row))
    return active


def _validate_blob(blob: InboxBlob) -> tuple[list[CandidateRecord], int]:
    candidates: list[CandidateRecord] = []
    invalid_records = 0
    for row in blob.candidates:
        try:
            candidate = row if isinstance(row, CandidateRecord) else CandidateRecord.model_validate(row)
        except (ValidationError, TypeError, ValueError):
            invalid_records += 1
            continue
        candidates.append(candidate)
    return candidates, invalid_records


def prepare_shadow(
    *,
    inbox_blobs: list[InboxBlob],
    existing_ledger: DistillLedger | None,
    accepted_rows: list[dict],
    review_rows: list[dict],
    manifests: list[dict],
    run_date: str,
    created_at: str,
    triage_batch_size: int = 100,
) -> ShadowPreparation:
    if not 1 <= triage_batch_size <= 100:
        raise ValueError("triage_batch_size must be between 1 and 100")
    if not created_at.strip():
        raise ValueError("created_at must not be blank")

    base_ledger = existing_ledger.model_copy(deep=True) if existing_ledger is not None else DistillLedger()
    ledger = seed_from_v2_history(
        base_ledger,
        accepted_rows=accepted_rows,
        review_rows=review_rows,
        manifests=manifests,
    )
    active_reviews = _active_review_rows(accepted_rows, review_rows)
    concept_index = tuple(build_index(accepted_rows, active_reviews))

    new_processed_blobs: list[str] = []
    triage_candidates: list[str] = []
    fingerprints: dict[str, str] = {}
    priorities: dict[str, int] = {}

    raw_candidates = 0
    valid_candidates = 0
    invalid_records = 0
    exact_duplicates = 0

    for blob in sorted(inbox_blobs, key=lambda item: (item.path, item.sha)):
        if blob.sha in ledger.processed_blob_shas:
            continue

        raw_candidates += len(blob.candidates)
        validated, blob_invalid = _validate_blob(blob)
        if blob_invalid:
            invalid_records += blob_invalid
            continue

        blob_ledger = ledger.model_copy(deep=True)
        blob_triage: list[str] = []
        blob_fingerprints: dict[str, str] = {}
        blob_priorities: dict[str, int] = {}
        blob_duplicates = 0

        for candidate in validated:
            route = route_candidate(candidate, blob_ledger)
            blob_ledger.record_route(candidate, route)
            if route.route == "TRIAGE":
                blob_triage.append(candidate.candidate_id)
                blob_fingerprints[candidate.candidate_id] = candidate.canonical_hash
                blob_priorities[candidate.candidate_id] = route.priority
            elif route.route == "DUPLICATE":
                blob_duplicates += 1

        blob_ledger.mark_blob_processed(blob.sha)
        ledger = blob_ledger
        new_processed_blobs.append(blob.sha)
        valid_candidates += len(validated)
        exact_duplicates += blob_duplicates
        triage_candidates.extend(blob_triage)
        fingerprints.update(blob_fingerprints)
        priorities.update(blob_priorities)

    triage_batches = tuple(
        build_batches(
            "TRIAGE",
            triage_candidates,
            fingerprints,
            batch_size=triage_batch_size,
            priorities=priorities,
            created_at=created_at,
        )
    )
    for batch in triage_batches:
        for candidate_id in batch.candidate_ids:
            state = ledger.candidate_states[candidate_id]
            state.batch_id = batch.batch_id
            state.updated_at = created_at

    metrics = DailyMetrics(
        date=run_date,
        raw_candidates=raw_candidates,
        unique_candidates=valid_candidates,
        exact_duplicates=exact_duplicates,
        invalid_records=invalid_records,
        review_queue_size=len(ledger.review_candidate_ids),
        backlog_by_stage={
            "TRIAGE": len(triage_candidates),
            "REVIEW": len(ledger.review_candidate_ids),
        },
    )

    return ShadowPreparation(
        ledger=ledger,
        concept_index=concept_index,
        triage_batches=triage_batches,
        metrics=metrics,
        processed_blob_shas=tuple(sorted(new_processed_blobs)),
    )
