from __future__ import annotations

import re

V3_ROOT = "ml/coach/miner-data/v3"

_STAGE_SET = {"TRIAGE", "DEEP", "JUDGE", "REVIEW", "AUDIT"}
_BATCH_RE = re.compile(r"^V3-(TRIAGE|DEEP|JUDGE|REVIEW|AUDIT)-[0-9a-f]{12}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def assert_v3_write_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path:
        raise ValueError("invalid V3 write path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid V3 write path")
    if path != V3_ROOT and not path.startswith(f"{V3_ROOT}/"):
        raise ValueError("writes are restricted to the V3 root")
    return path


def _stage_dir(stage: str) -> str:
    normalized = stage.strip().upper()
    if normalized not in _STAGE_SET:
        raise ValueError(f"unsupported stage: {stage}")
    return normalized.lower()


def _date_parts(run_date: str) -> tuple[str, str, str]:
    match = _DATE_RE.fullmatch(run_date)
    if match is None:
        raise ValueError("date must use YYYY-MM-DD")
    return match.group(1), match.group(2), match.group(3)


def queue_path(stage: str, batch_id: str) -> str:
    normalized_stage = stage.strip().upper()
    stage_dir = _stage_dir(normalized_stage)
    match = _BATCH_RE.fullmatch(batch_id)
    if match is None or match.group(1) != normalized_stage:
        raise ValueError("batch_id stage must match queue stage")
    return assert_v3_write_path(f"{V3_ROOT}/queues/{stage_dir}/{batch_id}.json")


def lease_path(batch_id: str) -> str:
    if _BATCH_RE.fullmatch(batch_id) is None:
        raise ValueError("invalid batch_id")
    return assert_v3_write_path(f"{V3_ROOT}/leases/{batch_id}.json")


def staging_path(stage: str, run_date: str, run_id: str) -> str:
    stage_dir = _stage_dir(stage)
    year, month, day = _date_parts(run_date)
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("invalid run_id")
    return assert_v3_write_path(
        f"{V3_ROOT}/staging/{stage_dir}/{year}/{month}/{day}/{run_id}.jsonl"
    )


def ledger_path() -> str:
    return assert_v3_write_path(f"{V3_ROOT}/ledgers/distill.json")


def concept_index_path() -> str:
    return assert_v3_write_path(f"{V3_ROOT}/concepts/concept_index.jsonl")


def metrics_path(run_date: str) -> str:
    year, month, day = _date_parts(run_date)
    return assert_v3_write_path(f"{V3_ROOT}/metrics/{year}/{month}/{day}.json")
