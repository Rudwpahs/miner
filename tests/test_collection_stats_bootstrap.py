from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from basketball_miner.collection_stats import bootstrap_collection_stats

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
    stats = bootstrap_collection_stats(store, datetime(2026, 9, 19, 12, 0, tzinfo=KST))
    assert stats.collected_total == 3
    assert stats.today_collected == 1
    assert stats.daily_counts == {"2026-09-18": 2, "2026-09-19": 1}


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
