import json
from pathlib import Path

from basketball_miner.models import CandidateRecord, Checkpoint, SourceRecord
from basketball_miner.run import run_miner
from basketball_miner.source_identity import IdentityDecision, SourceIdentityResult
from basketball_miner.sources.base import AdapterBatch

FIXTURE = Path(__file__).parent / "fixtures" / "crossref_identity_collision.json"


class FixtureAdapter:
    name = "crossref"

    def __init__(self, records: list[SourceRecord]) -> None:
        self.records = records
        self.used = False

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        if self.used:
            rows = []
        else:
            rows = self.records[:limit]
            self.used = True
        return AdapterBatch(
            records=rows,
            next_checkpoint=Checkpoint(adapter=self.name, cursor="done"),
        )


class ExactVerifier:
    def verify(self, source: SourceRecord) -> SourceIdentityResult:
        return SourceIdentityResult(IdentityDecision.EXACT_MATCH, source)


class CollectingSink:
    def __init__(self) -> None:
        self.candidates: list[CandidateRecord] = []

    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]):
        self.candidates.extend(candidates)
        return {"batch_id": batch_id, "count": len(candidates)}


def test_synthetic_supplement_collision_flags_both_candidates_without_winner():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = [
        SourceRecord(
            adapter="crossref",
            source_type="academic",
            stable_id=item["stable_id"],
            url=item["url"],
            title=item["title"],
            authors=item["authors"],
            published_at=item["published_at"],
            summary=None,
        )
        for item in payload["records"]
    ]
    sink = CollectingSink()

    counters = run_miner(
        [FixtureAdapter(records)],
        sink,
        budget=10,
        identity_verifier=ExactVerifier(),
    )

    assert len(sink.candidates) == 2
    assert {candidate.stable_id for candidate in sink.candidates} == {
        "10.1000/supplement-a",
        "10.1000/supplement-b",
    }
    assert all("DOI_IDENTITY_COLLISION" in candidate.warnings for candidate in sink.candidates)
    assert counters.identity_collisions == 2
