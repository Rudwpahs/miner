import json
from datetime import UTC, datetime

from basketball_miner.models import CandidateRecord
from basketball_miner.normalize import stable_candidate_id
from basketball_miner.sources.openalex_abstract import (
    ADAPTER,
    abstract_candidate,
    reconstruct_abstract,
    run_enrichment,
    select_papers,
    trim_abstract,
)

NOW = datetime(2026, 11, 19, 1, 2, 3, 456789, tzinfo=UTC)
ABSTRACT = (
    "This study measured elbow alignment and guide hand position in 24 collegiate basketball players. "
    "Players with a laterally deviated elbow shot 9% worse from the free throw line. "
    "Guide hand contact after release increased lateral misses."
)


def crossref(doi="10.1000/abc", title="Elbow alignment in the basketball jump shot", topics=("SHOOTING",),
             summary=None, candidate_id="CAND-0123456789abcdef", discovered_at="2026-11-01T00:00:00Z"):
    return {
        "adapter": "crossref",
        "authors": ["A. Researcher"],
        "candidate_id": candidate_id,
        "canonical_hash": "0" * 64,
        "discovered_at": discovered_at,
        "provenance": "LINKED",
        "published_at": "2020-01-01",
        "relevance_signals": ["basketball", "jump shot"],
        "source_type": "academic",
        "stable_id": doi,
        "summary": summary,
        "supersedes_candidate_id": None,
        "title": title,
        "topic_codes": list(topics),
        "url": f"https://doi.org/{doi}",
        "warnings": [],
    }


def test_reconstruct_abstract_orders_words_by_position():
    index = {"elbow": [1], "The": [0], "flares.": [2]}
    assert reconstruct_abstract(index) == "The elbow flares."
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_trim_abstract_cuts_at_a_sentence_inside_the_limit():
    long = " ".join(f"Sentence number {i} about the release angle." for i in range(100))
    trimmed = trim_abstract(long)
    assert len(trimmed) <= 1200
    assert trimmed.endswith(".")
    assert trim_abstract("  Short   abstract text.  ") == "Short abstract text."


def test_select_papers_takes_abstractless_crossref_shooting_first_once_per_doi():
    records = [
        crossref("10.1/defense", title="Basketball defensive rotations", topics=("DEFENSE",)),
        crossref("10.1/shot", topics=("SHOOTING", "BIOMECHANICS")),
        crossref("10.1/SHOT", topics=("SHOOTING",)),  # same DOI, other case
        crossref("10.1/has-abstract", summary="Already has an abstract of enough words to be useful here."),
        crossref("10.1/done"),
        {**crossref("10.1/yt"), "adapter": "youtube_rss"},
    ]
    picked = select_papers(records, done={"10.1/done"}, limit=10)
    assert [p["stable_id"] for p in picked] == ["10.1/shot", "10.1/defense"]


def test_select_papers_skips_dois_already_enriched_in_the_inbox():
    enriched = {**crossref("10.1/shot"), "adapter": ADAPTER, "summary": ABSTRACT}
    assert select_papers([crossref("10.1/shot"), enriched], done=set(), limit=10) == []


def test_abstract_candidate_supersedes_the_crossref_record():
    candidate = abstract_candidate(crossref(), ABSTRACT, NOW)
    CandidateRecord.model_validate(candidate.model_dump(mode="json"))
    assert candidate.adapter == ADAPTER == "openalex_abstract"
    assert candidate.supersedes_candidate_id == "CAND-0123456789abcdef"
    assert candidate.candidate_id != "CAND-0123456789abcdef"
    assert (candidate.candidate_id, candidate.canonical_hash) == stable_candidate_id(candidate)
    assert candidate.summary == ABSTRACT
    assert candidate.title == "Elbow alignment in the basketball jump shot"
    assert candidate.source_type == "academic"
    assert "SHOOTING" in candidate.topic_codes and "BIOMECHANICS" in candidate.topic_codes
    assert candidate.warnings == ["abstract-from-openalex"]
    assert candidate.discovered_at == "2026-11-19T01:02:03.456789Z"


class FakeSink:
    def __init__(self):
        self.batches = []

    def write_batch(self, batch_id, candidates):
        self.batches.append((batch_id, candidates))


def test_run_exports_in_files_and_remembers_every_looked_up_doi():
    records = [crossref(f"10.1/p{i}", candidate_id=f"CAND-{i:016x}") for i in range(5)]
    calls = []

    def fetch(dois):
        calls.append(list(dois))
        return {doi: (ABSTRACT if doi != "10.1/p3" else None) for doi in dois}

    sink = FakeSink()
    state = {"done": []}
    counts = run_enrichment(records, state, fetch, sink, now=NOW, limit=5, lookup_size=2, file_size=3)

    assert calls == [["10.1/p0", "10.1/p1"], ["10.1/p2", "10.1/p3"], ["10.1/p4"]]
    assert counts == {"looked_up": 5, "exported": 4, "no_abstract": 1}
    assert [batch_id for batch_id, _ in sink.batches] == [
        "RUN-20261119T010203456789Z-openalex_abstract-0001",
        "RUN-20261119T010203456789Z-openalex_abstract-0002",
    ]
    assert [len(c) for _, c in sink.batches] == [3, 1]
    assert sorted(state["done"]) == [f"10.1/p{i}" for i in range(5)]
    json.dumps(state)


def test_a_failed_lookup_stops_the_run_and_keeps_what_was_found():
    records = [crossref(f"10.1/p{i}", candidate_id=f"CAND-{i:016x}") for i in range(4)]

    def fetch(dois):
        if "10.1/p2" in dois:
            raise RuntimeError("openalex down")
        return {doi: ABSTRACT for doi in dois}

    sink = FakeSink()
    state = {"done": []}
    counts = run_enrichment(records, state, fetch, sink, now=NOW, limit=4, lookup_size=2, file_size=10)
    assert counts["exported"] == 2
    assert sorted(state["done"]) == ["10.1/p0", "10.1/p1"]
    assert len(sink.batches) == 1


def test_short_abstracts_are_not_exported():
    def fetch(dois):
        return {doi: "Abstract to be supplied." for doi in dois}

    sink = FakeSink()
    counts = run_enrichment([crossref()], {"done": []}, fetch, sink, now=NOW, limit=1)
    assert counts == {"looked_up": 1, "exported": 0, "no_abstract": 1}
    assert sink.batches == []
