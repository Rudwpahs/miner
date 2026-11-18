from basketball_miner.models import SourceRecord
from basketball_miner.relevance import classify_relevance


def make_source(title: str, summary: str | None = None, adapter: str = "test") -> SourceRecord:
    return SourceRecord(
        adapter=adapter,
        source_type="academic",
        stable_id=title,
        url="https://example.org/item",
        title=title,
        authors=[],
        published_at=None,
        summary=summary,
    )


def test_relevance_accepts_basketball_mechanics_and_maps_topics():
    result = classify_relevance(make_source("Basketball jump shot release angle biomechanics"))
    assert result.relevant is True
    assert "SHOOTING" in result.topic_codes
    assert "BIOMECHANICS" in result.topic_codes


def test_relevance_accepts_pick_and_roll_decision_making():
    result = classify_relevance(make_source("Pick and roll decision making under pressure"))
    assert result.relevant is True
    assert "PNR_TACTICS" in result.topic_codes
    assert "DECISION" in result.topic_codes


def test_relevance_accepts_defensive_closeout():
    result = classify_relevance(make_source("Basketball defensive closeout footwork"))
    assert result.relevant is True
    assert "DEFENSE" in result.topic_codes
    assert "FOOTWORK" in result.topic_codes


def test_relevance_rejects_unrelated_material():
    negatives = (
        "Football shooting technique",
        "Anterior knee surgery outcomes",
        "Crypto mining profitability",
        "Large language model inference optimization",
    )
    assert all(not classify_relevance(make_source(title)).relevant for title in negatives)


def test_allowlisted_youtube_channel_counts_as_basketball_without_the_word():
    # Coaching channels on the allowlist rarely say "basketball" in a title like this one.
    result = classify_relevance(
        make_source("Your Guide Hand Is Ruining Your Fadeaway... Fix It!", adapter="youtube_rss")
    )
    assert result.relevant is True
    assert result.signals == ("allowlisted basketball channel",)


def test_allowlisted_youtube_title_still_maps_shooting_topics():
    result = classify_relevance(make_source("Why Your Shot Drifts and Falls Short...", adapter="youtube_rss"))
    assert result.relevant is True
    assert "SHOOTING" in result.topic_codes


def test_the_word_basketball_is_still_required_outside_the_youtube_allowlist():
    assert classify_relevance(make_source("Why Your Shot Drifts and Falls Short...")).relevant is False
