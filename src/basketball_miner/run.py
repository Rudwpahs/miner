from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from basketball_miner.models import CandidateRecord, Checkpoint, RunCounters
from basketball_miner.normalize import canonicalize_url, fingerprint, stable_candidate_id
from basketball_miner.relevance import classify_relevance
from basketball_miner.sources.base import SourceAdapter


class CandidateSink(Protocol):
    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]): ...


def _candidate_from_source(source, topic_codes: tuple[str, ...], signals: tuple[str, ...]):
    candidate_id, canonical_hash = stable_candidate_id(source)
    return CandidateRecord(
        **source.model_dump(exclude={"url"}),
        url=canonicalize_url(str(source.url)),
        candidate_id=candidate_id,
        canonical_hash=canonical_hash,
        topic_codes=list(topic_codes),
        relevance_signals=list(signals),
        provenance="LINKED",
        warnings=[],
        discovered_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def run_miner(
    adapters: list[SourceAdapter],
    sink: CandidateSink,
    budget: int = 500,
    *,
    checkpoints: dict[str, Checkpoint] | None = None,
    seen_hashes: set[str] | None = None,
    chunk_size: int = 50,
) -> RunCounters:
    if not 1 <= budget <= 500:
        raise ValueError("budget must be between 1 and 500")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    checkpoint_store = checkpoints if checkpoints is not None else {}
    seen_store = seen_hashes if seen_hashes is not None else set()
    for adapter in adapters:
        checkpoint_store.setdefault(adapter.name, Checkpoint(adapter=adapter.name))

    inspected = 0
    duplicates = 0
    relevance_passed = 0
    exported = 0
    rate_limited = 0
    adapter_errors = 0
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
            for source in batch.records[:request_limit]:
                inspected += 1
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
                batch_id = f"MINER-{adapter.name}-{sequence:04d}"
                sink.write_batch(batch_id, candidates)
                exported += len(candidates)

            seen_store.update(pending_hashes)
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
    )
