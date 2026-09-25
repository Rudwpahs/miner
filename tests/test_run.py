from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.run import run_miner
from basketball_miner.source_identity import IdentityDecision, SourceIdentityResult
from basketball_miner.sources.base import AdapterBatch


def make_source(index: int, *, adapter: str = "fake", title: str | None = None) -> SourceRecord:
    stable_id = f"item-{index}" if adapter != "crossref" else f"10.1000/item-{index}"
    url = (
        f"https://example.org/{adapter}/{index}"
        if adapter != "crossref"
        else f"https://doi.org/{stable_id}"
    )
    return SourceRecord(
        adapter=adapter,
        source_type="academic",
        stable_id=stable_id,
        url=url,
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


class ShortParsedPageAdapter:
    name = "short-page"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        self.calls += 1
        if self.calls == 1:
            return AdapterBatch(
                records=[make_source(i, adapter=self.name) for i in range(44)],
                next_checkpoint=Checkpoint(adapter=self.name, cursor="page-2"),
                error_count=6,
                has_more=True,
            )
        return AdapterBatch(
            records=[make_source(i, adapter=self.name) for i in range(44, 50)],
            next_checkpoint=Checkpoint(adapter=self.name, cursor="done"),
            has_more=False,
        )


class FakeSink:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.candidates = []
        self.batch_ids: list[str] = []

    def write_batch(self, batch_id: str, candidates):
        if self.fail:
            raise RuntimeError("export failed")
        self.batch_ids.append(batch_id)
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


class FakeIdentityVerifier:
    def __init__(self, results: dict[str, SourceIdentityResult]) -> None:
        self.results = results
        self.calls: list[str] = []

    def verify(self, source: SourceRecord) -> SourceIdentityResult:
        self.calls.append(source.stable_id)
        return self.results[source.stable_id]


def test_run_accepts_40x_budget_ceiling():
    adapter = FakeAdapter("fake", [make_source(1)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=20_000)
    assert counters.inspected == 1
    assert counters.exported == 1


def test_run_rejects_budget_above_40x_ceiling():
    adapter = FakeAdapter("fake", [make_source(1)])
    sink = FakeSink()
    try:
        run_miner([adapter], sink, budget=20_001)
    except ValueError as exc:
        assert "between 1 and 20000" in str(exc)
    else:
        raise AssertionError("budget above 20,000 must be rejected")


def test_run_continues_after_short_parsed_page_when_adapter_has_more():
    adapter = ShortParsedPageAdapter()
    sink = FakeSink()

    counters = run_miner([adapter], sink, budget=20_000)

    assert adapter.calls == 2
    assert counters.inspected == 50
    assert counters.exported == 50
    assert counters.adapter_errors == 6


def test_run_without_budget_collects_until_max_exports():
    adapter = FakeAdapter("fake", [make_source(i) for i in range(100)])
    sink = FakeSink()

    counters = run_miner(
        [adapter],
        sink,
        budget=None,
        chunk_size=50,
        max_exports=75,
    )

    assert counters.exported == 75
    assert counters.inspected == 75
    assert adapter.calls == 2


def test_run_allocates_budget_across_adapters():
    first = FakeAdapter("first", [make_source(i, adapter="first") for i in range(200)])
    second = FakeAdapter(
        "second",
        [
            make_source(i, adapter="second", title=f"Basketball defensive closeout {i}")
            for i in range(200)
        ],
    )
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


def test_run_uses_supplied_run_id_in_export_batch_names():
    adapter = FakeAdapter("fake", [make_source(1)])
    sink = FakeSink()
    run_miner([adapter], sink, budget=10, run_id="RUN-20260911T001500Z")
    assert sink.batch_ids == ["RUN-20260911T001500Z-fake-0001"]


def test_crossref_identity_exports_canonical_metadata_and_warning():
    observed = make_source(1, adapter="crossref", title="Basketball passing under pressure")
    canonical = observed.model_copy(
        update={"title": "Basketball passing decisions under defensive pressure"}
    )
    verifier = FakeIdentityVerifier(
        {
            observed.stable_id: SourceIdentityResult(
                IdentityDecision.MISMATCH,
                canonical,
                ("DOI_TITLE_MISMATCH", "DOI_CANONICAL_METADATA_USED"),
            )
        }
    )
    sink = FakeSink()

    counters = run_miner(
        [FakeAdapter("crossref", [observed])],
        sink,
        budget=10,
        identity_verifier=verifier,
    )

    assert sink.candidates[0].title == canonical.title
    assert "DOI_TITLE_MISMATCH" in sink.candidates[0].warnings
    assert counters.identity_mismatches == 1
    assert counters.identity_verified == 1


def test_crossref_identity_rechecks_canonical_relevance_and_drops_false_hit():
    observed = make_source(1, adapter="crossref", title="Basketball passing under pressure")
    canonical = observed.model_copy(update={"title": "Nutrition in soccer players"})
    verifier = FakeIdentityVerifier(
        {observed.stable_id: SourceIdentityResult(IdentityDecision.MISMATCH, canonical)}
    )
    sink = FakeSink()

    counters = run_miner(
        [FakeAdapter("crossref", [observed])],
        sink,
        budget=10,
        identity_verifier=verifier,
    )

    assert sink.candidates == []
    assert counters.exported == 0
    assert counters.identity_mismatches == 1


def test_crossref_unverified_preserves_observed_candidate_with_warning():
    observed = make_source(1, adapter="crossref")
    verifier = FakeIdentityVerifier(
        {
            observed.stable_id: SourceIdentityResult(
                IdentityDecision.UNVERIFIED,
                None,
                ("DOI_IDENTITY_UNVERIFIED",),
            )
        }
    )
    sink = FakeSink()

    counters = run_miner(
        [FakeAdapter("crossref", [observed])],
        sink,
        budget=10,
        identity_verifier=verifier,
    )

    assert sink.candidates[0].title == observed.title
    assert sink.candidates[0].warnings == ["DOI_IDENTITY_UNVERIFIED"]
    assert counters.identity_unverified == 1


def test_crossref_same_signature_different_dois_flags_collision_in_batch():
    first = make_source(1, adapter="crossref", title="Basketball passing biomechanics")
    second = make_source(2, adapter="crossref", title="Basketball passing biomechanics")
    canonical_first = first.model_copy(update={"authors": ["Same Author"]})
    canonical_second = second.model_copy(update={"authors": ["Same Author"]})
    verifier = FakeIdentityVerifier(
        {
            first.stable_id: SourceIdentityResult(IdentityDecision.EXACT_MATCH, canonical_first),
            second.stable_id: SourceIdentityResult(IdentityDecision.EXACT_MATCH, canonical_second),
        }
    )
    sink = FakeSink()

    counters = run_miner(
        [FakeAdapter("crossref", [first, second])],
        sink,
        budget=10,
        identity_verifier=verifier,
    )

    assert len(sink.candidates) == 2
    assert all("DOI_IDENTITY_COLLISION" in item.warnings for item in sink.candidates)
    assert counters.identity_collisions == 2
