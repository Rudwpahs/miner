from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pytest

from basketball_miner.distill_v3.github_store import RemoteEntry, RemoteFile
from basketball_miner.distill_v3.ledger import DistillLedger, ledger_payload
from basketball_miner.distill_v3.paths import concept_index_path, ledger_path, metrics_path, queue_path
from basketball_miner.distill_v3.prepare import (
    InboxBlob,
    discover_remote_files,
    load_v2_history,
    prepare_shadow,
    run_remote_shadow,
)
from basketball_miner.models import CandidateRecord


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
                children[first] = RemoteEntry(
                    name=first,
                    path=child_path,
                    sha="d" * 40,
                    type="dir",
                )
            else:
                children[first] = RemoteEntry(
                    name=first,
                    path=file_path,
                    sha=remote.sha,
                    type="file",
                )
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
        if current is not None and current.content == content:
            return None
        sha = hashlib.sha1(content).hexdigest()
        self.files[path] = RemoteFile(path=path, sha=sha, content=content)
        self.writes.append(("mutable", path, expected_sha))
        return sha


def _remote(path: str, content: str, sha_char: str) -> RemoteFile:
    return RemoteFile(path=path, sha=sha_char * 40, content=content.encode("utf-8"))


def _candidate() -> CandidateRecord:
    return CandidateRecord.model_validate(
        {
            "adapter": "crossref",
            "source_type": "academic",
            "stable_id": "10.1234/remote-test",
            "url": "https://doi.org/10.1234/remote-test",
            "title": "Basketball shooting remote test",
            "authors": ["A. Author"],
            "published_at": "2026-09-01",
            "summary": "Basketball shooting evidence.",
            "candidate_id": "CAND-1234567890abcdef",
            "canonical_hash": "1" * 64,
            "topic_codes": ["SHOOTING"],
            "relevance_signals": ["basketball"],
            "provenance": "LINKED",
            "warnings": [],
            "discovered_at": "2026-09-14T00:00:00Z",
        }
    )


def _inbox_jsonl(candidate: CandidateRecord) -> str:
    return json.dumps(candidate.model_dump(mode="json"), sort_keys=True) + "\n"


def test_recursive_discovery_returns_only_requested_suffixes():
    store = MemoryStore(
        files={
            "ml/coach/miner-data/inbox/2026/09/13/a.jsonl": _remote("a", "{}\n", "a"),
            "ml/coach/miner-data/inbox/2026/09/13/ignore.txt": _remote("b", "x", "b"),
            "ml/coach/miner-data/inbox/2026/09/14/nested/b.jsonl": _remote("c", "{}\n", "c"),
        }
    )
    paths = discover_remote_files(
        store,
        "ml/coach/miner-data/inbox",
        suffixes=(".jsonl",),
    )
    assert paths == [
        "ml/coach/miner-data/inbox/2026/09/13/a.jsonl",
        "ml/coach/miner-data/inbox/2026/09/14/nested/b.jsonl",
    ]


def test_historical_readers_ingest_v2_without_writing():
    store = MemoryStore(
        files={
            "ml/coach/miner-data/distilled/accepted/2026/09/13.jsonl": _remote(
                "accepted", '{"knowledge_unit_id":"KU-A","claim":"A"}\n', "a"
            ),
            "ml/coach/miner-data/distilled/review/2026/09/13.jsonl": _remote(
                "review", '{"candidate_id":"CAND-aaaaaaaaaaaaaaaa","claim":"R"}\n', "b"
            ),
            "ml/coach/miner-data/distilled/manifests/2026/09/13.json": _remote(
                "manifest", '{"processed_candidate_ids":["CAND-bbbbbbbbbbbbbbbb"]}\n', "c"
            ),
        }
    )
    accepted, review, manifests = load_v2_history(store)
    assert accepted == [{"knowledge_unit_id": "KU-A", "claim": "A"}]
    assert review == [{"candidate_id": "CAND-aaaaaaaaaaaaaaaa", "claim": "R"}]
    assert manifests == [{"processed_candidate_ids": ["CAND-bbbbbbbbbbbbbbbb"]}]
    assert store.writes == []


def test_remote_dry_run_has_no_puts_and_reports_shadow_summary():
    candidate = _candidate()
    path = "ml/coach/miner-data/inbox/2026/09/14/one.jsonl"
    store = MemoryStore(files={path: _remote(path, _inbox_jsonl(candidate), "a")})

    summary = run_remote_shadow(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
        batch_size=100,
        write_shadow=False,
    )

    assert summary["new_inbox_blobs"] == 1
    assert summary["new_candidates"] == 1
    assert summary["duplicates"] == 0
    assert summary["triage_candidates"] == 1
    assert summary["triage_batches"] == 1
    assert summary["invariant_failures"] == []
    assert summary["write_enabled"] is False
    assert store.writes == []


def test_write_shadow_persists_only_v3_paths_and_uses_observed_shas():
    candidate = _candidate()
    inbox = "ml/coach/miner-data/inbox/2026/09/14/one.jsonl"
    old_ledger = json.dumps(ledger_payload(DistillLedger()), sort_keys=True) + "\n"
    store = MemoryStore(
        files={
            inbox: _remote(inbox, _inbox_jsonl(candidate), "a"),
            ledger_path(): _remote(ledger_path(), old_ledger, "b"),
            concept_index_path(): _remote(concept_index_path(), "", "c"),
            metrics_path("2026-09-14"): _remote(metrics_path("2026-09-14"), "{}\n", "d"),
        }
    )

    summary = run_remote_shadow(
        store=store,
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
        batch_size=100,
        write_shadow=True,
    )

    assert summary["write_enabled"] is True
    written_paths = {path for _kind, path, _sha in store.writes}
    assert ledger_path() in written_paths
    assert concept_index_path() in written_paths
    assert metrics_path("2026-09-14") in written_paths
    queue_writes = [item for item in store.writes if item[0] == "immutable"]
    assert len(queue_writes) == 1
    assert queue_writes[0][1].startswith("ml/coach/miner-data/v3/queues/triage/")
    assert all(path.startswith("ml/coach/miner-data/v3/") for path in written_paths)
    expected_by_path = {path: sha for kind, path, sha in store.writes if kind == "mutable"}
    assert expected_by_path[ledger_path()] == "b" * 40
    assert expected_by_path[concept_index_path()] == "c" * 40
    assert expected_by_path[metrics_path("2026-09-14")] == "d" * 40


def test_queue_collision_with_different_bytes_aborts_instead_of_renaming():
    candidate = _candidate()
    inbox_path = "ml/coach/miner-data/inbox/2026/09/14/one.jsonl"
    blob = InboxBlob(path=inbox_path, sha="a" * 40, candidates=(candidate,))
    prepared = prepare_shadow(
        inbox_blobs=[blob],
        existing_ledger=None,
        accepted_rows=[],
        review_rows=[],
        manifests=[],
        run_date="2026-09-14",
        created_at="2026-09-14T00:00:00Z",
    )
    batch = prepared.triage_batches[0]
    collision_path = queue_path("TRIAGE", batch.batch_id)
    store = MemoryStore(
        files={
            inbox_path: _remote(inbox_path, _inbox_jsonl(candidate), "a"),
            collision_path: _remote(collision_path, '{"different":true}\n', "e"),
        }
    )

    with pytest.raises(FileExistsError):
        run_remote_shadow(
            store=store,
            run_date="2026-09-14",
            created_at="2026-09-14T00:00:00Z",
            batch_size=100,
            write_shadow=True,
        )
    assert not any(path != collision_path for _kind, path, _sha in store.writes)
