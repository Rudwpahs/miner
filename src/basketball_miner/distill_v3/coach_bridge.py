from __future__ import annotations

import hashlib

LINKED_ID_BASE = 1_000_000_000_000


def stable_research_unit_id(knowledge_unit_id: str) -> int:
    """Return the deterministic Coach numeric id reserved for a canonical KU."""
    payload = f"coach-linked-v1:{knowledge_unit_id}".encode()
    return LINKED_ID_BASE + int(hashlib.sha256(payload).hexdigest()[:10], 16)
