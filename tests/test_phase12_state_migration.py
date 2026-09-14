from basketball_miner.models import Checkpoint, SourceRecord
from basketball_miner.normalize import fingerprint
from basketball_miner.run import run_miner
from basketball_miner.source_identity import IdentityDecision, SourceIdentityResult
from basketball_miner.sources.base import AdapterBatch


class OneShotAdapter:
    name = "crossref"

    def __init__(self, records: list[SourceRecord]) -> None:
        self.records = records

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        start = int(checkpoint.cursor or "0")
        rows = self.records[start : start + limit]
        return AdapterBatch(
            records=rows,
            next_checkpoint=Checkpoint(adapter=self.name, cursor=str(start + len(rows))),
        )


class CollectingSink:
    def __init__(self) -> None:
        self.candidates = []

    def write_batch(self, batch_id: str, candidates):
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


class ExactVerifier:
    def verify(self, source: SourceRecord) -> SourceIdentityResult:
        return SourceIdentityResult(IdentityDecision.EXACT_MATCH, source)


def source(doi: str, title: str = "Basketball passing under pressure") -> SourceRecord:
    return SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id=doi,
        url=f"https://doi.org/{doi}",
        title=title,
        authors=["Ada Player"],
        published_at="2026-09-01",
        summary=None,
    )


def test_legacy_seen_hash_does_not_hide_new_crossref_doi_identity():
    first = source("10.1000/old")
    corrected = source("10.1000/new")
    legacy_seen = {fingerprint(first)}
    doi_state: set[str] = set()
    sink = CollectingSink()

    counters = run_miner(
        [OneShotAdapter([corrected])],
        sink,
        budget=10,
        seen_hashes=legacy_seen,
        seen_crossref_dois=doi_state,
        identity_verifier=ExactVerifier(),
    )

    assert counters.exported == 1
    assert [item.stable_id for item in sink.candidates] == ["10.1000/new"]
    assert doi_state == {"10.1000/new"}


def test_crossref_doi_state_prevents_reexport_even_when_legacy_hash_differs():
    item = source("10.1000/already-seen", title="Basketball passing revised title")
    sink = CollectingSink()

    counters = run_miner(
        [OneShotAdapter([item])],
        sink,
        budget=10,
        seen_hashes=set(),
        seen_crossref_dois={"10.1000/already-seen"},
        identity_verifier=ExactVerifier(),
    )

    assert counters.duplicates == 1
    assert counters.exported == 0
    assert sink.candidates == []
