"""Abstracts from OpenAlex for collected Crossref papers that arrived without one.

Crossref deposits no abstract for about two thirds of the collected papers, and the target pipeline
cannot promote a paper from its title alone. OpenAlex holds an abstract for most of them. Each found
abstract becomes a new candidate (adapter ``openalex_abstract``) that supersedes the Crossref record;
the Crossref record itself is never rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime

from basketball_miner.models import CandidateRecord, SourceRecord
from basketball_miner.normalize import stable_candidate_id
from basketball_miner.relevance import classify_relevance

ADAPTER = "openalex_abstract"
SUMMARY_LIMIT = 1200
MIN_WORDS = 10  # the target's evidence gate treats shorter text as a placeholder
WARNING = "abstract-from-openalex"
# Shooting mechanics first: those are the coach's gap.
_PRIORITY = ("SHOOTING", "BIOMECHANICS")
_SPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def reconstruct_abstract(inverted_index: Mapping[str, list[int]] | None) -> str | None:
    """OpenAlex ships abstracts as word -> positions; put the words back in order."""
    if not inverted_index:
        return None
    positions = [(position, word) for word, places in inverted_index.items() for position in places]
    text = " ".join(word for _, word in sorted(positions))
    return text or None


def trim_abstract(text: str, limit: int = SUMMARY_LIMIT) -> str:
    text = _SPACE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [match.end() for match in _SENTENCE_END.finditer(head)]
    if ends:
        return head[: ends[-1]]
    return head[: head.rfind(" ")].rstrip() if " " in head else head


def _doi(record: Mapping) -> str:
    return str(record.get("stable_id", "")).strip().lower()


def select_papers(records: Iterable[dict], done: Iterable[str], limit: int) -> list[dict]:
    """Abstract-less Crossref papers not looked up yet, shooting and biomechanics first."""
    records = list(records)
    skip = {doi.lower() for doi in done}
    skip.update(_doi(r) for r in records if r.get("adapter") == ADAPTER)
    pending: dict[str, dict] = {}
    for record in sorted(records, key=lambda r: str(r.get("discovered_at", ""))):
        doi = _doi(record)
        if (
            record.get("adapter") != "crossref"
            or not doi.startswith("10.")
            or str(record.get("summary") or "").strip()
            or doi in skip
            or doi in pending
        ):
            continue
        pending[doi] = record

    def rank(record: dict) -> int:
        topics = record.get("topic_codes") or []
        return next((i for i, topic in enumerate(_PRIORITY) if topic in topics), len(_PRIORITY))

    return sorted(pending.values(), key=rank)[:limit]


def abstract_candidate(paper: dict, abstract: str, now: datetime) -> CandidateRecord:
    source = SourceRecord(
        adapter=ADAPTER,
        source_type="academic",
        stable_id=str(paper["stable_id"]),
        url=str(paper["url"]),
        title=str(paper["title"]),
        authors=list(paper.get("authors") or []),
        published_at=paper.get("published_at"),
        summary=abstract,
    )
    candidate_id, canonical_hash = stable_candidate_id(source)
    relevance = classify_relevance(source)
    topics = list(relevance.topic_codes) if relevance.relevant else list(paper.get("topic_codes") or [])
    signals = list(relevance.signals) if relevance.relevant else list(paper.get("relevance_signals") or [])
    return CandidateRecord(
        **source.model_dump(),
        candidate_id=candidate_id,
        canonical_hash=canonical_hash,
        topic_codes=topics,
        relevance_signals=signals,
        provenance="LINKED",
        warnings=[WARNING],
        supersedes_candidate_id=paper["candidate_id"],
        discovered_at=now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    )


def run_enrichment(
    records: Iterable[dict],
    state: dict,
    fetch: Callable[[list[str]], Mapping[str, str | None]],
    sink,
    *,
    now: datetime,
    limit: int,
    lookup_size: int = 50,
    file_size: int = 200,
) -> dict[str, int]:
    """Look up to `limit` papers and export what was found. Updates `state` in place.

    A failed lookup ends the run; papers not looked up stay pending for the next run.
    """
    done: list[str] = state.setdefault("done", [])
    counts = {"looked_up": 0, "exported": 0, "no_abstract": 0}
    found: list[CandidateRecord] = []
    finished: list[str] = []
    papers = select_papers(records, done, limit)

    for start in range(0, len(papers), lookup_size):
        chunk = papers[start : start + lookup_size]
        try:
            abstracts = fetch([str(p["stable_id"]) for p in chunk])
        except Exception:  # noqa: BLE001 - network or API trouble: the rest waits for the next run
            break
        lowered = {key.lower(): value for key, value in abstracts.items()}
        for paper in chunk:
            counts["looked_up"] += 1
            finished.append(_doi(paper))
            abstract = lowered.get(_doi(paper))
            abstract = trim_abstract(abstract) if abstract else None
            if not abstract or len(abstract.split()) < MIN_WORDS:
                counts["no_abstract"] += 1
                continue
            found.append(abstract_candidate(paper, abstract, now))

    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for number, start in enumerate(range(0, len(found), file_size), start=1):
        sink.write_batch(f"RUN-{stamp}-{ADAPTER}-{number:04d}", found[start : start + file_size])
    counts["exported"] = len(found)
    done.extend(finished)
    return counts
