import json
from zoneinfo import ZoneInfo

import pytest

from basketball_miner.collection_stats import CollectionStats, MinerTargetConfig
from basketball_miner.dashboard_status import (
    build_public_status,
    parse_concept_index,
    validate_public_payload,
)
from basketball_miner.distill_v3.concept_index import ConceptIndexRecord
from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.models import CandidateStageState

KST = ZoneInfo("Asia/Seoul")


def base_config():
    return MinerTargetConfig(daily_target=1659, timezone="Asia/Seoul")


def base_stats():
    return CollectionStats(
        date="2026-09-19",
        today_collected=100,
        collected_total=2000,
        daily_counts={"2026-09-18": 325, "2026-09-19": 100},
        last_miner_run_at="2026-09-19T18:00:00+09:00",
    )


def test_pending_is_active_union_parked_review():
    ledger = DistillLedger(
        candidate_states={
            "CAND-1111111111111111": CandidateStageState(
                candidate_id="CAND-1111111111111111",
                source_fingerprint="1" * 64,
                source_type="academic",
                stage="TRIAGE",
                status="PENDING",
                updated_at="2026-09-19T00:00:00Z",
            ),
            "CAND-2222222222222222": CandidateStageState(
                candidate_id="CAND-2222222222222222",
                source_fingerprint="2" * 64,
                source_type="academic",
                stage="REVIEW",
                status="COMPLETE",
                updated_at="2026-09-19T00:00:00Z",
            ),
        },
        parked_review_candidate_ids={"CAND-2222222222222222"},
    )
    status = build_public_status(
        base_config(),
        base_stats(),
        ledger,
        [],
        generated_at="2026-09-19T18:01:00+09:00",
        last_success_at=None,
    )
    assert status.distillation_pending == 2
    assert status.system_status == "DISTILLING"


def test_success_counts_unique_accepted_knowledge_units_only():
    rows = [
        ConceptIndexRecord(knowledge_unit_id="KU-1", status="ACCEPTED"),
        ConceptIndexRecord(knowledge_unit_id="KU-1", status="ACCEPTED"),
        ConceptIndexRecord(knowledge_unit_id="KU-2", status="REVIEW"),
    ]
    status = build_public_status(
        base_config(),
        base_stats(),
        DistillLedger(),
        rows,
        generated_at="2026-09-19T18:01:00+09:00",
        last_success_at=None,
    )
    assert status.distillation_success == 1


def test_target_reached_precedes_distilling_status():
    stats = base_stats().model_copy(update={"today_collected": 1659})
    ledger = DistillLedger(
        candidate_states={
            "CAND-1111111111111111": CandidateStageState(
                candidate_id="CAND-1111111111111111",
                source_fingerprint="1" * 64,
                source_type="academic",
                stage="TRIAGE",
                status="PENDING",
                updated_at="2026-09-19T00:00:00Z",
            )
        }
    )
    status = build_public_status(
        base_config(),
        stats,
        ledger,
        [],
        generated_at="2026-09-19T18:01:00+09:00",
        last_success_at=None,
    )
    assert status.system_status == "TARGET_REACHED"


def test_unknown_public_key_is_rejected():
    payload = build_public_status(
        base_config(),
        base_stats(),
        DistillLedger(),
        [],
        generated_at="2026-09-19T18:01:00+09:00",
        last_success_at=None,
    ).model_dump(mode="json")
    payload["candidate_id"] = "CAND-aaaaaaaaaaaaaaaa"
    with pytest.raises(ValueError):
        validate_public_payload(payload)


def test_serialized_public_status_has_no_candidate_or_url_fields():
    payload = build_public_status(
        base_config(),
        base_stats(),
        DistillLedger(),
        [],
        generated_at="2026-09-19T18:01:00+09:00",
        last_success_at=None,
    ).model_dump(mode="json")
    rendered = json.dumps(payload, sort_keys=True)
    assert "CAND-" not in rendered
    assert "canonical_hash" not in rendered
    assert "https://" not in rendered


def test_parse_concept_index_rejects_unknown_status():
    with pytest.raises(ValueError):
        parse_concept_index(b'{"knowledge_unit_id":"KU-1","status":"UNKNOWN"}\n')


def test_missing_ledger_fails_closed():
    from basketball_miner.dashboard_status import build_status_from_store

    class Store:
        def read_file(self, path):
            return None

        def list_dir(self, path):
            return []

    with pytest.raises(RuntimeError, match="ledger"):
        build_status_from_store(
            base_config(),
            base_stats(),
            Store(),
            generated_at="2026-09-19T18:01:00+09:00",
        )


def test_build_status_reads_private_state_and_latest_success():
    from basketball_miner.dashboard_status import build_status_from_store

    class Remote:
        def __init__(self, content):
            self.content = content
            self.sha = "0" * 40

    class Entry:
        def __init__(self, name, path, kind):
            self.name = name
            self.path = path
            self.type = kind

    ledger_payload = {
        "processed_blob_shas": [],
        "processed_staging_shas": [],
        "completed_batch_ids": [],
        "parked_review_candidate_ids": [],
        "terminal_candidate_ids": [],
        "review_candidate_ids": [],
        "normalized_source_keys": [],
        "canonical_hashes": [],
        "candidate_states": {},
    }
    files = {
        "ml/coach/miner-data/v3/ledgers/distill.json": json.dumps(ledger_payload).encode(),
        "ml/coach/miner-data/v3/concepts/concept_index.jsonl": (
            b'{"knowledge_unit_id":"KU-1","status":"ACCEPTED"}\n'
        ),
        "ml/coach/miner-data/v3/runs/2026/09/19/AUDIT-ok.json": json.dumps(
            {
                "stage": "AUDIT",
                "status": "COMPLETED",
                "created_at": "2026-09-18T17:34:13Z",
                "decision_counts": {
                    "CREATE": 1,
                    "SUPPORT": 0,
                    "REFINE": 0,
                    "CONTRADICT": 0,
                    "REVIEW": 1,
                    "BLOCKED": 0,
                },
            }
        ).encode(),
    }

    class Store:
        def read_file(self, path):
            value = files.get(path)
            return None if value is None else Remote(value)

        def list_dir(self, path):
            mapping = {
                "ml/coach/miner-data/v3/runs": [
                    Entry("2026", "ml/coach/miner-data/v3/runs/2026", "dir")
                ],
                "ml/coach/miner-data/v3/runs/2026": [
                    Entry("09", "ml/coach/miner-data/v3/runs/2026/09", "dir")
                ],
                "ml/coach/miner-data/v3/runs/2026/09": [
                    Entry("19", "ml/coach/miner-data/v3/runs/2026/09/19", "dir")
                ],
                "ml/coach/miner-data/v3/runs/2026/09/19": [
                    Entry(
                        "AUDIT-ok.json",
                        "ml/coach/miner-data/v3/runs/2026/09/19/AUDIT-ok.json",
                        "file",
                    )
                ],
            }
            return mapping.get(path, [])

    status = build_status_from_store(
        base_config(),
        base_stats(),
        Store(),
        generated_at="2026-09-19T18:01:00+09:00",
    )
    assert status.distillation_success == 1
    assert status.last_distillation_success_at == "2026-09-18T17:34:13Z"
