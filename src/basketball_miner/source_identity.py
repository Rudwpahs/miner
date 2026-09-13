from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

_NON_WORD_RE = re.compile("[^A-Za-z0-9_]+")
_WS_RE = re.compile(" +")


class IdentityDecision(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    HIGH_CONFIDENCE_VARIANT = "HIGH_CONFIDENCE_VARIANT"
    AMBIGUOUS = "AMBIGUOUS"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class TitleIdentityResult:
    decision: IdentityDecision
    sequence_ratio: float
    token_containment: float
    length_ratio: float


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    normalized = _NON_WORD_RE.sub(" ", normalized)
    return _WS_RE.sub(" ", normalized).strip()


def compare_titles(observed: str, canonical: str) -> TitleIdentityResult:
    left = normalize_title(observed)
    right = normalize_title(canonical)
    sequence_ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    shorter_count = min(len(left_tokens), len(right_tokens))
    token_containment = (
        len(left_tokens & right_tokens) / shorter_count if shorter_count else 0.0
    )
    longest = max(len(left), len(right))
    length_ratio = min(len(left), len(right)) / longest if longest else 1.0

    if left == right:
        decision = IdentityDecision.EXACT_MATCH
    elif shorter_count < 5 and sequence_ratio >= 0.97:
        decision = IdentityDecision.HIGH_CONFIDENCE_VARIANT
    elif shorter_count >= 5 and (
        sequence_ratio >= 0.94
        or (token_containment >= 0.95 and length_ratio >= 0.60)
    ):
        decision = IdentityDecision.HIGH_CONFIDENCE_VARIANT
    elif sequence_ratio < 0.75 and token_containment < 0.80:
        decision = IdentityDecision.MISMATCH
    else:
        decision = IdentityDecision.AMBIGUOUS

    return TitleIdentityResult(
        decision=decision,
        sequence_ratio=sequence_ratio,
        token_containment=token_containment,
        length_ratio=length_ratio,
    )
