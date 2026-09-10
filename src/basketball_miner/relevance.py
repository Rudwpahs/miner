from __future__ import annotations

from dataclasses import dataclass

from basketball_miner.models import SourceRecord

_ANCHORS = (
    "basketball",
    "hoops",
    "jump shot",
    "free throw",
    "pick and roll",
    "layup",
    "dribble",
    "closeout",
)

_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "SHOOTING",
        ("jump shot", "shooting", "shot", "free throw", "release", "layup"),
    ),
    (
        "BIOMECHANICS",
        (
            "biomechanic",
            "release angle",
            "release velocity",
            "kinematic",
            "kinetic",
            "joint",
            "elbow",
            "knee",
            "hip",
        ),
    ),
    ("DECISION", ("decision", "read", "choice", "perception", "reaction")),
    ("DEFENSE", ("defense", "defensive", "closeout", "contest")),
    ("FOOTWORK", ("footwork", "stance", "pivot", "step")),
    ("MOTOR_LEARNING", ("motor learning", "skill acquisition", "practice", "feedback")),
    ("FATIGUE", ("fatigue", "tired", "workload")),
    ("PNR_TACTICS", ("pick and roll", "pick-and-roll", "pnr")),
    ("SPACING_OFFBALL", ("spacing", "off-ball", "off ball", "cut", "screen")),
    ("YOUTH", ("youth", "adolescent", "junior")),
    ("COACHING_METHOD", ("coach", "coaching", "drill", "teaching", "cue")),
)


@dataclass(frozen=True)
class RelevanceResult:
    relevant: bool
    topic_codes: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()


def classify_relevance(record: SourceRecord) -> RelevanceResult:
    text = " ".join(part for part in (record.title, record.summary or "") if part).casefold()
    anchors = tuple(anchor for anchor in _ANCHORS if anchor in text)
    if not anchors:
        return RelevanceResult(relevant=False)

    topics = tuple(
        topic
        for topic, terms in _TOPIC_RULES
        if any(term in text for term in terms)
    )
    signals = anchors[:16]
    return RelevanceResult(relevant=True, topic_codes=topics[:12], signals=signals)
