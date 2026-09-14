from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from basketball_miner.models import CandidateRecord, Checkpoint, RunCounters, SourceRecord
from basketball_miner.normalize import canonicalize_url, fingerprint, stable_candidate_id
from basketball_miner.relevance import classify_relevance
from basketball_miner.source_identity import (
    IdentityDecision,
    SourceIdentityResult,
    normalize_title,
)
from basketball_miner.sources.base import SourceAdapter

MAX_BUDGET = 20_000


class CandidateSink(Protocol):
    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]): ...


class SourceIdentityVerifier(Protocol):
    def verify(self, source: SourceRecord) -> SourceIdentityResult: ...


def _candidate_from_source(
    source: SourceRecord,
    topic_codes: tuple[str, ...],
    signals: tuple[str, ...],
    *,
    warnings: tuple[str, ...] = (),
    supersedes_candidate_id: str | None = None,
) -> CandidateRecord:
    candidate_id, canonical_hash = stable_candidate_id(source)
    return CandidateRecord(
        **source.model_dump(exclude={"url"}),
        url=canonicalize_url(str(source.url)),
        candidate_id=candidate_id,
        canonical_hash=canonical_hash,
        topic_codes=list(topic_codes),
        relevance_signals=list(signals),
        provenance="LINKED",
        warnings=list(warnings),
        supersedes_candidate_id=supersedes_candidate_id,
        discovered_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _mark_identity_counter(decision: IdentityDecision, counters: dict[str, int]) -> None:
    if decision is IdentityDecision.UNVERIFIED:
        counters["unverified"] += 1
        return
    if decision is IdentityDecision.NOT_APPLICABLE:
        return

    counters["verified"] += 1
    if decision is IdentityDecision.HIGH_CONFIDENCE_VARIANT:
        counters["variants"] += 1
    elif decision is IdentityDecision.AMBIGUOUS:
        counters["ambiguous"] += 1
    elif decision is IdentityDecision.MISMATCH:
        counters["mismatches"] += 1


def _add_collision_warning(candidate: CandidateRecord) -> bool:
    if "DOI_IDENTITY_COLLISION" in candidate.warnings:
        return False
    candidate.warnings.append("DOI_IDENTITY_COLLISION")
    return True


def _identity_signature(source: SourceRecord) -> str:
    authors = "|".join(normalize_title(author) for author in source.authors)
    year = ""
    if source.published_at and source.published_at[:4].isdigit():
        year = source.published_at[:4]
    return f"{normalize_title(source.title)}\n{authors}\n{year}"


def run_miner(
    adapters: list[SourceAdapter],
    sink: CandidateSink,
    budget: int = 500,
    *,
    checkpoints: dict[str, Checkpoint] | None = None,
    seen_hashes: set[str] | None = None,
    seen_crossref_dois: set[str] | None = None,
    chunk_size: int = 50,
    run_id: str | None = None,
    identity_verifier: SourceIdentityVerifier | None = None,
) -> RunCounters:
    if not 1 <= budget <= MAX_BUDGET:
        raise ValueError(f"budget must be between 1 and {MAX_BUDGET}")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    resolved_run_id = run_id or datetime.now(UTC).strftime("RUN-%Y%m%dT%H%M%S%fZ")
    checkpoint_store = checkpoints if checkpoints is not None else {}
    seen_store = seen_hashes if seen_hashes is not None else set()
    crossref_doi_store = seen_crossref_dois if seen_crossref_dois is not None else set()
    for adapter in adapters:
        checkpoint_store.setdefault(adapter.name, Checkpoint(adapter=adapter.name))

    inspected = 0
    duplicates = 0
    relevance_passed = 0
    exported = 0
    rate_limited = 0
    adapter_errors = 0
    identity_counts = {
        "verified": 0,
        "variants": 0,
        "ambiguous": 0,
        "mismatches": 0,
        "unverified": 0,
        "collisions": 0,
    }
    identity_signature_dois: dict[str, set[str]] = {}
    identity_candidates: dict[str, list[CandidateRecord]] = {}
    active = list(adapters)
    sequence = 0

    while active and inspected < budget:
        next_active: list[SourceAdapter] = []
        for adapter in active:
            if inspected >= budget:
                break

            request_limit = min(chunk_size, budget - inspected)
            current_checkpoint = checkpoint_store[adapter.name]
            batch = adapter.fetch(current_checkpoint, request_limit)
            adapter_errors += batch.error_count
            if batch.rate_limited:
                rate_limited += 1

            candidates: list[CandidateRecord] = []
            pending_hashes: set[str] = set()
            pending_crossref_dois: set[str] = set()
            for source in batch.records[:request_limit]:
                inspected += 1

                if source.adapter == "crossref" and identity_verifier is not None:
                    preliminary = classify_relevance(source)
                    if not preliminary.relevant:
                        continue
                    relevance_passed += 1

                    identity = identity_verifier.verify(source)
                    _mark_identity_counter(identity.decision, identity_counts)
                    final_source = identity.canonical_source or source
                    final_relevance = classify_relevance(final_source)
                    if not final_relevance.relevant:
                        continue

                    doi = final_source.stable_id.casefold().strip()
                    if doi in crossref_doi_store or doi in pending_crossref_dois:
                        duplicates += 1
                        continue

                    digest = fingerprint(final_source)
                    signature = _identity_signature(final_source)
                    known_dois = identity_signature_dois.get(signature, set())

                    candidate = _candidate_from_source(
                        final_source,
                        final_relevance.topic_codes,
                        final_relevance.signals,
                        warnings=identity.warnings,
                    )
                    if known_dois and final_source.stable_id not in known_dois:
                        for previous in identity_candidates.get(signature, []):
                            if _add_collision_warning(previous):
                                identity_counts["collisions"] += 1
                        if _add_collision_warning(candidate):
                            identity_counts["collisions"] += 1

                    identity_signature_dois.setdefault(signature, set()).add(
                        final_source.stable_id
                    )
                    identity_candidates.setdefault(signature, []).append(candidate)
                    pending_hashes.add(digest)
                    pending_crossref_dois.add(doi)
                    candidates.append(candidate)
                    continue

                digest = fingerprint(source)
                if digest in seen_store or digest in pending_hashes:
                    duplicates += 1
                    continue
                pending_hashes.add(digest)

                relevance = classify_relevance(source)
                if not relevance.relevant:
                    continue
                relevance_passed += 1
                candidates.append(
                    _candidate_from_source(source, relevance.topic_codes, relevance.signals)
                )

            if candidates:
                sequence += 1
                batch_id = f"{resolved_run_id}-{adapter.name}-{sequence:04d}"
                sink.write_batch(batch_id, candidates)
                exported += len(candidates)

            seen_store.update(pending_hashes)
            crossref_doi_store.update(pending_crossref_dois)
            checkpoint_store[adapter.name] = batch.next_checkpoint

            if not batch.rate_limited and len(batch.records) >= request_limit:
                next_active.append(adapter)

        active = next_active

    return RunCounters(
        inspected=inspected,
        duplicates=duplicates,
        relevance_passed=relevance_passed,
        exported=exported,
        rate_limited=rate_limited,
        adapter_errors=adapter_errors,
        identity_verified=identity_counts["verified"],
        identity_variants=identity_counts["variants"],
        identity_ambiguous=identity_counts["ambiguous"],
        identity_mismatches=identity_counts["mismatches"],
        identity_unverified=identity_counts["unverified"],
        identity_collisions=identity_counts["collisions"],
    )
