from __future__ import annotations

import pytest
from pydantic import ValidationError

from basketball_miner.distill_v3.coach_bridge import (
    DEFAULT_CODEBOOK_PATH,
    CoachProjectionV1,
    load_codebook,
    stable_research_unit_id,
)


def _projection(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "coach-projection-v1",
        "knowledge_unit_id": "KU-SHOOT-DISTANCE-MILLER-1996-001",
        "domain_codes": ["SHOOTING", "BIOMECHANICS"],
        "metric_codes": ["JOINT_ANGLE"],
        "policy_codes": ["DO_NOT_OVERINFER"],
        "effect_code": "UNSPECIFIED",
        "evidence_code": "B",
        "projection_reason": "direct context-aware shooting evidence",
        "approved": True,
    }
    payload.update(overrides)
    return payload


def test_stable_id_is_repeatable_and_reserved() -> None:
    first = stable_research_unit_id("KU-SHOOT-DISTANCE-MILLER-1996-001")
    second = stable_research_unit_id("KU-SHOOT-DISTANCE-MILLER-1996-001")
    assert first == second
    assert 1_000_000_000_000 <= first < 2_100_000_000_000


def test_default_codebook_is_frozen_and_contains_only_approved_sentinels() -> None:
    codebook = load_codebook(DEFAULT_CODEBOOK_PATH)

    assert codebook.version == "coach-bridge-codes-v1"
    assert "SHOOTING" in codebook.domains
    assert "UNCLASSIFIED" in codebook.domains
    assert "JOINT_ANGLE" in codebook.metrics
    assert "UNMAPPED_METRIC" in codebook.metrics
    assert "GENERAL_GUIDANCE" in codebook.policies
    assert "MAGIC_METRIC" not in codebook.metrics


def test_projection_is_strict_and_requires_approved_true() -> None:
    projection = CoachProjectionV1.model_validate(_projection())
    assert projection.approved is True

    with pytest.raises(ValidationError):
        CoachProjectionV1.model_validate(_projection(approved=False))

    with pytest.raises(ValidationError):
        CoachProjectionV1.model_validate(_projection(unexpected="nope"))


def test_projection_rejects_bad_ku_and_unknown_code() -> None:
    with pytest.raises(ValidationError):
        CoachProjectionV1.model_validate(_projection(knowledge_unit_id="RU-0061"))

    with pytest.raises(ValidationError):
        CoachProjectionV1.model_validate(_projection(metric_codes=["MAGIC_METRIC"]))
