from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from basketball_miner.collection_stats import (
    CollectionStats,
    MinerTargetConfig,
    effective_daily_target,
)
from basketball_miner.distill_v3.concept_index import ConceptIndexRecord
from basketball_miner.distill_v3.ledger import DistillLedger

ComponentStatus = Literal[
    "OPERATIONAL", "COLLECTING", "DELAYED", "DEGRADED", "UNAVAILABLE", "UNKNOWN"
]
SummaryStatus = Literal["OPERATIONAL", "DEGRADED", "PARTIAL_OUTAGE", "STALE"]
ReasonCode = Literal[
    "NONE", "STATE_UNAVAILABLE", "STATE_MALFORMED", "NO_RECENT_SUCCESS", "PAGE_STALE"
]

MINER_DELAY_AFTER = timedelta(minutes=60)
DISTILLATION_DELAY_AFTER = timedelta(hours=12)


class HistoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    collected: int = Field(ge=0)


class MinerComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ComponentStatus
    today_collected: int = Field(ge=0)
    daily_target: int = Field(gt=0)
    collected_total: int = Field(ge=0)
    last_success_at: str | None = None

    @field_validator("last_success_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        return _validate_timestamp(value)


class DistillationComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ComponentStatus
    pending: int | None = Field(default=None, ge=0)
    last_success_at: str | None = None
    reason: ReasonCode = "NONE"

    @field_validator("last_success_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        return _validate_timestamp(value)


class CorpusComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ComponentStatus
    accepted_total: int | None = Field(default=None, ge=0)


class PublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    generated_at: str
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    summary_status: SummaryStatus
    miner: MinerComponentStatus
    distillation: DistillationComponentStatus
    corpus: CorpusComponentStatus
    history_7d: list[HistoryPoint] = Field(default_factory=list, max_length=7)

    @field_validator("generated_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        validated = _validate_timestamp(value)
        assert validated is not None
        return validated


def _validate_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    return value


def _is_older_than(reference: str, value: str | None, delay: timedelta) -> bool:
    if value is None:
        return True
    reference_at = datetime.fromisoformat(reference)
    value_at = datetime.fromisoformat(value)
    return reference_at - value_at > delay


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


def _pending_count(ledger: DistillLedger) -> int:
    active_ids = {
        candidate_id
        for candidate_id, state in ledger.candidate_states.items()
        if state.status in {"PENDING", "CLAIMED"}
    }
    pending_ids = active_ids | set(ledger.parked_review_candidate_ids)
    return len(pending_ids)


def _accepted_count(concept_rows: list[ConceptIndexRecord]) -> int:
    accepted_ku_ids = {
        row.knowledge_unit_id for row in concept_rows if row.status == "ACCEPTED"
    }
    return len(accepted_ku_ids)


def _miner_component(
    config: MinerTargetConfig,
    collection_stats: CollectionStats,
    generated_at: str,
) -> MinerComponentStatus:
    active_target = effective_daily_target(config, collection_stats)
    if collection_stats.today_collected >= active_target:
        miner_status: ComponentStatus = "OPERATIONAL"
    elif _is_older_than(
        generated_at, collection_stats.last_miner_run_at, MINER_DELAY_AFTER
    ):
        miner_status = "DELAYED"
    else:
        miner_status = "COLLECTING"
    return MinerComponentStatus(
        status=miner_status,
        today_collected=collection_stats.today_collected,
        daily_target=active_target,
        collected_total=collection_stats.collected_total,
        last_success_at=collection_stats.last_miner_run_at,
    )


def _history(collection_stats: CollectionStats) -> list[HistoryPoint]:
    return [
        HistoryPoint(date=date, collected=collection_stats.daily_counts[date])
        for date in sorted(collection_stats.daily_counts)[-7:]
    ]


def _summary_status(
    miner: MinerComponentStatus,
    distillation: DistillationComponentStatus,
    corpus: CorpusComponentStatus,
) -> SummaryStatus:
    unavailable_states = {"UNAVAILABLE", "UNKNOWN"}
    degraded_states = {"DELAYED", "DEGRADED", "UNAVAILABLE", "UNKNOWN"}
    if miner.status in unavailable_states:
        return "PARTIAL_OUTAGE"
    if (
        miner.status in degraded_states
        or distillation.status in degraded_states
        or corpus.status in degraded_states
    ):
        return "DEGRADED"
    return "OPERATIONAL"


def build_public_status(
    config: MinerTargetConfig,
    collection_stats: CollectionStats,
    ledger: DistillLedger,
    concept_rows: list[ConceptIndexRecord],
    *,
    generated_at: str,
    last_success_at: str | None,
) -> PublicStatus:
    miner = _miner_component(config, collection_stats, generated_at)
    distillation = DistillationComponentStatus(
        status="OPERATIONAL",
        pending=_pending_count(ledger),
        last_success_at=last_success_at,
        reason="NONE",
    )
    corpus = CorpusComponentStatus(
        status="OPERATIONAL",
        accepted_total=_accepted_count(concept_rows),
    )
    status = PublicStatus(
        generated_at=generated_at,
        timezone=config.timezone,
        summary_status=_summary_status(miner, distillation, corpus),
        miner=miner,
        distillation=distillation,
        corpus=corpus,
        history_7d=_history(collection_stats),
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
    from basketball_miner.distill_v3.paths import concept_index_path, ledger_path

    miner = _miner_component(config, collection_stats, generated_at)

    ledger_remote = store.read_file(ledger_path())
    if ledger_remote is None or not ledger_remote.content.strip():
        distillation = DistillationComponentStatus(
            status="UNAVAILABLE",
            pending=None,
            last_success_at=None,
            reason="STATE_UNAVAILABLE",
        )
    else:
        try:
            ledger = DistillLedger.model_validate_json(ledger_remote.content)
        except ValueError:
            distillation = DistillationComponentStatus(
                status="UNAVAILABLE",
                pending=None,
                last_success_at=None,
                reason="STATE_MALFORMED",
            )
        else:
            pending = _pending_count(ledger)
            try:
                last_success_at = _last_distillation_success_at(store)
            except (RuntimeError, TypeError, ValueError):
                last_success_at = None
            if pending > 0 and _is_older_than(
                generated_at, last_success_at, DISTILLATION_DELAY_AFTER
            ):
                distillation = DistillationComponentStatus(
                    status="DELAYED",
                    pending=pending,
                    last_success_at=last_success_at,
                    reason="NO_RECENT_SUCCESS",
                )
            else:
                distillation = DistillationComponentStatus(
                    status="OPERATIONAL",
                    pending=pending,
                    last_success_at=last_success_at,
                    reason="NONE",
                )

    concept_remote = store.read_file(concept_index_path())
    if concept_remote is None:
        corpus = CorpusComponentStatus(status="UNAVAILABLE", accepted_total=None)
    else:
        try:
            concept_rows = parse_concept_index(concept_remote.content)
        except (TypeError, ValueError):
            corpus = CorpusComponentStatus(status="UNAVAILABLE", accepted_total=None)
        else:
            corpus = CorpusComponentStatus(
                status="OPERATIONAL",
                accepted_total=_accepted_count(concept_rows),
            )

    status = PublicStatus(
        generated_at=generated_at,
        timezone=config.timezone,
        summary_status=_summary_status(miner, distillation, corpus),
        miner=miner,
        distillation=distillation,
        corpus=corpus,
        history_7d=_history(collection_stats),
    )
    return validate_public_payload(status.model_dump(mode="json"))
