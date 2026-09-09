from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import SourceRecord

_TRACKING_KEYS = {"fbclid", "gclid"}
_WHITESPACE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.port is not None:
        default = (scheme == "https" and parts.port == 443) or (scheme == "http" and parts.port == 80)
        netloc = host if default else f"{host}:{parts.port}"
    else:
        netloc = host

    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_KEYS:
            continue
        kept.append((key, value))
    kept.sort(key=lambda pair: (pair[0], pair[1]))
    return urlunsplit((scheme, netloc, parts.path or "", urlencode(kept, doseq=True), ""))


def stable_candidate_id(record: SourceRecord) -> tuple[str, str]:
    canonical_url = canonicalize_url(str(record.url))
    identity = f"{record.adapter.strip().lower()}\n{record.stable_id.strip().lower()}\n{canonical_url}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"CAND-{digest[:16]}", digest


def _normalized_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).casefold()


def fingerprint(record: SourceRecord) -> str:
    title = _normalized_text(record.title)
    authors = "|".join(_normalized_text(author) for author in record.authors)
    year = ""
    if record.published_at and len(record.published_at) >= 4 and record.published_at[:4].isdigit():
        year = record.published_at[:4]
    identity = f"{title}\n{authors}\n{year}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
