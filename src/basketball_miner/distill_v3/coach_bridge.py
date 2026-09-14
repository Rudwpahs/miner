from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ids import normalize_doi

LINKED_ID_BASE = 1_000_000_000_000
DEFAULT_CODEBOOK_PATH = Path(__file__).resolve().parents[3] / "config" / "coach_bridge_v1_codes.json"


class CoachBridgeCodebookV1(BaseModel):
    """Frozen controlled vocabulary used by Coach Provenance Bridge V1."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["coach-bridge-codes-v1"]
    domains: list[str] = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    policies: list[str] = Field(min_length=1)
    effects: list[str] = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)

    @field_validator("domains", "metrics", "policies", "effects", "evidence")
    @classmethod
    def reject_duplicates(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("codebook entries must be unique")
        return values


def load_codebook(path: Path = DEFAULT_CODEBOOK_PATH) -> CoachBridgeCodebookV1:
    """Load the frozen bridge codebook from disk."""
    return CoachBridgeCodebookV1.model_validate_json(path.read_text(encoding="utf-8"))


_CODEBOOK = load_codebook()
_ALLOWED_DOMAINS = frozenset(_CODEBOOK.domains)
_ALLOWED_METRICS = frozenset(_CODEBOOK.metrics)
_ALLOWED_POLICIES = frozenset(_CODEBOOK.policies)
_ALLOWED_EFFECTS = frozenset(_CODEBOOK.effects)
_ALLOWED_EVIDENCE = frozenset(_CODEBOOK.evidence)


class BridgeExportError(ValueError):
    """Raised when a linked Coach bundle cannot be exported without ambiguity."""


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


class LinkedSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_identifier: str
    title: str
    url: str
    adapter: str


class LinkedCoachUnitV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_unit_id: int
    knowledge_unit_id: str
    claim: str
    domain_codes: list[str]
    metric_codes: list[str]
    policy_codes: list[str]
    effect_code: str
    evidence_code: str
    provenance_code: Literal["LINKED"] = "LINKED"
    source_ids: list[str]
    confidence: float
    limitation: str
    may_infer: list[str]
    may_not_infer: list[str]
    projection_reason: str


class LinkedCoachBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach-linked-bundle-v1"] = "coach-linked-bundle-v1"
    canonical_input_count: int
    projection_input_count: int
    collision_count: int = 0
    units: list[LinkedCoachUnitV1]
    sources: list[LinkedSourceV1]
    skipped: dict[str, int]


class LinkedCoachManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coach-linked-manifest-v1"] = "coach-linked-manifest-v1"
    exporter_version: Literal["coach-provenance-bridge-v1"] = "coach-provenance-bridge-v1"
    id_derivation_version: Literal["coach-linked-v1"] = "coach-linked-v1"
    codebook_version: Literal["coach-bridge-codes-v1"] = "coach-bridge-codes-v1"
    canonical_input_count: int
    projection_input_count: int
    exported_unit_count: int
    source_count: int
    linked_count: int
    skipped: dict[str, int]
    collision_count: int
    unit_ordering: Literal["research_unit_id,knowledge_unit_id"] = (
        "research_unit_id,knowledge_unit_id"
    )
    source_ordering: Literal["source_id"] = "source_id"
    units_sha256: str
    sources_sha256: str


def _required_text(record: dict[str, Any], field_name: str, knowledge_unit_id: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise BridgeExportError(f"{knowledge_unit_id}: missing {field_name}")
    return value.strip()


def _optional_text(record: dict[str, Any], field_name: str) -> str:
    value = record.get(field_name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BridgeExportError(f"invalid {field_name}")
    return value.strip()


def _normalize_source(
    record: dict[str, Any],
    provenance: dict[str, Any],
    knowledge_unit_id: str,
) -> LinkedSourceV1 | None:
    source_url_raw = record.get("source_url")
    source_title_raw = record.get("source_title")
    if not isinstance(source_url_raw, str) or not source_url_raw.strip():
        return None
    if not isinstance(source_title_raw, str) or not source_title_raw.strip():
        return None

    adapter = provenance.get("adapter")
    if not isinstance(adapter, str) or not adapter.strip():
        raise BridgeExportError(f"{knowledge_unit_id}: missing provenance adapter")
    adapter = adapter.strip()

    source_identifier = _optional_text(record, "source_identifier")
    source_url = source_url_raw.strip()
    source_title = source_title_raw.strip()

    doi = normalize_doi(source_identifier) if source_identifier else None
    if doi is None:
        doi = normalize_doi(source_url)
    if doi is not None:
        source_identifier = doi
        source_url = f"https://doi.org/{doi}"

    identity = "\n".join(
        (
            adapter.casefold(),
            source_identifier.casefold() if source_identifier else source_url.casefold(),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return LinkedSourceV1(
        source_id=f"SRC-LINKED-{digest[:12].upper()}",
        source_identifier=source_identifier,
        title=source_title,
        url=source_url,
        adapter=adapter,
    )


def _canonical_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        knowledge_unit_id = record.get("knowledge_unit_id")
        if not isinstance(knowledge_unit_id, str) or not knowledge_unit_id.startswith("KU-"):
            raise BridgeExportError("malformed knowledge_unit_id")
        existing = by_id.get(knowledge_unit_id)
        if existing is not None and existing != record:
            raise BridgeExportError(
                f"conflicting duplicate knowledge_unit_id: {knowledge_unit_id}"
            )
        by_id[knowledge_unit_id] = record
    return by_id


def _projection_index(projections: list[CoachProjectionV1]) -> dict[str, CoachProjectionV1]:
    by_id: dict[str, CoachProjectionV1] = {}
    for projection in projections:
        existing = by_id.get(projection.knowledge_unit_id)
        if existing is not None and existing != projection:
            raise BridgeExportError(
                f"conflicting duplicate projection: {projection.knowledge_unit_id}"
            )
        by_id[projection.knowledge_unit_id] = projection
    return by_id


def build_linked_bundle(
    records: list[dict[str, Any]],
    projections: list[CoachProjectionV1],
    *,
    id_func: Callable[[str], int] = stable_research_unit_id,
) -> LinkedCoachBundleV1:
    """Build a deterministic LINKED-only Coach bundle from reviewed canonical KUs."""
    canonical_by_id = _canonical_index(records)
    projection_by_id = _projection_index(projections)
    units: list[LinkedCoachUnitV1] = []
    sources_by_id: dict[str, LinkedSourceV1] = {}
    numeric_ids: dict[int, str] = {}
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for knowledge_unit_id in sorted(canonical_by_id):
        record = canonical_by_id[knowledge_unit_id]
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("status") != "LINKED":
            skip("provenance_not_linked")
            continue

        projection = projection_by_id.get(knowledge_unit_id)
        if projection is None:
            skip("projection_missing")
            continue

        source = _normalize_source(record, provenance, knowledge_unit_id)
        if source is None:
            skip("source_missing")
            continue

        research_unit_id = id_func(knowledge_unit_id)
        previous_ku = numeric_ids.get(research_unit_id)
        if previous_ku is not None and previous_ku != knowledge_unit_id:
            raise BridgeExportError(
                "research_unit_id collision: "
                f"{research_unit_id} maps both {previous_ku} and {knowledge_unit_id}"
            )
        numeric_ids[research_unit_id] = knowledge_unit_id

        existing_source = sources_by_id.get(source.source_id)
        if existing_source is not None and existing_source != source:
            raise BridgeExportError(f"conflicting source identity: {source.source_id}")
        sources_by_id[source.source_id] = source

        safe_inference = record.get("safe_inference")
        if not isinstance(safe_inference, dict):
            raise BridgeExportError(f"{knowledge_unit_id}: missing safe_inference")
        may_infer = safe_inference.get("may_infer")
        may_not_infer = safe_inference.get("may_not_infer")
        if not isinstance(may_infer, list) or not all(isinstance(x, str) for x in may_infer):
            raise BridgeExportError(f"{knowledge_unit_id}: invalid may_infer")
        if not isinstance(may_not_infer, list) or not all(
            isinstance(x, str) for x in may_not_infer
        ):
            raise BridgeExportError(f"{knowledge_unit_id}: invalid may_not_infer")

        confidence = record.get("confidence")
        if not isinstance(confidence, int | float) or isinstance(confidence, bool):
            raise BridgeExportError(f"{knowledge_unit_id}: invalid confidence")

        units.append(
            LinkedCoachUnitV1(
                research_unit_id=research_unit_id,
                knowledge_unit_id=knowledge_unit_id,
                claim=_required_text(record, "claim", knowledge_unit_id),
                domain_codes=projection.domain_codes,
                metric_codes=projection.metric_codes,
                policy_codes=projection.policy_codes,
                effect_code=projection.effect_code,
                evidence_code=projection.evidence_code,
                source_ids=[source.source_id],
                confidence=float(confidence),
                limitation=_required_text(
                    record, "contradiction_or_limitation", knowledge_unit_id
                ),
                may_infer=may_infer,
                may_not_infer=may_not_infer,
                projection_reason=projection.projection_reason,
            )
        )

    units.sort(key=lambda unit: (unit.research_unit_id, unit.knowledge_unit_id))
    sources = sorted(sources_by_id.values(), key=lambda source: source.source_id)
    return LinkedCoachBundleV1(
        canonical_input_count=len(records),
        projection_input_count=len(projections),
        units=units,
        sources=sources,
        skipped=dict(sorted(skipped.items())),
    )


def _jsonl_bytes(models: list[BaseModel]) -> bytes:
    lines = [
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for model in models
    ]
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode()


def _canonical_json_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _atomic_write_files(output_dir: Path, files: dict[str, bytes]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: dict[str, Path] = {}
    try:
        for name, payload in files.items():
            temp_path = output_dir / f".{name}.tmp"
            temp_path.write_bytes(payload)
            temporary[name] = temp_path
        for name in files:
            temporary[name].replace(output_dir / name)
    finally:
        for temp_path in temporary.values():
            temp_path.unlink(missing_ok=True)


def write_linked_bundle(
    bundle: LinkedCoachBundleV1,
    output_dir: Path,
) -> LinkedCoachManifestV1:
    """Write deterministic JSONL bundle files and a hash-bound manifest."""
    units_bytes = _jsonl_bytes(bundle.units)
    sources_bytes = _jsonl_bytes(bundle.sources)
    manifest = LinkedCoachManifestV1(
        canonical_input_count=bundle.canonical_input_count,
        projection_input_count=bundle.projection_input_count,
        exported_unit_count=len(bundle.units),
        source_count=len(bundle.sources),
        linked_count=len(bundle.units),
        skipped=bundle.skipped,
        collision_count=bundle.collision_count,
        units_sha256=hashlib.sha256(units_bytes).hexdigest(),
        sources_sha256=hashlib.sha256(sources_bytes).hexdigest(),
    )
    manifest_bytes = _canonical_json_bytes(manifest)
    _atomic_write_files(
        output_dir,
        {
            "units.jsonl": units_bytes,
            "sources.jsonl": sources_bytes,
            "manifest.json": manifest_bytes,
        },
    )
    return manifest
