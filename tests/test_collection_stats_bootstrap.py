import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from basketball_miner.collection_stats import (
    CollectionStats,
    bootstrap_collection_stats,
    reconcile_collection_stats,
)
from basketball_miner.models import SourceRecord
from basketball_miner.normalize import fingerprint

KST = ZoneInfo("Asia/Seoul")


class Entry:
    def __init__(self, path: str, kind: str) -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.type = kind


class RemoteFile:
    def __init__(self, path: str, content: bytes) -> None:
        self.path = path
        self.content = content
        self.sha = "0" * 40


class FakeStore:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.directories = {"ml/coach/miner-data/inbox"}
        for path in files:
            parts = path.split("/")[:-1]
            for index in range(1, len(parts) + 1):
                self.directories.add("/".join(parts[:index]))

    def list_dir(self, path: str):
        prefix = path.rstrip("/") + "/"
        children: dict[str, str] = {}
        for directory in self.directories:
            if directory.startswith(prefix):
                tail = directory[len(prefix) :]
                if tail and "/" not in tail:
                    children[tail] = "dir"
        for file_path in self.files:
            if file_path.startswith(prefix):
                tail = file_path[len(prefix) :]
                if tail and "/" not in tail:
                    children[tail] = "file"
        return [Entry(prefix + name, kind) for name, kind in sorted(children.items())]

    def read_file(self, path: str):
        content = self.files.get(path)
        return None if content is None else RemoteFile(path, content)


def test_bootstrap_counts_candidate_ids_once_across_days():
    store = FakeStore(
        {
            "ml/coach/miner-data/inbox/2026/09/18/a.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa"}\n'
                b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb"}\n'
            ),
            "ml/coach/miner-data/inbox/2026/09/19/b.jsonl": (
                b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb"}\n'
                b'{"candidate_id":"CAND-cccccccccccccccc"}\n'
            ),
        }
    )
    now = datetime(2026, 9, 19, 12, 0, tzinfo=KST)
    stats = bootstrap_collection_stats(store, now)
    assert stats.collected_total == 3
    assert stats.today_collected == 1
    assert stats.daily_counts == {"2026-09-18": 2, "2026-09-19": 1}
    assert stats.reconciled_through_at == now.isoformat()


def test_bootstrap_rejects_malformed_json():
    store = FakeStore({"ml/coach/miner-data/inbox/2026/09/19/a.jsonl": b"not-json\n"})
    with pytest.raises(ValueError):
        bootstrap_collection_stats(store, datetime(2026, 9, 19, 12, 0, tzinfo=KST))


def test_bootstrap_rejects_malformed_candidate_id():
    store = FakeStore(
        {"ml/coach/miner-data/inbox/2026/09/19/a.jsonl": b'{"candidate_id":"bad"}\n'}
    )
    with pytest.raises(ValueError):
        bootstrap_collection_stats(store, datetime(2026, 9, 19, 12, 0, tzinfo=KST))


def test_reconcile_recovers_failed_persist_without_double_counting():
    store = FakeStore(
        {
            "ml/coach/miner-data/inbox/2026/09/19/run-a.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa",'
                b'"discovered_at":"2026-09-19T00:10:00Z"}\n'
                b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb",'
                b'"discovered_at":"2026-09-19T00:11:00Z"}\n'
            ),
            "ml/coach/miner-data/inbox/2026/09/19/run-retry.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa",'
                b'"discovered_at":"2026-09-19T00:10:00Z"}\n'
            ),
        }
    )
    stats = CollectionStats(
        date="2026-09-19",
        today_collected=100,
        collected_total=500,
        daily_counts={"2026-09-19": 100},
        reconciled_through_at="2026-09-19T09:00:00+09:00",
    )

    reconciled = reconcile_collection_stats(
        store,
        stats,
        datetime(2026, 9, 19, 10, 0, tzinfo=KST),
    )

    assert reconciled.collected_total == 502
    assert reconciled.today_collected == 102
    assert reconciled.daily_counts["2026-09-19"] == 102
    assert reconciled.reconciled_through_at == "2026-09-19T10:00:00+09:00"


def test_reconcile_is_idempotent_after_cursor_advances():
    store = FakeStore(
        {
            "ml/coach/miner-data/inbox/2026/09/19/run-a.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa",'
                b'"discovered_at":"2026-09-19T00:10:00Z"}\n'
            )
        }
    )
    stats = CollectionStats(
        date="2026-09-19",
        today_collected=100,
        collected_total=500,
        daily_counts={"2026-09-19": 100},
        reconciled_through_at="2026-09-19T09:00:00+09:00",
    )
    first = reconcile_collection_stats(store, stats, datetime(2026, 9, 19, 10, 0, tzinfo=KST))
    second = reconcile_collection_stats(store, first, datetime(2026, 9, 19, 10, 30, tzinfo=KST))

    assert first.collected_total == 501
    assert second.collected_total == 501
    assert second.today_collected == 101
    assert second.reconciled_through_at == "2026-09-19T10:30:00+09:00"


def test_reconcile_assigns_recovered_candidate_to_discovery_day_across_midnight():
    store = FakeStore(
        {
            "ml/coach/miner-data/inbox/2026/09/19/late.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa",'
                b'"discovered_at":"2026-09-19T14:59:30Z"}\n'
            ),
            "ml/coach/miner-data/inbox/2026/09/20/retry.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa",'
                b'"discovered_at":"2026-09-19T14:59:30Z"}\n'
            ),
        }
    )
    stats = CollectionStats(
        date="2026-09-20",
        today_collected=0,
        collected_total=500,
        daily_counts={"2026-09-19": 100},
        reconciled_through_at="2026-09-19T23:59:00+09:00",
    )

    reconciled = reconcile_collection_stats(
        store,
        stats,
        datetime(2026, 9, 20, 0, 10, tzinfo=KST),
    )

    assert reconciled.collected_total == 501
    assert reconciled.today_collected == 0
    assert reconciled.daily_counts["2026-09-19"] == 101


def test_reconcile_restores_seen_sets_for_recovered_private_export():
    source = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/recovered",
        url="https://doi.org/10.1000/recovered",
        title="Basketball jump shot recovery",
        authors=["Test Author"],
        published_at="2026-09-19",
        summary=None,
    )
    row = {
        **source.model_dump(mode="json"),
        "candidate_id": "CAND-aaaaaaaaaaaaaaaa",
        "discovered_at": "2026-09-19T00:10:00Z",
    }
    store = FakeStore(
        {
            "ml/coach/miner-data/inbox/2026/09/19/recovered.jsonl": (
                json.dumps(row).encode("utf-8") + b"\n"
            )
        }
    )
    stats = CollectionStats(
        date="2026-09-19",
        today_collected=100,
        collected_total=500,
        daily_counts={"2026-09-19": 100},
        reconciled_through_at="2026-09-19T09:00:00+09:00",
    )
    seen_hashes: set[str] = set()
    seen_dois: set[str] = set()

    reconcile_collection_stats(
        store,
        stats,
        datetime(2026, 9, 19, 10, 0, tzinfo=KST),
        seen_hashes=seen_hashes,
        seen_crossref_dois=seen_dois,
    )

    assert fingerprint(source) in seen_hashes
    assert "10.1000/recovered" in seen_dois
