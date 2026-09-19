from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from basketball_miner.collection_stats import CollectionStats, MinerTargetConfig
from basketball_miner.distill_v3.concept_index import ConceptIndexRecord
from basketball_miner.distill_v3.ledger import DistillLedger


class HistoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    collected: int = Field(ge=0)


class PublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    generated_at: str
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    daily_target: int = Field(gt=0)
    collected_total: int = Field(ge=0)
    distillation_pending: int = Field(ge=0)
    distillation_success: int = Field(ge=0)
    today_collected: int = Field(ge=0)
    last_miner_run_at: str | None = None
    last_distillation_success_at: str | None = None
    system_status: Literal[
        "COLLECTING", "TARGET_REACHED", "DISTILLING", "BLOCKED", "DEGRADED"
    ]
    history_7d: list[HistoryPoint] = Field(default_factory=list, max_length=7)

    @field_validator("generated_at", "last_miner_run_at", "last_distillation_success_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamp must be ISO-8601") from exc
        return value


def parse_concept_index(content: bytes) -> list[ConceptIndexRecord]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("concept index is not UTF-8") from exc
    rows: list[ConceptIndexRecord] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("concept index contains invalid JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("concept index rows must be objects")
        rows.append(ConceptIndexRecord.model_validate(payload))
    return rows


def build_public_status(
    config: MinerTargetConfig,
    collection_stats: CollectionStats,
    ledger: DistillLedger,
    concept_rows: list[ConceptIndexRecord],
    *,
    generated_at: str,
    last_success_at: str | None,
) -> PublicStatus:
    active_ids = {
        candidate_id
        for candidate_id, state in ledger.candidate_states.items()
        if state.status in {"PENDING", "CLAIMED"}
    }
    pending_ids = active_ids | set(ledger.parked_review_candidate_ids)
    accepted_ku_ids = {
        row.knowledge_unit_id for row in concept_rows if row.status == "ACCEPTED"
    }

    if collection_stats.today_collected >= config.daily_target:
        system_status = "TARGET_REACHED"
    elif pending_ids:
        system_status = "DISTILLING"
    else:
        system_status = "COLLECTING"

    history = [
        HistoryPoint(date=date, collected=collection_stats.daily_counts[date])
        for date in sorted(collection_stats.daily_counts)[-7:]
    ]

    status = PublicStatus(
        generated_at=generated_at,
        timezone=config.timezone,
        daily_target=config.daily_target,
        collected_total=collection_stats.collected_total,
        distillation_pending=len(pending_ids),
        distillation_success=len(accepted_ku_ids),
        today_collected=collection_stats.today_collected,
        last_miner_run_at=collection_stats.last_miner_run_at,
        last_distillation_success_at=last_success_at,
        system_status=system_status,
        history_7d=history,
    )
    return validate_public_payload(status.model_dump(mode="json"))


def validate_public_payload(payload: dict) -> PublicStatus:
    status = PublicStatus.model_validate(payload)
    rendered = json.dumps(status.model_dump(mode="json"), sort_keys=True)
    forbidden_fragments = (
        "CAND-",
        "canonical_hash",
        "candidate_id",
        "batch_id",
        "knowledge_unit_id",
        "https://",
        "http://",
    )
    if any(fragment in rendered for fragment in forbidden_fragments):
        raise ValueError("public status contains private candidate material")
    return status


def _last_distillation_success_at(store) -> str | None:
    from basketball_miner.distill_v3.paths import V3_ROOT

    runs_root = f"{V3_ROOT}/runs"
    years = sorted(
        (entry for entry in store.list_dir(runs_root) if entry.type == "dir"),
        key=lambda entry: entry.name,
        reverse=True,
    )
    for year in years:
        months = sorted(
            (entry for entry in store.list_dir(year.path) if entry.type == "dir"),
            key=lambda entry: entry.name,
            reverse=True,
        )
        for month in months:
            days = sorted(
                (entry for entry in store.list_dir(month.path) if entry.type == "dir"),
                key=lambda entry: entry.name,
                reverse=True,
            )
            for day in days:
                successes: list[str] = []
                for entry in store.list_dir(day.path):
                    if entry.type != "file" or not entry.name.endswith(".json"):
                        continue
                    remote = store.read_file(entry.path)
                    if remote is None:
                        raise RuntimeError(f"run file disappeared: {entry.path}")
                    try:
                        payload = json.loads(remote.content.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"invalid V3 run record: {entry.path}") from exc
                    if not isinstance(payload, dict):
                        raise TypeError(f"invalid V3 run record: {entry.path}")
                    if payload.get("stage") != "AUDIT" or payload.get("status") != "COMPLETED":
                        continue
                    counts = payload.get("decision_counts")
                    if not isinstance(counts, dict):
                        raise TypeError(f"AUDIT run missing decision_counts: {entry.path}")
                    promoted = sum(
                        int(counts.get(action, 0))
                        for action in ("CREATE", "SUPPORT", "REFINE", "CONTRADICT")
                    )
                    if promoted <= 0:
                        continue
                    created_at = payload.get("created_at")
                    if not isinstance(created_at, str):
                        raise TypeError(f"AUDIT run missing created_at: {entry.path}")
                    try:
                        datetime.fromisoformat(created_at)
                    except ValueError as exc:
                        raise ValueError(f"AUDIT run has invalid created_at: {entry.path}") from exc
                    successes.append(created_at)
                if successes:
                    return max(successes)
    return None


def build_status_from_store(
    config: MinerTargetConfig,
    collection_stats: CollectionStats,
    store,
    *,
    generated_at: str,
) -> PublicStatus:
    from basketball_miner.distill_v3.ledger import DistillLedger
    from basketball_miner.distill_v3.paths import concept_index_path, ledger_path

    ledger_remote = store.read_file(ledger_path())
    if ledger_remote is None:
        raise RuntimeError("V3 ledger is missing")
    concept_remote = store.read_file(concept_index_path())
    if concept_remote is None:
        raise RuntimeError("V3 concept index is missing")
    try:
        ledger = DistillLedger.model_validate_json(ledger_remote.content)
    except ValueError as exc:
        raise ValueError("V3 ledger is malformed") from exc
    concept_rows = parse_concept_index(concept_remote.content)
    last_success_at = _last_distillation_success_at(store)
    return build_public_status(
        config,
        collection_stats,
        ledger,
        concept_rows,
        generated_at=generated_at,
        last_success_at=last_success_at,
    )
