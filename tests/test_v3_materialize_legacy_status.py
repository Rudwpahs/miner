import json

from basketball_miner.distill_v3.github_store import RemoteFile
from basketball_miner.distill_v3.materialize import StagingBlob, load_source_batches


class _Store:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read_file(self, path: str) -> RemoteFile:
        return RemoteFile(
            path=path,
            sha="a" * 40,
            content=json.dumps(self.payload).encode("utf-8"),
        )


def test_load_source_batches_normalizes_legacy_completed_status():
    batch_id = "V3-AUDIT-555555555555"
    payload = {
        "batch_id": batch_id,
        "stage": "AUDIT",
        "candidate_ids": ["CAND-1111111111111111"],
        "priority": 85,
        "created_at": "2026-09-14T00:00:00Z",
        "input_fingerprints": ["1" * 64],
        "status": "COMPLETED",
    }
    blob = StagingBlob(
        path="ml/coach/miner-data/v3/staging/audit/2026/09/14/run.jsonl",
        sha="b" * 40,
        payload={"batch_id": batch_id, "stage": "AUDIT"},
    )

    batches = load_source_batches(_Store(payload), [blob])

    assert batches[batch_id].status == "COMPLETE"
