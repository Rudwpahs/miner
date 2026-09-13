from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from basketball_miner.models import CandidateRecord

from .ids import normalized_source_key
from .models import CandidateStageState, RouteDecision

_BLOB_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CANDIDATE_ID_RE = re.compile(r"^CAND-[0-9a-f]{16}$")


class DistillLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processed_blob_shas: set[str] = Field(default_factory=set)
    processed_staging_shas: set[str] = Field(default_factory=set)
    completed_batch_ids: set[str] = Field(default_factory=set)
    parked_review_candidate_ids: set[str] = Field(default_factory=set)
    terminal_candidate_ids: set[str] = Field(default_factory=set)
    review_candidate_ids: set[str] = Field(default_factory=set)
    normalized_source_keys: set[str] = Field(default_factory=set)
    canonical_hashes: set[str] = Field(default_factory=set)
    candidate_states: dict[str, CandidateStageState] = Field(default_factory=dict)

    def mark_blob_processed(self, blob_sha: str) -> bool:
        normalized = blob_sha.strip().casefold()
        if not _BLOB_SHA_RE.fullmatch(normalized):
            raise ValueError("blob_sha must be a 40-character lowercase hex SHA")
        if normalized in self.processed_blob_shas:
            return False
        self.processed_blob_shas.add(normalized)
        return True

    def record_route(self, candidate: CandidateRecord, route: RouteDecision) -> None:
        if route.candidate_id != candidate.candidate_id:
            raise ValueError("route candidate_id does not match candidate")
        self.normalized_source_keys.add(normalized_source_key(candidate))
        self.canonical_hashes.add(candidate.canonical_hash)

        status = "PENDING" if route.route == "TRIAGE" else "COMPLETE"
        self.candidate_states[candidate.candidate_id] = CandidateStageState(
            candidate_id=candidate.candidate_id,
            source_fingerprint=candidate.canonical_hash,
            source_type=candidate.source_type,
            stage="TRIAGE",
            status=status,
            batch_id=None,
            attempt=0,
            updated_at=candidate.discovered_at,
        )
        if route.route in {"DUPLICATE", "TERMINAL_INVALID", "TERMINAL_PROCESSED"}:
            self.terminal_candidate_ids.add(candidate.candidate_id)


def _candidate_id_from_row(row: dict) -> str | None:
    for key in ("source_candidate_id", "candidate_id"):
        value = row.get(key)
        if isinstance(value, str) and _CANDIDATE_ID_RE.fullmatch(value):
            return value
    return None


def seed_from_v2_history(
    ledger: DistillLedger,
    *,
    accepted_rows: list[dict],
    review_rows: list[dict],
    manifests: list[dict],
) -> DistillLedger:
    seeded = ledger.model_copy(deep=True)
    generic_processed: set[str] = set()
    accepted_ids: set[str] = set()
    review_ids: set[str] = set()

    for manifest in manifests:
        for item in manifest.get("input_files", []):
            if not isinstance(item, dict):
                continue
            blob_sha = item.get("blob_sha")
            if isinstance(blob_sha, str) and _BLOB_SHA_RE.fullmatch(blob_sha.casefold()):
                seeded.processed_blob_shas.add(blob_sha.casefold())
        for candidate_id in manifest.get("processed_candidate_ids", []):
            if isinstance(candidate_id, str) and _CANDIDATE_ID_RE.fullmatch(candidate_id):
                generic_processed.add(candidate_id)

    for row in accepted_rows:
        candidate_id = _candidate_id_from_row(row)
        if candidate_id is not None:
            accepted_ids.add(candidate_id)

    for row in review_rows:
        candidate_id = _candidate_id_from_row(row)
        if candidate_id is not None:
            review_ids.add(candidate_id)

    current_review = review_ids - accepted_ids
    seeded.review_candidate_ids.update(current_review)
    seeded.review_candidate_ids.difference_update(accepted_ids)
    seeded.terminal_candidate_ids.update(generic_processed | accepted_ids)
    seeded.terminal_candidate_ids.difference_update(current_review)
    seeded.terminal_candidate_ids.update(accepted_ids)
    return seeded


def ledger_payload(ledger: DistillLedger) -> dict[str, object]:
    return {
        "processed_blob_shas": sorted(ledger.processed_blob_shas),
        "processed_staging_shas": sorted(ledger.processed_staging_shas),
        "completed_batch_ids": sorted(ledger.completed_batch_ids),
        "parked_review_candidate_ids": sorted(ledger.parked_review_candidate_ids),
        "terminal_candidate_ids": sorted(ledger.terminal_candidate_ids),
        "review_candidate_ids": sorted(ledger.review_candidate_ids),
        "normalized_source_keys": sorted(ledger.normalized_source_keys),
        "canonical_hashes": sorted(ledger.canonical_hashes),
        "candidate_states": {
            candidate_id: ledger.candidate_states[candidate_id].model_dump(mode="json")
            for candidate_id in sorted(ledger.candidate_states)
        },
    }
