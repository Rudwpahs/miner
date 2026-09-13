from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


def _payload(value: BaseModel | dict) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _json_bytes(value: BaseModel | dict) -> bytes:
    text = json.dumps(
        _payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if path.read_bytes() == content:
            return
        raise FileExistsError(f"immutable staging path already exists with different bytes: {path}")


def write_immutable_json(path: Path, payload: BaseModel | dict) -> None:
    _write_immutable(path, _json_bytes(payload))


def write_immutable_jsonl(path: Path, rows: list[BaseModel | dict]) -> None:
    content = b"".join(_json_bytes(row) for row in rows)
    _write_immutable(path, content)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("JSONL rows must be objects")
        rows.append(value)
    return rows
