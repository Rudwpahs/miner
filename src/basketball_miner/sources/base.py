from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from basketball_miner.models import Checkpoint, SourceRecord


@dataclass(frozen=True)
class AdapterBatch:
    records: list[SourceRecord]
    next_checkpoint: Checkpoint
    rate_limited: bool = False
    error_count: int = 0
    retry_after: str | None = None


class SourceAdapter(Protocol):
    name: str

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch: ...
