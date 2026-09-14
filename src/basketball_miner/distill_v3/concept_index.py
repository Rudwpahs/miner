from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ids import normalize_doi


class ConceptIndexRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_unit_id: str = Field(min_length=1, max_length=200)
    concept_id: str | None = Field(default=None, pattern=r"^CONCEPT-[0-9a-f]{12}$")
    normalized_source_id: str | None = None
    topic_codes: list[str] = Field(default_factory=list, max_length=32)
    claim_signature: str = ""
    status: Literal["ACCEPTED", "REVIEW"]


def claim_signature(claim: str) -> str:
    normalized = "".join(character if character.isalnum() else " " for character in claim.casefold())
    tokens = sorted({token for token in normalized.split() if len(token) >= 3})
    return " ".join(tokens[:32])


def _normalized_source_id(row: dict) -> str | None:
    for key in ("source_identifier", "source_url", "url"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        doi = normalize_doi(value)
        if doi is not None:
            return doi
        return value.strip().casefold()
    return None


def _normalized_topics(row: dict) -> list[str]:
    values = row.get("topic_codes", [])
    if not isinstance(values, list):
        return []
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def _useful_text(row: dict) -> str:
    for key in ("claim", "title", "source_title"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _row_identifier(row: dict, *, status: str, text: str, source_id: str | None) -> str:
    keys = ("knowledge_unit_id", "source_candidate_id", "candidate_id")
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if not text:
        raise ValueError("historical row lacks usable identifier and useful text")
    identity = f"{status}\n{source_id or ''}\n{text.casefold()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"LEGACY-INDEX-{digest[:12]}"


def _index_row(row: dict, *, status: Literal["ACCEPTED", "REVIEW"]) -> ConceptIndexRecord:
    text = _useful_text(row)
    source_id = _normalized_source_id(row)
    identifier = _row_identifier(row, status=status, text=text, source_id=source_id)
    concept_id = row.get("concept_id")
    if not isinstance(concept_id, str) or not concept_id.startswith("CONCEPT-"):
        concept_id = None
    return ConceptIndexRecord(
        knowledge_unit_id=identifier,
        concept_id=concept_id,
        normalized_source_id=source_id,
        topic_codes=_normalized_topics(row),
        claim_signature=claim_signature(text),
        status=status,
    )


def build_index(
    accepted_rows: list[dict],
    review_rows: list[dict],
) -> list[ConceptIndexRecord]:
    records = [
        *(_index_row(dict(row), status="ACCEPTED") for row in accepted_rows),
        *(_index_row(dict(row), status="REVIEW") for row in review_rows),
    ]
    return sorted(records, key=lambda record: (record.status, record.knowledge_unit_id))


def _normalize_query_source(source_id: str | None) -> str | None:
    if source_id is None or not source_id.strip():
        return None
    doi = normalize_doi(source_id)
    return doi if doi is not None else source_id.strip().casefold()


def shortlist(
    index: list[ConceptIndexRecord],
    *,
    source_id: str | None,
    topic_codes: list[str],
    claim: str,
    limit: int = 20,
) -> list[ConceptIndexRecord]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    query_source = _normalize_query_source(source_id)
    query_topics = {code.strip().upper() for code in topic_codes if code.strip()}
    query_tokens = set(claim_signature(claim).split())

    scored: list[tuple[int, ConceptIndexRecord]] = []
    for record in index:
        score = 0
        if query_source is not None and record.normalized_source_id == query_source:
            score += 100
        score += 10 * len(query_topics.intersection(record.topic_codes))
        record_tokens = set(record.claim_signature.split())
        score += min(10, len(query_tokens.intersection(record_tokens)))
        if score > 0:
            scored.append((score, record))

    scored.sort(key=lambda item: (-item[0], item[1].knowledge_unit_id))
    return [record for _, record in scored[:limit]]
