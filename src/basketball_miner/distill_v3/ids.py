from __future__ import annotations

import hashlib
import re

from basketball_miner.models import CandidateRecord
from basketball_miner.normalize import canonicalize_url

from .models import Stage

_CANDIDATE_ID_RE = re.compile(r"^CAND-[0-9a-f]{16}$")
_VALID_STAGES = {"TRIAGE", "DEEP", "JUDGE", "REVIEW", "AUDIT"}


def normalize_doi(value: str) -> str | None:
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    if not normalized.startswith("10.") or "/" not in normalized:
        return None
    return normalized


def normalized_source_key(candidate: CandidateRecord) -> str:
    doi = normalize_doi(candidate.stable_id)
    if doi is None:
        doi = normalize_doi(str(candidate.url))
    if doi is not None:
        return f"doi:{doi}"

    canonical_url = canonicalize_url(str(candidate.url))
    identity = "\n".join(
        (
            candidate.adapter.strip().casefold(),
            candidate.stable_id.strip().casefold(),
            canonical_url,
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"source:{digest}"


def make_batch_id(stage: Stage, candidate_ids: list[str]) -> str:
    if stage not in _VALID_STAGES:
        raise ValueError(f"unsupported stage: {stage}")
    if not candidate_ids:
        raise ValueError("candidate_ids must not be empty")
    unique_ids = sorted(set(candidate_ids))
    for candidate_id in unique_ids:
        if not _CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise ValueError(f"malformed candidate_id: {candidate_id}")
    identity = f"{stage}\n" + "\n".join(unique_ids)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"V3-{stage}-{digest[:12]}"


def make_concept_id(topic_codes: list[str], claim_signature: str) -> str:
    topics = sorted({code.strip().upper() for code in topic_codes if code.strip()})
    signature = claim_signature.strip().casefold()
    if not topics:
        raise ValueError("topic_codes must contain at least one non-empty value")
    if not signature:
        raise ValueError("claim_signature must not be empty")
    identity = "|".join(topics) + "\n" + signature
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"CONCEPT-{digest[:12]}"
