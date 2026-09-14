from __future__ import annotations

from copy import deepcopy

from .models import AuditPromotion, ConceptAction, SemanticResult


def validate_promotion(
    result: SemanticResult,
    *,
    knowledge_unit_id: str,
    concept_id: str,
    concept_action: ConceptAction,
    canonical_date: str,
) -> AuditPromotion:
    if result.stage != "JUDGE" or result.decision != "CONFIRM":
        raise ValueError("canonical promotion requires Judge CONFIRM")
    if not knowledge_unit_id.strip() or result.knowledge_unit_id != knowledge_unit_id:
        raise ValueError("knowledge_unit_id must match confirmed Judge result")
    if not concept_id.strip() or result.concept_id != concept_id:
        raise ValueError("concept_id must match confirmed Judge result")
    if result.concept_action is not None and result.concept_action != concept_action:
        raise ValueError("concept_action must match confirmed Judge result")

    return AuditPromotion(
        candidate_id=result.candidate_id,
        knowledge_unit_id=knowledge_unit_id,
        concept_id=concept_id,
        judge_decision="CONFIRM",
        concept_action=concept_action,
        canonical_date=canonical_date,
    )


def _empty_concept(promotion: AuditPromotion) -> dict:
    return {
        "concept_id": promotion.concept_id,
        "primary_knowledge_unit_id": promotion.knowledge_unit_id,
        "supporting_knowledge_unit_ids": [],
        "refinement_knowledge_unit_ids": [],
        "contradicting_knowledge_unit_ids": [],
    }


def apply_concept_action(existing: dict | None, promotion: AuditPromotion) -> dict:
    action = promotion.concept_action
    if action == "CREATE":
        if existing is not None:
            raise ValueError("concept already exists")
        return _empty_concept(promotion)

    if existing is None:
        raise ValueError("existing concept is required for non-CREATE action")
    if existing.get("concept_id") != promotion.concept_id:
        raise ValueError("existing concept ID does not match promotion")

    updated = deepcopy(existing)
    field_by_action = {
        "SUPPORT": "supporting_knowledge_unit_ids",
        "REFINE": "refinement_knowledge_unit_ids",
        "CONTRADICT": "contradicting_knowledge_unit_ids",
    }
    field = field_by_action[action]
    values = list(updated.get(field, []))
    if promotion.knowledge_unit_id not in values:
        values.append(promotion.knowledge_unit_id)
        values.sort()
    updated[field] = values
    return updated
