from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from basketball_miner.models import CandidateRecord

from .concept_index import ConceptIndexRecord, build_index
from .github_store import GitHubV3Store
from .ledger import DistillLedger, ledger_payload, seed_from_v2_history
from .metrics import DailyMetrics, check_release_invariants
from .models import BatchRecord
from .paths import concept_index_path, ledger_path, metrics_path, queue_path
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


def discover_remote_files(
    store: GitHubV3Store,
    root: str,
    *,
    suffixes: tuple[str, ...],
) -> list[str]:
    if not suffixes or any(not suffix for suffix in suffixes):
        raise ValueError("suffixes must not be empty")
    found: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in store.list_dir(directory):
            if entry.type == "dir":
                pending.append(entry.path)
            elif entry.path.endswith(suffixes):
                found.append(entry.path)
    return sorted(set(found))


def _jsonl_rows(content: bytes, *, tolerate_invalid: bool) -> list[dict]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        if tolerate_invalid:
            return [{"__invalid_utf8__": True}]
        raise RuntimeError("remote JSONL is not UTF-8") from exc

    rows: list[dict] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            if tolerate_invalid:
                rows.append({"__invalid_json_line__": line_number})
                continue
            raise RuntimeError(f"remote JSONL contains invalid JSON at line {line_number}") from exc
        if not isinstance(row, dict):
            if tolerate_invalid:
                rows.append({"__invalid_json_type__": line_number})
                continue
            raise RuntimeError(f"remote JSONL row {line_number} is not an object")
        rows.append(row)
    return rows


def _read_required(store: GitHubV3Store, path: str):
    remote = store.read_file(path)
    if remote is None:
        raise RuntimeError(f"remote file disappeared during preparation: {path}")
    return remote


def load_inbox_blobs(store: GitHubV3Store) -> list[InboxBlob]:
    paths = discover_remote_files(
        store,
        "ml/coach/miner-data/inbox",
        suffixes=(".jsonl",),
    )
    blobs: list[InboxBlob] = []
    for path in paths:
        remote = _read_required(store, path)
        blobs.append(
            InboxBlob(
                path=path,
                sha=remote.sha,
                candidates=tuple(_jsonl_rows(remote.content, tolerate_invalid=True)),
            )
        )
    return blobs


def _load_jsonl_tree(store: GitHubV3Store, root: str) -> list[dict]:
    rows: list[dict] = []
    for path in discover_remote_files(store, root, suffixes=(".jsonl",)):
        remote = _read_required(store, path)
        rows.extend(_jsonl_rows(remote.content, tolerate_invalid=False))
    return rows


def _load_json_tree(store: GitHubV3Store, root: str) -> list[dict]:
    rows: list[dict] = []
    for path in discover_remote_files(store, root, suffixes=(".json",)):
        remote = _read_required(store, path)
        try:
            value = json.loads(remote.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"remote JSON is invalid: {path}") from exc
        if isinstance(value, dict):
            rows.append(value)
        elif isinstance(value, list) and all(isinstance(item, dict) for item in value):
            rows.extend(value)
        else:
            raise RuntimeError(f"remote JSON must contain object data: {path}")
    return rows


def load_v2_history(store: GitHubV3Store) -> tuple[list[dict], list[dict], list[dict]]:
    accepted = _load_jsonl_tree(store, "ml/coach/miner-data/distilled/accepted")
    review = _load_jsonl_tree(store, "ml/coach/miner-data/distilled/review")
    manifests = _load_json_tree(store, "ml/coach/miner-data/distilled/manifests")
    return accepted, review, manifests


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _load_existing_ledger(store: GitHubV3Store) -> tuple[DistillLedger | None, str | None]:
    remote = store.read_file(ledger_path())
    if remote is None:
        return None, None
    try:
        payload = json.loads(remote.content.decode("utf-8"))
        ledger = DistillLedger.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("existing V3 ledger is invalid") from exc
    return ledger, remote.sha


def _persist_shadow(
    *,
    store: GitHubV3Store,
    preparation: ShadowPreparation,
    ledger_sha: str | None,
    run_date: str,
) -> None:
    failures = check_release_invariants(preparation.metrics)
    if failures:
        raise RuntimeError(f"hard invariant failure: {','.join(failures)}")

    index_remote = store.read_file(concept_index_path())
    metrics_remote = store.read_file(metrics_path(run_date))

    for batch in preparation.triage_batches:
        path = queue_path("TRIAGE", batch.batch_id)
        store.create_immutable(
            path,
            _json_bytes(batch.model_dump(mode="json")),
            f"distill-v3: stage {batch.batch_id}",
        )

    store.update_mutable(
        concept_index_path(),
        _jsonl_bytes([row.model_dump(mode="json") for row in preparation.concept_index]),
        expected_sha=index_remote.sha if index_remote is not None else None,
        message="distill-v3: update concept index",
    )
    store.update_mutable(
        metrics_path(run_date),
        _json_bytes(preparation.metrics.model_dump(mode="json")),
        expected_sha=metrics_remote.sha if metrics_remote is not None else None,
        message=f"distill-v3: update metrics {run_date}",
    )
    store.update_mutable(
        ledger_path(),
        _json_bytes(ledger_payload(preparation.ledger)),
        expected_sha=ledger_sha,
        message="distill-v3: advance shadow ledger",
    )


def run_remote_shadow(
    *,
    store: GitHubV3Store,
    run_date: str,
    created_at: str,
    batch_size: int,
    write_shadow: bool,
) -> dict[str, object]:
    inbox_blobs = load_inbox_blobs(store)
    accepted_rows, review_rows, manifests = load_v2_history(store)
    existing_ledger, ledger_sha = _load_existing_ledger(store)
    preparation = prepare_shadow(
        inbox_blobs=inbox_blobs,
        existing_ledger=existing_ledger,
        accepted_rows=accepted_rows,
        review_rows=review_rows,
        manifests=manifests,
        run_date=run_date,
        created_at=created_at,
        triage_batch_size=batch_size,
    )
    invariant_failures = check_release_invariants(preparation.metrics)
    summary: dict[str, object] = {
        "new_inbox_blobs": len(preparation.processed_blob_shas),
        "new_candidates": preparation.metrics.unique_candidates,
        "duplicates": preparation.metrics.exact_duplicates,
        "triage_candidates": sum(len(batch.candidate_ids) for batch in preparation.triage_batches),
        "triage_batches": len(preparation.triage_batches),
        "review_seeded": len(preparation.ledger.review_candidate_ids),
        "invariant_failures": invariant_failures,
        "write_enabled": write_shadow,
    }
    if invariant_failures:
        raise RuntimeError(f"hard invariant failure: {','.join(invariant_failures)}")
    if write_shadow:
        _persist_shadow(
            store=store,
            preparation=preparation,
            ledger_sha=ledger_sha,
            run_date=run_date,
        )
    return summary
