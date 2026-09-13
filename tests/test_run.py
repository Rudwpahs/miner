from types import SimpleNamespace

from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.run import _candidate_from_source, run_miner
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
        self.batch_ids: list[str] = []

    def write_batch(self, batch_id: str, candidates):
        if self.fail:
            raise RuntimeError("export failed")
        self.batch_ids.append(batch_id)
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


class FakeIdentityVerifier:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    def verify(self, source):
        self.calls += 1
        return self.result


def test_run_never_inspects_more_than_500():
    adapter = FakeAdapter("fake", [make_source(i) for i in range(700)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=500)
    assert counters.inspected == 500
    assert counters.exported == 500


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


def test_run_rechecks_relevance_using_canonical_metadata():
    observed = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/example",
        url="https://doi.org/10.1000/example",
        title="Basketball shooting biomechanics",
        authors=["Ada Player"],
        published_at="2026-01-01",
    )
    canonical = observed.model_copy(update={"title": "Molecular signaling in cardiac tissue"})
    result = SimpleNamespace(
        decision="MISMATCH",
        source=canonical,
        warnings=("DOI_TITLE_MISMATCH", "DOI_CANONICAL_METADATA_USED"),
    )
    sink = FakeSink()
    counters = run_miner(
        [FakeAdapter("crossref", [observed])],
        sink,
        budget=10,
        identity_verifier=FakeIdentityVerifier(result),
    )
    assert counters.identity_mismatches == 1
    assert counters.exported == 0
    assert sink.candidates == []


def test_run_exports_canonical_metadata_and_identity_warning():
    observed = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/example",
        url="https://doi.org/10.1000/example",
        title="Basketball passing decision making",
        authors=["Ada Player"],
        published_at="2026-01-01",
    )
    canonical = observed.model_copy(update={"title": "Basketball defensive closeout biomechanics"})
    result = SimpleNamespace(
        decision="MISMATCH",
        source=canonical,
        warnings=("DOI_TITLE_MISMATCH", "DOI_CANONICAL_METADATA_USED"),
    )
    sink = FakeSink()
    counters = run_miner(
        [FakeAdapter("crossref", [observed])],
        sink,
        budget=10,
        identity_verifier=FakeIdentityVerifier(result),
    )
    assert counters.exported == 1
    assert sink.candidates[0].title == canonical.title
    assert "DOI_TITLE_MISMATCH" in sink.candidates[0].warnings


def test_run_preserves_unverified_raw_candidate_without_aborting():
    observed = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/example",
        url="https://doi.org/10.1000/example",
        title="Basketball shooting biomechanics",
        authors=["Ada Player"],
        published_at="2026-01-01",
    )
    result = SimpleNamespace(
        decision="UNVERIFIED",
        source=observed,
        warnings=("DOI_IDENTITY_UNVERIFIED",),
    )
    sink = FakeSink()
    counters = run_miner(
        [FakeAdapter("crossref", [observed])],
        sink,
        budget=10,
        identity_verifier=FakeIdentityVerifier(result),
    )
    assert counters.identity_unverified == 1
    assert counters.exported == 1
    assert "DOI_IDENTITY_UNVERIFIED" in sink.candidates[0].warnings


def test_corrected_source_gets_new_candidate_id_and_lineage():
    original_source = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/wrong",
        url="https://doi.org/10.1000/wrong",
        title="Basketball shooting biomechanics",
    )
    corrected_source = original_source.model_copy(
        update={
            "stable_id": "10.1000/correct",
            "url": "https://doi.org/10.1000/correct",
        }
    )
    original = _candidate_from_source(original_source, ("SHOOTING",), ("basketball",))
    corrected = _candidate_from_source(
        corrected_source,
        ("SHOOTING",),
        ("basketball",),
        warnings=("CORRECTED_SOURCE",),
        supersedes_candidate_id=original.candidate_id,
    )
    assert corrected.candidate_id != original.candidate_id
    assert corrected.supersedes_candidate_id == original.candidate_id


def test_same_doi_canonicalization_keeps_candidate_id_stable():
    observed = SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1000/same",
        url="https://doi.org/10.1000/same",
        title="Basketball shooting",
    )
    canonical = observed.model_copy(update={"title": "Basketball shooting: biomechanics"})
    first = _candidate_from_source(observed, ("SHOOTING",), ("basketball",))
    second = _candidate_from_source(canonical, ("SHOOTING",), ("basketball",))
    assert first.candidate_id == second.candidate_id
