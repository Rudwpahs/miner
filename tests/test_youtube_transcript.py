import json
from datetime import UTC, datetime

from basketball_miner.models import CandidateRecord
from basketball_miner.normalize import stable_candidate_id
from basketball_miner.sources.youtube_transcript import (
    ADAPTER,
    excerpt_candidate,
    extract_excerpt,
    run_excerpts,
    select_videos,
)

NOW = datetime(2026, 11, 12, 3, 4, 5, 678901, tzinfo=UTC)

TRANSCRIPT = (
    "[music] What's up gang. >> Your guide hand isn't supposed to shoot the basketball. "
    "If your guide hand is flicking or pushing the ball, you can create side spin and inconsistent misses. "
    "Then he gets an easy pass from the elbow. "
    "If your elbow constantly flares outward, you may have trouble controlling the direction of your shot. "
    "Subscribe for more videos."
)


def rss_record(video_id="OdZuZlUpb0w", title="Fix Your Shooting In 10 Minutes", topics=("SHOOTING",)):
    return {
        "adapter": "youtube_rss",
        "authors": ["The Hoop Sage"],
        "candidate_id": "CAND-0123456789abcdef",
        "canonical_hash": "0" * 64,
        "discovered_at": "2026-11-05T00:00:00Z",
        "provenance": "LINKED",
        "published_at": "2026-11-04T16:00:00+00:00",
        "relevance_signals": ["basketball"],
        "source_type": "coaching",
        "stable_id": video_id,
        "summary": "Original video link only",
        "supersedes_candidate_id": None,
        "title": title,
        "topic_codes": list(topics),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "warnings": [],
    }


def test_excerpt_keeps_mechanics_sentences_and_drops_noise():
    excerpt = extract_excerpt(TRANSCRIPT)
    assert "guide hand is flicking" in excerpt
    assert "elbow constantly flares outward" in excerpt
    assert "pass from the elbow" not in excerpt  # court location, not the shooter's arm
    assert "[music]" not in excerpt and ">>" not in excerpt
    assert "Subscribe" not in excerpt


def test_excerpt_needs_at_least_two_mechanics_sentences():
    assert extract_excerpt("Your guide hand matters. Subscribe now.") is None
    assert extract_excerpt("") is None


def test_excerpt_stays_within_the_inbox_summary_limit():
    long = " ".join(f"Your elbow should stay under the ball on shot number {i}." for i in range(200))
    excerpt = extract_excerpt(long)
    assert excerpt is not None and len(excerpt) <= 1200
    assert excerpt.endswith(".")


def test_select_videos_picks_untried_shooting_form_videos_once():
    records = [
        rss_record("AAAAAAAAAAA"),
        rss_record("AAAAAAAAAAA"),  # re-collected copy
        rss_record("BBBBBBBBBBB", title="Classic Popovich timeout", topics=("SHOOTING",)),
        rss_record("CCCCCCCCCCC", title="Elite Shooting Drill", topics=("COACHING_METHOD",)),
        rss_record("DDDDDDDDDDD", title="The 3 Pillars of an Elite Shooter"),
        {**rss_record("EEEEEEEEEEE"), "adapter": "crossref"},
    ]
    picked = select_videos(records, done={"DDDDDDDDDDD"}, limit=5)
    assert [r["stable_id"] for r in picked] == ["AAAAAAAAAAA"]


def test_excerpt_candidate_is_a_new_candidate_labelled_as_a_transcript_excerpt():
    candidate = excerpt_candidate(rss_record(), "Your elbow stays under the ball. Your guide hand only balances it.", NOW)
    CandidateRecord.model_validate(candidate.model_dump(mode="json"))
    assert candidate.adapter == ADAPTER == "youtube_transcript"
    assert candidate.source_type == "coaching"
    assert candidate.stable_id == "OdZuZlUpb0w#transcript"
    assert candidate.title == "Fix Your Shooting In 10 Minutes [coaching video transcript excerpt]"
    assert candidate.candidate_id != "CAND-0123456789abcdef"
    assert (candidate.candidate_id, candidate.canonical_hash) == stable_candidate_id(candidate)
    assert candidate.topic_codes == ["SHOOTING"]
    assert candidate.discovered_at == "2026-11-12T03:04:05.678901Z"
    assert "expert-opinion" in candidate.warnings


class FakeSink:
    def __init__(self):
        self.batches = []

    def write_batch(self, batch_id, candidates):
        self.batches.append((batch_id, candidates))


def test_run_exports_one_batch_and_remembers_outcomes():
    records = [rss_record("AAAAAAAAAAA"), rss_record("BBBBBBBBBBB"), rss_record("CCCCCCCCCCC")]
    transcripts = {"AAAAAAAAAAA": TRANSCRIPT, "BBBBBBBBBBB": "Nothing about form here. Just a highlight."}

    def fetch(video_id):
        if video_id == "CCCCCCCCCCC":
            raise RuntimeError("blocked")
        return transcripts[video_id]

    sink = FakeSink()
    state = {"done": [], "failures": {}}
    summary = run_excerpts(records, state, fetch, sink, now=NOW, limit=5, max_attempts=3, pause=lambda: None)

    assert summary == {"tried": 3, "exported": 1, "no_excerpt": 1, "failed": 1}
    [(batch_id, candidates)] = sink.batches
    assert batch_id == "RUN-20261112T030405678901Z-youtube_transcript-0001"
    assert [c.stable_id for c in candidates] == ["AAAAAAAAAAA#transcript"]
    assert sorted(state["done"]) == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert state["failures"] == {"CCCCCCCCCCC": 1}
    json.dumps(state)


def test_a_video_that_keeps_failing_is_given_up_after_max_attempts():
    state = {"done": [], "failures": {"CCCCCCCCCCC": 2}}

    def fetch(video_id):
        raise RuntimeError("blocked")

    summary = run_excerpts([rss_record("CCCCCCCCCCC")], state, fetch, FakeSink(), now=NOW, limit=5,
                           max_attempts=3, pause=lambda: None)
    assert summary["failed"] == 1
    assert state["done"] == ["CCCCCCCCCCC"] and state["failures"] == {}


def test_a_failed_export_leaves_the_videos_to_retry():
    class BrokenSink:
        def write_batch(self, batch_id, candidates):
            raise RuntimeError("github down")

    state = {"done": [], "failures": {}}
    try:
        run_excerpts([rss_record("AAAAAAAAAAA")], state, lambda _: TRANSCRIPT, BrokenSink(), now=NOW, limit=5,
                     max_attempts=3, pause=lambda: None)
    except RuntimeError:
        pass
    assert state["done"] == []


def test_nothing_is_exported_when_no_video_yields_an_excerpt():
    sink = FakeSink()
    run_excerpts([rss_record("BBBBBBBBBBB")], {"done": [], "failures": {}}, lambda _: "Highlights only.",
                 sink, now=NOW, limit=5, max_attempts=3, pause=lambda: None)
    assert sink.batches == []
