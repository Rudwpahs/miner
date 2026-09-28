import json

from basketball_miner.collection_stats import CollectionStats, MinerTargetConfig
from basketball_miner.dashboard_status import build_public_status, build_status_from_store
from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.models import CandidateStageState


def config():
    return MinerTargetConfig(daily_target=1659, timezone="Asia/Seoul")


def test_miner_is_delayed_after_three_missed_collection_windows():
    stats = CollectionStats(
        date="2026-09-28",
        today_collected=100,
        collected_total=1000,
        daily_counts={"2026-09-28": 100},
        last_miner_run_at="2026-09-28T15:00:00+09:00",
    )
    status = build_public_status(
        config(),
        stats,
        DistillLedger(),
        [],
        generated_at="2026-09-28T16:01:00+09:00",
        last_success_at=None,
    )
    assert status.miner.status == "DELAYED"
    assert status.summary_status == "DEGRADED"


def test_distillation_is_delayed_with_pending_work_and_stale_success():
    class Remote:
        def __init__(self, content):
            self.content = content
            self.sha = "0" * 40

    class Entry:
        def __init__(self, name, path, kind):
            self.name = name
            self.path = path
            self.type = kind

    ledger = DistillLedger(
        candidate_states={
            "CAND-1111111111111111": CandidateStageState(
                candidate_id="CAND-1111111111111111",
                source_fingerprint="1" * 64,
                source_type="academic",
                stage="TRIAGE",
                status="PENDING",
                updated_at="2026-09-28T00:00:00Z",
            )
        }
    )
    files = {
        "ml/coach/miner-data/v3/ledgers/distill.json": ledger.model_dump_json().encode(),
        "ml/coach/miner-data/v3/concepts/concept_index.jsonl": b"",
        "ml/coach/miner-data/v3/runs/2026/09/28/AUDIT-ok.json": json.dumps(
            {
                "stage": "AUDIT",
                "status": "COMPLETED",
                "created_at": "2026-09-27T18:00:00+09:00",
                "decision_counts": {
                    "CREATE": 1,
                    "SUPPORT": 0,
                    "REFINE": 0,
                    "CONTRADICT": 0,
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
                    Entry("28", "ml/coach/miner-data/v3/runs/2026/09/28", "dir")
                ],
                "ml/coach/miner-data/v3/runs/2026/09/28": [
                    Entry(
                        "AUDIT-ok.json",
                        "ml/coach/miner-data/v3/runs/2026/09/28/AUDIT-ok.json",
                        "file",
                    )
                ],
            }
            return mapping.get(path, [])

    stats = CollectionStats(
        date="2026-09-28",
        today_collected=1659,
        collected_total=10284,
        daily_counts={"2026-09-28": 1659},
        last_miner_run_at="2026-09-28T01:56:47+09:00",
    )
    status = build_status_from_store(
        config(),
        stats,
        Store(),
        generated_at="2026-09-28T16:01:00+09:00",
    )
    assert status.distillation.pending == 1
    assert status.distillation.status == "DELAYED"
    assert status.distillation.reason == "NO_RECENT_SUCCESS"
    assert status.summary_status == "DEGRADED"
