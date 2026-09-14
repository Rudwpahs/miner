from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pytest

from basketball_miner.distill_v3.github_store import RemoteEntry, RemoteFile
from basketball_miner.distill_v3.ledger import DistillLedger, ledger_payload
from basketball_miner.distill_v3.materialize import run_remote_materialization
from basketball_miner.distill_v3.models import BatchRecord, CandidateStageState
from basketball_miner.distill_v3.paths import ledger_path, metrics_path, queue_path


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


def _remote(path: str, content: bytes, char: str) -> RemoteFile:
    return RemoteFile(path=path, sha=char * 40, content=content)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fixture() -> tuple[MemoryStore, BatchRecord]:
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
        source_type="academic",
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
        "records": [{
            "candidate_id": candidate_id,
            "stage": "TRIAGE",
            "decision": "DEEP_PENDING",
            "reason_code": "RELEVANT",
        }],
    }
    store = MemoryStore(
        files={
            qpath: _remote(qpath, _json_bytes(batch.model_dump(mode="json")), "a"),
            spath: _remote(spath, _json_bytes(staging), "b"),
            ledger_path(): _remote(ledger_path(), _json_bytes(ledger_payload(ledger)), "c"),
        }
    )
    return store, batch


def test_remote_materialization_dry_run_has_zero_writes():
    store, _batch = _fixture()
    summary = run_remote_materialization(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
        write_shadow=False,
    )
    assert summary["staging_files_seen"] == 1
    assert summary["staging_files_materialized"] == 1
    assert summary["next_batches_created_by_stage"] == {"DEEP": 1}
    assert summary["write_enabled"] is False
    assert store.writes == []


def test_remote_materialization_writes_next_queue_before_observed_sha_state():
    store, _batch = _fixture()
    run_remote_materialization(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
        write_shadow=True,
    )
    assert store.writes[0][0] == "immutable"
    assert "/queues/deep/" in store.writes[0][1]
    mutable = {path: sha for kind, path, sha in store.writes if kind == "mutable"}
    assert mutable[ledger_path()] == "c" * 40
    assert metrics_path("2026-09-14") in mutable
    assert all(path.startswith("ml/coach/miner-data/v3/") for _kind, path, _sha in store.writes)


def test_different_byte_next_queue_collision_aborts_before_any_write():
    store, _batch = _fixture()
    dry = run_remote_materialization(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
        write_shadow=False,
    )
    assert dry["next_batch_ids"]
    deep_id = dry["next_batch_ids"][0]
    path = queue_path("DEEP", deep_id)
    store.files[path] = _remote(path, b'{"different":true}\n', "e")

    with pytest.raises(FileExistsError):
        run_remote_materialization(
            store=store,
            run_date="2026-09-14",
            created_at="2026-09-14T00:20:00Z",
            write_shadow=True,
        )
    assert store.writes == []


def test_same_byte_next_queue_collision_is_idempotent():
    store, _batch = _fixture()
    dry = run_remote_materialization(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
        write_shadow=False,
    )
    path = queue_path("DEEP", dry["next_batch_ids"][0])
    store.files[path] = _remote(path, dry["next_batch_payloads"][path].encode(), "e")
    run_remote_materialization(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:20:00Z",
        write_shadow=True,
    )
    assert not any(kind == "immutable" for kind, _path, _sha in store.writes)
    assert any(path == ledger_path() for kind, path, _sha in store.writes if kind == "mutable")


def test_stale_ledger_sha_fails_closed():
    store, _batch = _fixture()
    original_update = store.update_mutable

    def stale(path: str, content: bytes, *, expected_sha: str | None, message: str):
        if path == ledger_path():
            raise RuntimeError("stale remote SHA")
        return original_update(path, content, expected_sha=expected_sha, message=message)

    store.update_mutable = stale  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="stale remote SHA"):
        run_remote_materialization(
            store=store,
            run_date="2026-09-14",
            created_at="2026-09-14T00:20:00Z",
            write_shadow=True,
        )
