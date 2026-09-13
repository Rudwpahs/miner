from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Stage = Literal["TRIAGE", "DEEP", "JUDGE", "REVIEW", "AUDIT"]
BatchStatus = Literal["PENDING", "CLAIMED", "COMPLETE", "FAILED"]
TriageDecision = Literal["REJECT", "DUPLICATE", "DEEP_PENDING"]
DeepDecision = Literal["PROPOSE_ACCEPT", "REVIEW", "REJECT"]
ReviewDecision = Literal["PROPOSE_ACCEPT", "REVIEW", "REJECT"]
JudgeDecision = Literal["CONFIRM", "REVIEW", "REJECT"]
ConceptAction = Literal["CREATE", "SUPPORT", "REFINE", "CONTRADICT"]
Route = Literal["TRIAGE", "DUPLICATE", "TERMINAL_INVALID", "TERMINAL_PROCESSED"]
AuditStatus = Literal["COMPLETED", "BLOCKED"]

_CANDIDATE_PATTERN = r"^CAND-[0-9a-f]{16}$"
_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
_BATCH_PATTERN = r"^V3-(TRIAGE|DEEP|JUDGE|REVIEW|AUDIT)-[0-9a-f]{12}$"
_CONCEPT_PATTERN = r"^CONCEPT-[0-9a-f]{12}$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class BatchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(pattern=_BATCH_PATTERN)
    stage: Stage
    candidate_ids: list[str] = Field(min_length=1, max_length=100)
    priority: int = Field(ge=0, le=100)
    created_at: str = Field(min_length=1)
    input_fingerprints: list[str] = Field(min_length=1, max_length=100)
    status: BatchStatus

    @model_validator(mode="after")
    def validate_members(self) -> "BatchRecord":
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids must be unique")
        if len(self.candidate_ids) != len(self.input_fingerprints):
            raise ValueError("candidate_ids and input_fingerprints length mismatch")
        for candidate_id in self.candidate_ids:
            if not __import__("re").fullmatch(_CANDIDATE_PATTERN, candidate_id):
                raise ValueError("malformed candidate_id")
        for digest in self.input_fingerprints:
            if not __import__("re").fullmatch(_FINGERPRINT_PATTERN, digest):
                raise ValueError("malformed input fingerprint")
        return self


class LeaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(pattern=_BATCH_PATTERN)
    worker: str = Field(min_length=1, max_length=120)
    claimed_at: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    attempt: int = Field(ge=1)


class CandidateStageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_PATTERN)
    source_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    stage: Stage
    status: BatchStatus
    batch_id: str | None = Field(default=None, pattern=_BATCH_PATTERN)
    attempt: int = Field(default=0, ge=0)
    updated_at: str = Field(min_length=1)


class SemanticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_PATTERN)
    stage: Literal["TRIAGE", "DEEP", "JUDGE", "REVIEW"]
    decision: str = Field(min_length=1, max_length=40)
    reason_code: str = Field(min_length=1, max_length=120)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    knowledge_unit_id: str | None = Field(default=None, min_length=1, max_length=160)
    concept_id: str | None = Field(default=None, pattern=_CONCEPT_PATTERN)
    concept_action: ConceptAction | None = None

    @model_validator(mode="after")
    def validate_stage_decision(self) -> "SemanticResult":
        allowed = {
            "TRIAGE": {"REJECT", "DUPLICATE", "DEEP_PENDING"},
            "DEEP": {"PROPOSE_ACCEPT", "REVIEW", "REJECT"},
            "JUDGE": {"CONFIRM", "REVIEW", "REJECT"},
            "REVIEW": {"PROPOSE_ACCEPT", "REVIEW", "REJECT"},
        }
        if self.decision not in allowed[self.stage]:
            raise ValueError(f"decision {self.decision!r} is illegal for stage {self.stage}")
        return self


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_PATTERN)
    route: Route
    reason_code: str = Field(min_length=1, max_length=120)
    priority: int = Field(ge=0, le=100)


class AuditPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_PATTERN)
    knowledge_unit_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(pattern=_CONCEPT_PATTERN)
    judge_decision: Literal["CONFIRM"]
    concept_action: ConceptAction
    canonical_date: str = Field(pattern=_DATE_PATTERN)


class DailyAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_date: str = Field(pattern=_DATE_PATTERN)
    status: AuditStatus
    recorded_at: str = Field(min_length=1)
    reason_code: str | None = Field(default=None, max_length=120)
