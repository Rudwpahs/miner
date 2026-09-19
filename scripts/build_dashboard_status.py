from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from basketball_miner.collection_stats import load_collection_stats, load_target_config
from basketball_miner.dashboard_status import build_status_from_store, validate_public_payload
from basketball_miner.distill_v3.github_store import GitHubV3Store

ROOT = Path(__file__).parents[1]
DEFAULT_TARGET_CONFIG = ROOT / "config" / "miner_target.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build privacy-safe Miner dashboard status")
    parser.add_argument("--target-config", type=Path, default=DEFAULT_TARGET_CONFIG)
    parser.add_argument("--collection-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    token = os.environ.get("HOOPHUB_MINER_TOKEN", "")
    if not token:
        raise SystemExit("dashboard private-repository token is not configured")

    config = load_target_config(args.target_config)
    now = datetime.now(ZoneInfo(config.timezone))
    collection_stats = load_collection_stats(args.collection_state, now)
    store = GitHubV3Store("Rudwpahs/hoopDB", "main", token)
    status = build_status_from_store(
        config,
        collection_stats,
        store,
        generated_at=now.isoformat(),
    )
    validated = validate_public_payload(status.model_dump(mode="json"))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        validated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
