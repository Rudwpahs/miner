from __future__ import annotations

from basketball_miner.models import CandidateRecord

from .ids import normalized_source_key
from .ledger import DistillLedger
from .models import RouteDecision
from .queue import priority_for


def route_candidate(candidate: CandidateRecord, ledger: DistillLedger) -> RouteDecision:
    priority = priority_for(candidate.source_type, "TRIAGE")

    if candidate.candidate_id in ledger.terminal_candidate_ids:
        return RouteDecision(
            candidate_id=candidate.candidate_id,
            route="TERMINAL_PROCESSED",
            reason_code="ALREADY_PROCESSED",
            priority=priority,
        )

    if candidate.candidate_id in ledger.review_candidate_ids:
        return RouteDecision(
            candidate_id=candidate.candidate_id,
            route="TERMINAL_PROCESSED",
            reason_code="ACTIVE_REVIEW",
            priority=100,
        )

    if normalized_source_key(candidate) in ledger.normalized_source_keys:
        return RouteDecision(
            candidate_id=candidate.candidate_id,
            route="DUPLICATE",
            reason_code="EXACT_SOURCE",
            priority=priority,
        )

    if candidate.canonical_hash in ledger.canonical_hashes:
        return RouteDecision(
            candidate_id=candidate.candidate_id,
            route="DUPLICATE",
            reason_code="EXACT_CANONICAL_HASH",
            priority=priority,
        )

    return RouteDecision(
        candidate_id=candidate.candidate_id,
        route="TRIAGE",
        reason_code="NEW_SEMANTIC_CANDIDATE",
        priority=priority,
    )
