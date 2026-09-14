from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from basketball_miner.distill_v3.coach_bridge import (
    BridgeExportError,
    CoachProjectionV1,
    build_linked_bundle,
    write_linked_bundle,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: JSONL record must be an object")
            records.append(value)
    return records


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export LINKED Coach evidence bundle v1")
    parser.add_argument(
        "--canonical",
        action="append",
        required=True,
        type=Path,
        help="canonical accepted JSONL; may be supplied more than once",
    )
    parser.add_argument("--projections", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        canonical_records: list[dict[str, Any]] = []
        for path in args.canonical:
            canonical_records.extend(_load_jsonl(path))
        projection_records = _load_jsonl(args.projections)
        projections = [CoachProjectionV1.model_validate(record) for record in projection_records]
        bundle = build_linked_bundle(canonical_records, projections)
        write_linked_bundle(bundle, args.output)
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        BridgeExportError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"coach bridge export failed: {type(exc).__name__}", file=sys.stderr)
        return 2

    skipped = sum(bundle.skipped.values())
    print(
        f"exported_units={len(bundle.units)} sources={len(bundle.sources)} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
