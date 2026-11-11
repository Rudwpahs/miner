"""Shooting-mechanics excerpts from the captions of already-collected YouTube coaching videos.

The RSS adapter only sees a title and a description, so its candidates are title-only and can never
be promoted. For SHOOTING videos this module reads the video's captions, keeps the few sentences that
talk about the shooter's body (elbow, guide hand, dip, legs, wrist, release), and emits them as a new
candidate under its own adapter. The excerpt is a coach's opinion, not a measurement, and is labelled
so. The full transcript is never stored.

Captions are fetched from the owner's PC: YouTube blocks most cloud IPs, so this does not run in the
hosted miner workflow.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from basketball_miner.models import CandidateRecord, SourceRecord
from basketball_miner.normalize import stable_candidate_id

ADAPTER = "youtube_transcript"
TITLE_SUFFIX = " [coaching video transcript excerpt]"
SUMMARY_LIMIT = 1200
MIN_SENTENCES = 2
MIN_WORDS = 10

_FORM_TITLE = re.compile(r"shoot|shot|jumper|form|elbow|guide hand|release|airball", re.IGNORECASE)
# Sentences about the shooter's body. "elbow" alone is often a court location ("pass from the elbow").
_MECHANICS = re.compile(
    r"guide hand|off hand|non-shooting hand|\bdip\b|\bwrist|\bknees?\b|\blegs?\b|set point|release point|"
    r"quick release|follow through|shot pocket|"
    r"\b(?:your|his|her|their|my|the shooting) elbow|\belbow (?:is|stays?|under|out|in|flares?|above|below|"
    r"position|alignment|working|tucked)",
    re.IGNORECASE,
)
_NOISE = re.compile(r"\[[^\]]*\]|>>")
_SPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _clean(text: str) -> str:
    return _SPACE.sub(" ", _NOISE.sub(" ", text)).strip()


def extract_excerpt(transcript: str, limit: int = SUMMARY_LIMIT) -> str | None:
    """The transcript's shooting-mechanics sentences, in order, within the inbox summary limit."""
    kept: list[str] = []
    seen: set[str] = set()
    length = 0
    for sentence in _SENTENCE.split(_clean(transcript)):
        sentence = sentence.strip()
        if not sentence or not _MECHANICS.search(sentence):
            continue
        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        key = sentence.casefold()
        if key in seen:
            continue
        added = len(sentence) + (1 if kept else 0)
        if length + added > limit:
            break
        kept.append(sentence)
        seen.add(key)
        length += added
    excerpt = " ".join(kept)
    if len(kept) < MIN_SENTENCES or len(excerpt.split()) < MIN_WORDS:
        return None
    return excerpt


def select_videos(records: Iterable[dict], done: Iterable[str], limit: int) -> list[dict]:
    """Collected SHOOTING videos whose titles are about shooting form, not tried yet, oldest first."""
    done_ids = set(done)
    picked: list[dict] = []
    for record in sorted(records, key=lambda r: str(r.get("discovered_at", ""))):
        video_id = str(record.get("stable_id", ""))
        if (
            record.get("adapter") != "youtube_rss"
            or "SHOOTING" not in (record.get("topic_codes") or [])
            or not _FORM_TITLE.search(str(record.get("title", "")))
            or video_id in done_ids
        ):
            continue
        done_ids.add(video_id)
        picked.append(record)
        if len(picked) >= limit:
            break
    return picked


def excerpt_candidate(video: dict, excerpt: str, now: datetime) -> CandidateRecord:
    title = str(video["title"])[: 500 - len(TITLE_SUFFIX)] + TITLE_SUFFIX
    source = SourceRecord(
        adapter=ADAPTER,
        source_type="coaching",
        stable_id=f"{video['stable_id']}#transcript",
        url=str(video["url"]),
        title=title,
        authors=list(video.get("authors") or []),
        published_at=video.get("published_at"),
        summary=excerpt,
    )
    candidate_id, canonical_hash = stable_candidate_id(source)
    return CandidateRecord(
        **source.model_dump(),
        candidate_id=candidate_id,
        canonical_hash=canonical_hash,
        topic_codes=list(video.get("topic_codes") or []),
        relevance_signals=list(video.get("relevance_signals") or []),
        provenance="LINKED",
        warnings=["expert-opinion", "transcript-excerpt"],
        discovered_at=now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    )


def run_excerpts(
    records: Iterable[dict],
    state: dict,
    fetch: Callable[[str], str],
    sink,
    *,
    now: datetime,
    limit: int,
    max_attempts: int,
    pause: Callable[[], None],
) -> dict[str, int]:
    """Try up to `limit` videos and export one batch. Updates `state` in place."""
    done: list[str] = state.setdefault("done", [])
    failures: dict[str, int] = state.setdefault("failures", {})
    counts = {"tried": 0, "exported": 0, "no_excerpt": 0, "failed": 0}
    candidates: list[CandidateRecord] = []
    finished: list[str] = []

    for index, video in enumerate(select_videos(records, done, limit)):
        if index:
            pause()
        video_id = str(video["stable_id"])
        counts["tried"] += 1
        try:
            transcript = fetch(video_id)
        except Exception:  # noqa: BLE001 - blocked, no captions, network: retry on a later day
            counts["failed"] += 1
            failures[video_id] = failures.get(video_id, 0) + 1
            if failures[video_id] >= max_attempts:
                failures.pop(video_id)
                done.append(video_id)
            continue
        failures.pop(video_id, None)
        excerpt = extract_excerpt(transcript)
        if excerpt is None:
            counts["no_excerpt"] += 1
            done.append(video_id)
            continue
        candidates.append(excerpt_candidate(video, excerpt, now))
        finished.append(video_id)

    if candidates:
        run_id = now.astimezone(UTC).strftime("RUN-%Y%m%dT%H%M%S%fZ")
        sink.write_batch(f"{run_id}-{ADAPTER}-0001", candidates)
        counts["exported"] = len(candidates)
        done.extend(finished)  # only after the export succeeded
    return counts
