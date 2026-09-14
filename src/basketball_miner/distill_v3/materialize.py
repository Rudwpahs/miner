from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from .github_store import GitHubV3Store
from .ledger import DistillLedger, ledger_payload
from .metrics import DailyMetrics, check_release_invariants
from .models import BatchRecord, SemanticResult, ShadowAuditResult, Stage
from .paths import V3_ROOT, ledger_path, metrics_path, queue_path
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
        raise TypeError("staging records must be a list")
    parsed: list[SemanticResult | ShadowAuditResult] = []
    for row in raw_records:
        if not isinstance(row, dict):
            raise TypeError("staging records must contain objects")
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
        ("REVIEW", "BLOCKED"): None,
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


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _read_required(store: GitHubV3Store, path: str):
    remote = store.read_file(path)
    if remote is None:
        raise RuntimeError(f"remote file disappeared during materialization: {path}")
    return remote


def _parse_staging_payload(content: bytes, *, path: str) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"remote staging is not UTF-8: {path}") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"remote staging must contain exactly one envelope line: {path}")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"remote staging contains invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"remote staging envelope must be an object: {path}")
    return payload


def discover_staging_blobs(store: GitHubV3Store) -> list[StagingBlob]:
    from .prepare import discover_remote_files

    paths = discover_remote_files(
        store,
        f"{V3_ROOT}/staging",
        suffixes=(".jsonl",),
    )
    blobs: list[StagingBlob] = []
    for path in paths:
        remote = _read_required(store, path)
        blobs.append(
            StagingBlob(
                path=path,
                sha=remote.sha,
                payload=_parse_staging_payload(remote.content, path=path),
            )
        )
    return blobs


def load_source_batches(
    store: GitHubV3Store,
    staging_blobs: list[StagingBlob],
) -> dict[str, BatchRecord]:
    batches: dict[str, BatchRecord] = {}
    for blob in sorted(staging_blobs, key=lambda item: (item.path, item.sha)):
        batch_id = blob.payload.get("batch_id")
        stage = blob.payload.get("stage")
        if not isinstance(batch_id, str) or not isinstance(stage, str):
            raise ValueError("staging batch_id and stage must be strings")
        path = queue_path(stage, batch_id)
        remote = _read_required(store, path)
        try:
            payload = json.loads(remote.content.decode("utf-8"))
            batch = BatchRecord.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeError(f"source queue is invalid: {path}") from exc
        existing = batches.get(batch.batch_id)
        if existing is not None and existing != batch:
            raise RuntimeError(f"source batch changed during materialization: {batch.batch_id}")
        batches[batch.batch_id] = batch
    return batches


def _load_remote_ledger(store: GitHubV3Store) -> tuple[DistillLedger, str | None]:
    remote = store.read_file(ledger_path())
    if remote is None:
        return DistillLedger(), None
    try:
        payload = json.loads(remote.content.decode("utf-8"))
        ledger = DistillLedger.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("existing V3 ledger is invalid") from exc
    return ledger, remote.sha


def _preflight_next_queues(
    store: GitHubV3Store,
    next_batches: tuple[BatchRecord, ...],
) -> tuple[dict[str, bytes], list[str]]:
    payloads: dict[str, bytes] = {}
    missing: list[str] = []
    for batch in next_batches:
        path = queue_path(batch.stage, batch.batch_id)
        content = _json_bytes(batch.model_dump(mode="json"))
        payloads[path] = content
        current = store.read_file(path)
        if current is None:
            missing.append(path)
        elif current.content != content:
            raise FileExistsError(f"immutable V3 path already exists with different bytes: {path}")
    return dict(sorted(payloads.items())), sorted(missing)


def run_remote_materialization(
    *,
    store: GitHubV3Store,
    run_date: str,
    created_at: str,
    write_shadow: bool,
) -> dict[str, object]:
    staging_blobs = discover_staging_blobs(store)
    source_batches = load_source_batches(store, staging_blobs)
    existing_ledger, ledger_sha = _load_remote_ledger(store)
    materialization = materialize_staging(
        staging_blobs=staging_blobs,
        source_batches=source_batches,
        existing_ledger=existing_ledger,
        run_date=run_date,
        created_at=created_at,
    )
    invariant_failures = check_release_invariants(materialization.metrics)
    if invariant_failures:
        raise RuntimeError(f"hard invariant failure: {','.join(invariant_failures)}")

    next_payloads, missing_paths = _preflight_next_queues(store, materialization.next_batches)
    metrics_remote = store.read_file(metrics_path(run_date))

    summary: dict[str, object] = {
        "staging_files_seen": materialization.metrics.staging_files_seen,
        "staging_files_materialized": materialization.metrics.staging_files_materialized,
        "batches_completed": materialization.metrics.batches_completed,
        "candidates_advanced": materialization.metrics.candidates_advanced,
        "next_batches_created_by_stage": dict(
            sorted(materialization.metrics.next_batches_created_by_stage.items())
        ),
        "next_batch_ids": [batch.batch_id for batch in materialization.next_batches],
        "next_batch_payloads": {
            path: content.decode("utf-8") for path, content in next_payloads.items()
        },
        "processed_staging_shas": list(materialization.processed_staging_shas),
        "completed_batch_ids": list(materialization.completed_batch_ids),
        "invariant_failures": invariant_failures,
        "write_enabled": write_shadow,
    }
    if not write_shadow:
        return summary

    latest_ledger = store.read_file(ledger_path())
    latest_sha = latest_ledger.sha if latest_ledger is not None else None
    if latest_sha != ledger_sha:
        raise RuntimeError("stale remote SHA")

    for path in missing_paths:
        store.create_immutable(
            path,
            next_payloads[path],
            f"distill-v3: stage {path.rsplit('/', 1)[-1].removesuffix('.json')}",
        )

    store.update_mutable(
        metrics_path(run_date),
        _json_bytes(materialization.metrics.model_dump(mode="json")),
        expected_sha=metrics_remote.sha if metrics_remote is not None else None,
        message=f"distill-v3: update materializer metrics {run_date}",
    )
    store.update_mutable(
        ledger_path(),
        _json_bytes(ledger_payload(materialization.ledger)),
        expected_sha=ledger_sha,
        message="distill-v3: materialize shadow staging",
    )
    return summary
