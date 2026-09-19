import pytest

from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.run import run_miner
from basketball_miner.sources.base import AdapterBatch


def make_source(index: int) -> SourceRecord:
    return SourceRecord(
        adapter="fake",
        source_type="academic",
        stable_id=f"item-{index}",
        url=f"https://example.org/fake/{index}",
        title=f"Basketball jump shot release angle {index}",
        authors=["Test Author"],
        published_at="2026-09-01",
        summary=None,
    )


class FakeAdapter:
    name = "fake"

    def __init__(self, records: list[SourceRecord]) -> None:
        self.records = records
        self.calls = 0
        self.request_limits: list[int] = []

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        self.calls += 1
        self.request_limits.append(limit)
        start = int(checkpoint.cursor or "0")
        rows = self.records[start : start + limit]
        return AdapterBatch(
            records=rows,
            next_checkpoint=Checkpoint(adapter=self.name, cursor=str(start + len(rows))),
        )


class FakeSink:
    def __init__(self) -> None:
        self.candidates = []

    def write_batch(self, batch_id: str, candidates):
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


def test_zero_export_cap_skips_adapter_calls():
    adapter = FakeAdapter([make_source(1)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=10, max_exports=0)
    assert adapter.calls == 0
    assert counters.exported == 0


def test_final_request_is_bounded_by_remaining_export_slots():
    adapter = FakeAdapter([make_source(i) for i in range(10)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=100, chunk_size=50, max_exports=3)
    assert counters.exported == 3
    assert len(sink.candidates) == 3
    assert adapter.request_limits[0] == 3


def test_negative_export_cap_is_rejected():
    with pytest.raises(ValueError, match="max_exports"):
        run_miner([], FakeSink(), max_exports=-1)
