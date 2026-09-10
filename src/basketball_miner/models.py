from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

SourceType = Literal["academic", "official", "coaching", "interview"]
Provenance = Literal["LINKED"]


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = Field(min_length=1, max_length=80)
    source_type: SourceType
    stable_id: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list, max_length=32)
    published_at: str | None = None
    summary: str | None = Field(default=None, max_length=1200)


class CandidateRecord(SourceRecord):
    candidate_id: str = Field(pattern=r"^CAND-[0-9a-f]{16}$")
    canonical_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    topic_codes: list[str] = Field(default_factory=list, max_length=12)
    relevance_signals: list[str] = Field(default_factory=list, max_length=16)
    provenance: Provenance
    warnings: list[str] = Field(default_factory=list, max_length=16)
    discovered_at: str

    @field_validator("url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("candidate URL must use HTTPS")
        return value


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str = Field(min_length=1, max_length=80)
    last_checked_at: str | None = None
    cursor: str | None = None
    last_stable_id_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RunCounters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inspected: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    relevance_passed: int = Field(default=0, ge=0)
    exported: int = Field(default=0, ge=0)
    rate_limited: int = Field(default=0, ge=0)
    adapter_errors: int = Field(default=0, ge=0)
