from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Checkpoint


def load_checkpoint(path: Path, adapter: str) -> Checkpoint:
    if not path.exists():
        return Checkpoint(adapter=adapter)
    payload = json.loads(path.read_text(encoding="utf-8"))
    checkpoint = Checkpoint.model_validate(payload)
    if checkpoint.adapter != adapter:
        raise ValueError(
            f"checkpoint adapter mismatch: expected {adapter!r}, got {checkpoint.adapter!r}"
        )
    return checkpoint


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps(
        checkpoint.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
