from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.run import run_miner
from basketball_miner.sources.base import AdapterBatch


def make_source(index: int, *, adapter: str = "fake", title: str | None = None) -> SourceRecord:
    return SourceRecord(
        adapter=adapter,
        source_type="academic",
        stable_id=f"item-{index}",
        url=f"https://example.org/{adapter}/{index}",
        title=title or f"Basketball jump shot release angle {index}",
        authors=["Test Author"],
        published_at="2026-09-01",
        summary=None,
    )


class FakeAdapter:
    def __init__(self, name: str, records: list[SourceRecord]) -> None:
        self.name = name
        self.records = records
        self.calls = 0

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        self.calls += 1
        start = int(checkpoint.cursor or "0")
        rows = self.records[start : start + limit]
        return AdapterBatch(
            records=rows,
            next_checkpoint=Checkpoint(adapter=self.name, cursor=str(start + len(rows))),
        )


class FakeSink:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.candidates = []

    def write_batch(self, batch_id: str, candidates):
        if self.fail:
            raise RuntimeError("export failed")
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


def test_run_never_inspects_more_than_500():
    adapter = FakeAdapter("fake", [make_source(i) for i in range(700)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=500)
    assert counters.inspected == 500
    assert counters.exported == 500


def test_run_allocates_budget_across_adapters():
    first = FakeAdapter("first", [make_source(i, adapter="first") for i in range(200)])
    second = FakeAdapter("second", [make_source(i, adapter="second") for i in range(200)])
    sink = FakeSink()
    counters = run_miner([first, second], sink, budget=100)
    assert counters.inspected == 100
    assert first.calls >= 1
    assert second.calls >= 1
    assert {candidate.adapter for candidate in sink.candidates} == {"first", "second"}


def test_run_deduplicates_before_export():
    records = [
        make_source(1, title="Basketball defensive closeout"),
        make_source(2, title="Basketball defensive closeout"),
    ]
    adapter = FakeAdapter("fake", records)
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=10)
    assert counters.inspected == 2
    assert counters.duplicates == 1
    assert counters.exported == 1
    assert len(sink.candidates) == 1


def test_run_does_not_advance_checkpoint_when_export_fails():
    adapter = FakeAdapter("fake", [make_source(1)])
    checkpoints = {"fake": Checkpoint(adapter="fake", cursor="0")}
    sink = FakeSink(fail=True)
    try:
        run_miner([adapter], sink, budget=10, checkpoints=checkpoints)
    except RuntimeError:
        pass
    else:
        raise AssertionError("export failure must propagate")
    assert checkpoints["fake"].cursor == "0"
