from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pytest

from basketball_miner.distill_v3.github_store import RemoteEntry, RemoteFile
from basketball_miner.distill_v3.ledger import DistillLedger, ledger_payload
from basketball_miner.distill_v3.materialize import run_remote_materialization
from basketball_miner.distill_v3.metrics import DailyMetrics, check_release_invariants
from basketball_miner.distill_v3.models import BatchRecord, CandidateStageState
from basketball_miner.distill_v3.paths import ledger_path, queue_path


@dataclass
class MemoryStore:
    files: dict[str, RemoteFile] = field(default_factory=dict)
    writes: list[tuple[str, str, str | None]] = field(default_factory=list)

    def list_dir(self, path: str) -> list[RemoteEntry]:
        prefix = path.rstrip("/") + "/"
        children: dict[str, RemoteEntry] = {}
        for file_path, remote in self.files.items():
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            first, separator, _rest = remainder.partition("/")
            child_path = prefix + first
            if separator:
                children[first] = RemoteEntry(first, child_path, "d" * 40, "dir")
            else:
                children[first] = RemoteEntry(first, file_path, remote.sha, "file")
        return sorted(children.values(), key=lambda entry: (entry.name, entry.path))

    def read_file(self, path: str) -> RemoteFile | None:
        return self.files.get(path)

    def create_immutable(self, path: str, content: bytes, message: str) -> str | None:
        current = self.files.get(path)
        if current is not None:
            if current.content == content:
                return None
            raise FileExistsError(path)
        sha = hashlib.sha1(content).hexdigest()
        self.files[path] = RemoteFile(path=path, sha=sha, content=content)
        self.writes.append(("immutable", path, None))
        return sha

    def update_mutable(
        self,
        path: str,
        content: bytes,
        *,
        expected_sha: str | None,
        message: str,
    ) -> str | None:
        current = self.files.get(path)
        if current is None:
            if expected_sha is not None:
                raise RuntimeError("stale remote SHA")
        elif current.sha != expected_sha:
            raise RuntimeError("stale remote SHA")
        sha = hashlib.sha1(content).hexdigest()
        self.files[path] = RemoteFile(path=path, sha=sha, content=content)
        self.writes.append(("mutable", path, expected_sha))
        return sha


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _remote(path: str, content: bytes, char: str) -> RemoteFile:
    return RemoteFile(path=path, sha=char * 40, content=content)


def _legacy_fixture(*, include_inbox: bool) -> MemoryStore:
    candidate_id = "CAND-1111111111111111"
    fingerprint = "1" * 64
    batch = BatchRecord(
        batch_id="V3-TRIAGE-111111111111",
        stage="TRIAGE",
        candidate_ids=[candidate_id],
        priority=85,
        created_at="2026-09-14T00:00:00Z",
        input_fingerprints=[fingerprint],
        status="PENDING",
    )
    state = CandidateStageState(
        candidate_id=candidate_id,
        source_fingerprint=fingerprint,
        source_type=None,
        stage="TRIAGE",
        status="PENDING",
        batch_id=batch.batch_id,
        updated_at="2026-09-14T00:00:00Z",
    )
    ledger = DistillLedger(candidate_states={candidate_id: state})
    qpath = queue_path("TRIAGE", batch.batch_id)
    spath = "ml/coach/miner-data/v3/staging/triage/2026/09/14/RUN-001.jsonl"
    staging = {
        "run_id": "RUN-001",
        "batch_id": batch.batch_id,
        "stage": "TRIAGE",
        "worker": "GPT-V3",
        "created_at": "2026-09-14T00:10:00Z",
        "input_fingerprints": [fingerprint],
        "records": [
            {
                "candidate_id": candidate_id,
                "stage": "TRIAGE",
                "decision": "DEEP_PENDING",
                "reason_code": "RELEVANT",
            }
        ],
    }
    files = {
        qpath: _remote(qpath, _json_bytes(batch.model_dump(mode="json")), "a"),
        spath: _remote(spath, _json_bytes(staging), "b"),
        ledger_path(): _remote(ledger_path(), _json_bytes(ledger_payload(ledger)), "c"),
    }
    if include_inbox:
        inbox_path = "ml/coach/miner-data/inbox/2026/09/14/legacy.jsonl"
        candidate = {
            "adapter": "crossref",
            "source_type": "academic",
            "stable_id": "10.1234/legacy-source",
            "url": "https://doi.org/10.1234/legacy-source",
            "title": "Legacy basketball evidence",
            "authors": ["A. Author"],
            "published_at": "2026-09-01",
            "summary": "Authoritative source record for source type recovery.",
            "candidate_id": candidate_id,
            "canonical_hash": fingerprint,
            "topic_codes": ["SHOOTING"],
            "relevance_signals": ["basketball"],
            "provenance": "LINKED",
            "warnings": [],
            "discovered_at": "2026-09-14T00:00:00Z",
        }
        files[inbox_path] = _remote(inbox_path, _json_bytes(candidate), "d")
    return MemoryStore(files=files)


def test_remote_materialization_rehydrates_legacy_source_type_from_authoritative_inbox():
    store = _legacy_fixture(include_inbox=True)

    summary = run_remote_materialization(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
        write_shadow=False,
    )

    assert summary["legacy_source_type_rehydrations"] == 1
    assert summary["next_batches_created_by_stage"] == {"DEEP": 1}
    assert store.writes == []


def test_missing_authoritative_source_type_fails_before_any_write():
    store = _legacy_fixture(include_inbox=False)

    with pytest.raises(ValueError, match="source_type"):
        run_remote_materialization(
            store=store,
            run_date="2026-09-14",
            created_at="2026-09-14T00:20:00Z",
            write_shadow=True,
        )

    assert store.writes == []


def test_source_type_rehydration_failure_after_write_is_release_invariant():
    metrics = DailyMetrics(
        date="2026-09-14",
        source_type_rehydration_failure_after_write=1,
    )
    assert "source_type_rehydration_failure_after_write" in check_release_invariants(metrics)
