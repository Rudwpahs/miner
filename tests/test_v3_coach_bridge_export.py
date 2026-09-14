from __future__ import annotations

import hashlib

import pytest

from basketball_miner.distill_v3.coach_bridge import (
    BridgeExportError,
    CoachProjectionV1,
    build_linked_bundle,
    write_linked_bundle,
)


def _canonical(
    knowledge_unit_id: str = "KU-SHOOT-DISTANCE-MILLER-1996-001",
    *,
    provenance_status: str = "LINKED",
) -> dict[str, object]:
    return {
        "knowledge_unit_id": knowledge_unit_id,
        "source_candidate_id": "CAND-993d1c1026b7351b",
        "claim": "Shot distance can alter release and body kinematics.",
        "source_url": "https://doi.org/10.1080/026404196367895",
        "source_identifier": "10.1080/026404196367895",
        "source_title": "The relationship between basketball shooting kinematics, distance and playing position",
        "evidence_strength": "HIGH_FOR_STUDIED_SAMPLE",
        "confidence": 0.94,
        "provenance": {"adapter": "crossref", "status": provenance_status},
        "contradiction_or_limitation": "Observed adaptations are not universal prescriptions.",
        "safe_inference": {
            "may_infer": ["Shot distance can alter release and body kinematics."],
            "may_not_infer": ["Every shooter must reproduce the reported angles."],
        },
    }


def _projection(knowledge_unit_id: str = "KU-SHOOT-DISTANCE-MILLER-1996-001") -> CoachProjectionV1:
    return CoachProjectionV1.model_validate(
        {
            "schema_version": "coach-projection-v1",
            "knowledge_unit_id": knowledge_unit_id,
            "domain_codes": ["SHOOTING", "BIOMECHANICS"],
            "metric_codes": ["JOINT_ANGLE"],
            "policy_codes": ["DO_NOT_OVERINFER", "REQUIRE_CONTEXT"],
            "effect_code": "ASSOCIATION",
            "evidence_code": "B+",
            "projection_reason": "direct distance-dependent kinematics evidence",
            "approved": True,
        }
    )


def test_build_linked_bundle_exports_only_explicit_linked_matches() -> None:
    linked = _canonical()
    row_only = _canonical("KU-ROW-ONLY-001", provenance_status="ROW_ONLY")

    bundle = build_linked_bundle([row_only, linked], [_projection()])

    assert len(bundle.units) == 1
    unit = bundle.units[0]
    assert unit.knowledge_unit_id == linked["knowledge_unit_id"]
    assert unit.provenance_code == "LINKED"
    assert unit.metric_codes == ["JOINT_ANGLE"]
    assert unit.source_ids == [bundle.sources[0].source_id]
    assert bundle.sources[0].url == "https://doi.org/10.1080/026404196367895"
    assert bundle.skipped == {
        "projection_missing": 1,
    }


def test_missing_projection_and_unlinked_records_never_become_row_only_output() -> None:
    bundle = build_linked_bundle(
        [_canonical("KU-NO-PROJECTION-001"), _canonical("KU-UNLINKED-001", provenance_status="ROW_ONLY")],
        [],
    )

    assert bundle.units == []
    assert bundle.sources == []
    assert bundle.skipped == {"projection_missing": 1, "provenance_not_linked": 1}


def test_conflicting_duplicate_ku_and_numeric_collision_fail_closed() -> None:
    first = _canonical()
    conflicting = {**first, "claim": "A conflicting duplicate claim."}
    with pytest.raises(BridgeExportError, match="conflicting duplicate knowledge_unit_id"):
        build_linked_bundle([first, conflicting], [_projection()])

    second_id = "KU-SHOOT-CONTEST-ROJAS-2000-001"
    with pytest.raises(BridgeExportError, match="research_unit_id collision"):
        build_linked_bundle(
            [first, _canonical(second_id)],
            [_projection(), _projection(second_id)],
            id_func=lambda _knowledge_unit_id: 1_000_000_000_001,
        )


def test_serialization_is_byte_deterministic_and_manifest_hashes_match(tmp_path) -> None:
    second_id = "KU-SHOOT-CONTEST-ROJAS-2000-001"
    records = [_canonical(), _canonical(second_id)]
    projections = [_projection(), _projection(second_id)]
    first_bundle = build_linked_bundle(records, projections)
    second_bundle = build_linked_bundle(list(reversed(records)), list(reversed(projections)))

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_manifest = write_linked_bundle(first_bundle, first_dir)
    second_manifest = write_linked_bundle(second_bundle, second_dir)

    first_units = (first_dir / "units.jsonl").read_bytes()
    second_units = (second_dir / "units.jsonl").read_bytes()
    first_sources = (first_dir / "sources.jsonl").read_bytes()
    second_sources = (second_dir / "sources.jsonl").read_bytes()

    assert first_units == second_units
    assert first_sources == second_sources
    assert first_manifest.units_sha256 == hashlib.sha256(first_units).hexdigest()
    assert first_manifest.sources_sha256 == hashlib.sha256(first_sources).hexdigest()
    assert first_manifest.units_sha256 == second_manifest.units_sha256
    assert first_manifest.sources_sha256 == second_manifest.sources_sha256
