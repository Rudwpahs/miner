from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime

import httpx

from basketball_miner.distill_v3.github_store import GitHubV3Store
from basketball_miner.distill_v3.prepare import run_remote_shadow
from basketball_miner.export import ensure_private_repo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare FormPath Distillation V3 shadow state")
    parser.add_argument("--repo", default="Rudwpahs/hoopDB")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write-shadow", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not 1 <= args.batch_size <= 100:
        raise SystemExit("--batch-size must be between 1 and 100")

    token = os.environ.get("HOOPHUB_MINER_TOKEN", "")
    if not token:
        raise SystemExit("HOOPHUB_MINER_TOKEN is required for private repository access")

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        ensure_private_repo(args.repo, token, client=client)
        store = GitHubV3Store(args.repo, args.branch, token, client=client)
        summary = run_remote_shadow(
            store=store,
            run_date=args.run_date,
            created_at=created_at,
            batch_size=args.batch_size,
            write_shadow=args.write_shadow,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
