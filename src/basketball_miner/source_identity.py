from __future__ import annotations

import html
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from urllib.parse import quote

import httpx

from basketball_miner.models import SourceRecord


class IdentityDecision(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    HIGH_CONFIDENCE_VARIANT = "HIGH_CONFIDENCE_VARIANT"
    AMBIGUOUS = "AMBIGUOUS"
    MISMATCH = "MISMATCH"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ValidationMode(str, Enum):
    SINGLE_SOURCE_DOI = "SINGLE_SOURCE_DOI"
    MULTISOURCE_SYNTHESIS = "MULTISOURCE_SYNTHESIS"


@dataclass(frozen=True)
class TitleIdentityResult:
    decision: IdentityDecision
    sequence_ratio: float
    token_containment: float
    length_ratio: float


@dataclass(frozen=True)
class SourceIdentityResult:
    decision: IdentityDecision
    canonical_source: SourceRecord | None
    warnings: tuple[str, ...] = ()
    title_result: TitleIdentityResult | None = None


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    text = "".join(char if char.isalnum() else " " for char in text)
    return " ".join(text.split())


def compare_titles(observed: str, canonical: str) -> TitleIdentityResult:
    left = normalize_title(observed)
    right = normalize_title(canonical)
    sequence_ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    shorter = min(len(left_tokens), len(right_tokens))
    containment = len(left_tokens & right_tokens) / shorter if shorter else 0.0
    longest = max(len(left), len(right))
    length_ratio = min(len(left), len(right)) / longest if longest else 1.0
    if left == right:
        decision = IdentityDecision.EXACT_MATCH
    elif shorter < 5 and sequence_ratio >= 0.97:
        decision = IdentityDecision.HIGH_CONFIDENCE_VARIANT
    elif shorter >= 5 and (
        sequence_ratio >= 0.94 or (containment >= 0.95 and length_ratio >= 0.60)
    ):
        decision = IdentityDecision.HIGH_CONFIDENCE_VARIANT
    elif sequence_ratio < 0.75 and containment < 0.80:
        decision = IdentityDecision.MISMATCH
    else:
        decision = IdentityDecision.AMBIGUOUS
    return TitleIdentityResult(decision, sequence_ratio, containment, length_ratio)


def _source_from_message(message: object) -> SourceRecord:
    if not isinstance(message, dict):
        raise ValueError("invalid Crossref message")
    doi = str(message.get("DOI", "")).strip()
    titles = message.get("title")
    title = str(titles[0]).strip() if isinstance(titles, list) and titles else ""
    if not doi or not title:
        raise ValueError("missing DOI or title")
    authors = []
    for author in message.get("author", []) or []:
        if isinstance(author, dict):
            name = " ".join(
                value for value in (
                    str(author.get("given", "")).strip(),
                    str(author.get("family", "")).strip(),
                ) if value
            )
            if name:
                authors.append(name)
    published_at = None
    for key in ("published-online", "published-print"):
        value = message.get(key)
        parts = value.get("date-parts") if isinstance(value, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = int(parts[0][0])
            month = int(parts[0][1]) if len(parts[0]) > 1 else 1
            day = int(parts[0][2]) if len(parts[0]) > 2 else 1
            published_at = f"{year:04d}-{month:02d}-{day:02d}"
            break
    return SourceRecord(
        adapter="crossref",
        source_type="academic",
        stable_id=doi,
        url=f"https://doi.org/{doi}",
        title=title,
        authors=authors,
        published_at=published_at,
        summary=None,
    )


def _family(authors: list[str]) -> str | None:
    return authors[0].casefold().split()[-1] if authors else None


def _year(value: str | None) -> int | None:
    return int(value[:4]) if value and value[:4].isdigit() else None


class CrossrefIdentityVerifier:
    endpoint = "https://api.crossref.org/works"

    def __init__(self, client: httpx.Client | None = None, *, sleep_fn=time.sleep) -> None:
        self.client = client or httpx.Client(timeout=10.0, follow_redirects=True)
        self.sleep_fn = sleep_fn

    def verify(
        self,
        source: SourceRecord,
        *,
        mode: ValidationMode = ValidationMode.SINGLE_SOURCE_DOI,
        supporting_sources: tuple[str, ...] = (),
    ) -> SourceIdentityResult:
        if mode is ValidationMode.MULTISOURCE_SYNTHESIS:
            if not supporting_sources:
                raise ValueError("multisource synthesis requires supporting source")
            return SourceIdentityResult(IdentityDecision.NOT_APPLICABLE, None)

        response = None
        for attempt in range(3):
            try:
                response = self.client.get(f"{self.endpoint}/{quote(source.stable_id, safe='')}")
            except httpx.TimeoutException:
                if attempt < 2:
                    self.sleep_fn(0)
                    continue
                return self._unverified()
            if response.status_code == 429 or response.status_code == 404:
                return self._unverified()
            if response.status_code >= 500:
                if attempt < 2:
                    self.sleep_fn(0)
                    continue
                return self._unverified()
            if response.status_code >= 400:
                return self._unverified()
            break

        try:
            payload = response.json() if response is not None else {}
            canonical = _source_from_message(payload.get("message"))
        except (TypeError, ValueError):
            return self._unverified()

        title_result = compare_titles(source.title, canonical.title)
        decision = title_result.decision
        if decision is IdentityDecision.HIGH_CONFIDENCE_VARIANT:
            author_conflict = _family(source.authors) and _family(canonical.authors) and (
                _family(source.authors) != _family(canonical.authors)
            )
            source_year = _year(source.published_at)
            canonical_year = _year(canonical.published_at)
            year_conflict = (
                source_year is not None
                and canonical_year is not None
                and abs(source_year - canonical_year) > 1
            )
            if author_conflict or year_conflict:
                decision = IdentityDecision.AMBIGUOUS

        warnings = {
            IdentityDecision.HIGH_CONFIDENCE_VARIANT: (
                "DOI_TITLE_VARIANT",
                "DOI_CANONICAL_METADATA_USED",
            ),
            IdentityDecision.AMBIGUOUS: (
                "DOI_TITLE_AMBIGUOUS",
                "DOI_CANONICAL_METADATA_USED",
            ),
            IdentityDecision.MISMATCH: (
                "DOI_TITLE_MISMATCH",
                "DOI_CANONICAL_METADATA_USED",
            ),
        }.get(decision, ())
        return SourceIdentityResult(decision, canonical, warnings, title_result)

    @staticmethod
    def _unverified() -> SourceIdentityResult:
        return SourceIdentityResult(
            IdentityDecision.UNVERIFIED,
            None,
            ("DOI_IDENTITY_UNVERIFIED",),
        )
