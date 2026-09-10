from basketball_miner.models import SourceRecord
from basketball_miner.relevance import classify_relevance


def make_source(title: str, summary: str | None = None) -> SourceRecord:
    return SourceRecord(
        adapter="test",
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
