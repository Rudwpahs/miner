from datetime import datetime
from zoneinfo import ZoneInfo

from basketball_miner.collection_stats import bootstrap_collection_stats

KST = ZoneInfo("Asia/Seoul")


class Entry:
    def __init__(self, path: str, kind: str) -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.type = kind


class RemoteFile:
    def __init__(self, content: bytes) -> None:
        self.content = content


class Store:
    def __init__(self) -> None:
        self.file_path = "ml/coach/miner-data/inbox/2026/09/18/late.jsonl"

    def list_dir(self, path: str):
        mapping = {
            "ml/coach/miner-data/inbox": [
                Entry("ml/coach/miner-data/inbox/2026", "dir")
            ],
            "ml/coach/miner-data/inbox/2026": [
                Entry("ml/coach/miner-data/inbox/2026/09", "dir")
            ],
            "ml/coach/miner-data/inbox/2026/09": [
                Entry("ml/coach/miner-data/inbox/2026/09/18", "dir")
            ],
            "ml/coach/miner-data/inbox/2026/09/18": [Entry(self.file_path, "file")],
        }
        return mapping.get(path, [])

    def read_file(self, path: str):
        assert path == self.file_path
        return RemoteFile(
            b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa",'
            b'"discovered_at":"2026-09-18T23:30:00Z"}\n'
        )


def test_bootstrap_assigns_candidates_to_seoul_calendar_day():
    stats = bootstrap_collection_stats(
        Store(),
        datetime(2026, 9, 19, 12, 0, tzinfo=KST),
    )

    assert stats.collected_total == 1
    assert stats.today_collected == 1
    assert stats.daily_counts == {"2026-09-19": 1}
