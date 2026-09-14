from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.queue import build_batches
from basketball_miner.distill_v3.router import route_candidate
from basketball_miner.models import CandidateRecord


def _load_candidates(path: Path) -> tuple[int, int, list[CandidateRecord]]:
    raw_records = 0
    invalid_records = 0
    candidates: list[CandidateRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw_records += 1
        try:
            payload = json.loads(line)
            candidate = CandidateRecord.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError):
            invalid_records += 1
            continue
        candidates.append(candidate)
    return raw_records, invalid_records, candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run the deterministic Distillation V3 core")
    parser.add_argument("--inbox-jsonl", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        parser.error("persistent mode belongs to the storage integration plan; rerun with --dry-run")
    if not 1 <= args.batch_size <= 100:
        parser.error("--batch-size must be between 1 and 100")
    if not args.inbox_jsonl.is_file():
        parser.error("--inbox-jsonl must point to an existing file")

    raw_records, invalid_records, candidates = _load_candidates(args.inbox_jsonl)
    ledger = DistillLedger()
    duplicates = 0
    terminal_processed = 0
    triage_ids: list[str] = []
    fingerprints: dict[str, str] = {}
    priorities: dict[str, int] = {}

    for candidate in candidates:
        route = route_candidate(candidate, ledger)
        if route.route == "DUPLICATE":
            duplicates += 1
            ledger.record_route(candidate, route)
            continue
        if route.route in {"TERMINAL_INVALID", "TERMINAL_PROCESSED"}:
            terminal_processed += 1
            ledger.record_route(candidate, route)
            continue

        triage_ids.append(candidate.candidate_id)
        fingerprints[candidate.candidate_id] = candidate.canonical_hash
        priorities[candidate.candidate_id] = route.priority
        ledger.record_route(candidate, route)

    batches = build_batches(
        "TRIAGE",
        triage_ids,
        fingerprints,
        batch_size=args.batch_size,
        priorities=priorities,
        created_at="1970-01-01T00:00:00Z",
    )
    summary = {
        "status": "ok",
        "raw_records": raw_records,
        "valid_candidates": len(candidates),
        "invalid_records": invalid_records,
        "duplicates": duplicates,
        "terminal_processed": terminal_processed,
        "triage_candidates": len(triage_ids),
        "triage_batches": len(batches),
        "would_write_state": False,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
