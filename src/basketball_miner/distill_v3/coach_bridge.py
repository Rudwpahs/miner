from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LINKED_ID_BASE = 1_000_000_000_000

_ALLOWED_DOMAINS = frozenset(
    {
        "BIOMECHANICS",
        "COACHING_METHOD",
        "DECELERATION_COD",
        "DECISION",
        "DEFENSE",
        "FATIGUE",
        "FINISHING",
        "FOOTWORK",
        "HANDLE",
        "MOTOR_LEARNING",
        "PERCEPTION_GAZE",
        "PNR_TACTICS",
        "POSE_VALIDATION",
        "RELEASE_BALLISTICS",
        "SHOOTING",
        "SPACING_OFFBALL",
        "TRAINING_LOAD",
        "YOUTH",
        "UNCLASSIFIED",
    }
)
_ALLOWED_METRICS = frozenset(
    {
        "ACCELERATION",
        "ANGULAR_VELOCITY",
        "ASYMMETRY",
        "BACKSPIN",
        "COM_DISPLACEMENT",
        "DECELERATION",
        "DECISION_ACCURACY",
        "DEFENDER_DISTANCE",
        "ENTRY_ANGLE",
        "FIXATION_COUNT",
        "FIXATION_DURATION",
        "GRF_FORCE",
        "HEART_RATE",
        "JOINT_ANGLE",
        "JOINT_MOMENT_POWER",
        "JUMP_HEIGHT",
        "MOVEMENT_SPEED",
        "POSE_ERROR",
        "REACTION_TIME",
        "RELEASE_ANGLE",
        "RELEASE_HEIGHT",
        "RELEASE_TIMING",
        "RELEASE_VELOCITY",
        "SHOT_ACCURACY",
        "SHOT_CLOCK",
        "VARIABILITY",
        "UNMAPPED_METRIC",
    }
)
_ALLOWED_POLICIES = frozenset(
    {
        "CONFIDENCE_GATE",
        "DO_NOT_INFER_UNOBSERVABLE",
        "DO_NOT_OVERINFER",
        "HYPOTHESIS_ONLY",
        "PREFER_LONGITUDINAL",
        "PRESERVE_CONTRADICTION",
        "PROGRESSION",
        "REQUIRE_CONTEXT",
        "SEPARATE_DIMENSIONS",
        "USE_PERSONAL_BASELINE",
        "GENERAL_GUIDANCE",
    }
)
_ALLOWED_EFFECTS = frozenset(
    {
        "NO_SIGNIFICANT_DIFFERENCE",
        "INCREASE",
        "DECREASE",
        "ASSOCIATION",
        "DIFFERENCE",
        "OFFICIAL_GUIDANCE",
        "UNSPECIFIED",
    }
)
_ALLOWED_EVIDENCE = frozenset(
    {
        "A",
        "A-",
        "A+",
        "B",
        "B-",
        "B+",
        "C",
        "C-",
        "C+",
        "D",
        "D-",
        "D+",
        "E",
        "U",
    }
)


def stable_research_unit_id(knowledge_unit_id: str) -> int:
    """Return the deterministic Coach numeric id reserved for a canonical KU."""
    payload = f"coach-linked-v1:{knowledge_unit_id}".encode()
    return LINKED_ID_BASE + int(hashlib.sha256(payload).hexdigest()[:10], 16)


def _reject_unknown(values: list[str], allowed: frozenset[str], field_name: str) -> list[str]:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unknown {field_name}: {', '.join(unknown)}")
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field_name}")
    return values


class CoachProjectionV1(BaseModel):
    """Explicit, approved semantic projection from one canonical KU to Coach codes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach-projection-v1"]
    knowledge_unit_id: str = Field(pattern=r"^KU-[A-Z0-9][A-Z0-9-]{2,127}$")
    domain_codes: list[str] = Field(min_length=1)
    metric_codes: list[str] = Field(default_factory=list)
    policy_codes: list[str] = Field(default_factory=list)
    effect_code: str
    evidence_code: str
    projection_reason: str = Field(min_length=1, max_length=1000)
    approved: Literal[True]

    @field_validator("domain_codes")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        return _reject_unknown(values, _ALLOWED_DOMAINS, "domain_codes")

    @field_validator("metric_codes")
    @classmethod
    def validate_metrics(cls, values: list[str]) -> list[str]:
        return _reject_unknown(values, _ALLOWED_METRICS, "metric_codes")

    @field_validator("policy_codes")
    @classmethod
    def validate_policies(cls, values: list[str]) -> list[str]:
        return _reject_unknown(values, _ALLOWED_POLICIES, "policy_codes")

    @field_validator("effect_code")
    @classmethod
    def validate_effect(cls, value: str) -> str:
        if value not in _ALLOWED_EFFECTS:
            raise ValueError(f"unknown effect_code: {value}")
        return value

    @field_validator("evidence_code")
    @classmethod
    def validate_evidence(cls, value: str) -> str:
        if value not in _ALLOWED_EVIDENCE:
            raise ValueError(f"unknown evidence_code: {value}")
        return value
