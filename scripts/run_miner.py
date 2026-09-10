from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from basketball_miner.export import GitHubPrivateRepoSink, ensure_private_repo
from basketball_miner.models import CandidateRecord, Checkpoint
from basketball_miner.run import run_miner
from basketball_miner.sources.crossref import CrossrefAdapter
from basketball_miner.sources.youtube_rss import YouTubeRssAdapter, load_channel_ids
from basketball_miner.state import load_checkpoint, save_checkpoint

ROOT = Path(__file__).parents[1]
DEFAULT_CONFIG = ROOT / "config" / "youtube_channels.json"


class DryRunSink:
    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]) -> dict[str, object]:
        return {"batch_id": batch_id, "count": len(candidates)}


def _load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _save_state(
    directory: Path,
    checkpoints: dict[str, Checkpoint],
    seen_hashes: set[str],
) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for adapter, checkpoint in sorted(checkpoints.items()):
        save_checkpoint(directory / f"{adapter}.json", checkpoint)
    seen_path = directory / "seen_hashes.jsonl"
    seen_path.write_text(
        "".join(f"{digest}\n" for digest in sorted(seen_hashes)),
        encoding="utf-8",
        newline="\n",
    )


def _safe_summary(counters, *, export_enabled: bool) -> str:
    return json.dumps(
        {
            "status": "ok",
            "export_enabled": export_enabled,
            "inspected": counters.inspected,
            "duplicates": counters.duplicates,
            "relevance_passed": counters.relevance_passed,
            "exported": counters.exported if export_enabled else 0,
            "would_export": counters.exported if not export_enabled else 0,
            "rate_limited": counters.rate_limited,
            "adapter_errors": counters.adapter_errors,
        },
        sort_keys=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Basketball Knowledge Miner")
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--state-dir", type=Path, default=Path("_state_current"))
    parser.add_argument("--next-state-dir", type=Path, default=Path("_state_next"))
    parser.add_argument("--youtube-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.budget <= 500:
        parser.error("--budget must be between 1 and 500")

    checkpoints = {
        "crossref": load_checkpoint(args.state_dir / "crossref.json", "crossref"),
        "youtube_rss": load_checkpoint(args.state_dir / "youtube_rss.json", "youtube_rss"),
    }
    seen_hashes = _load_seen(args.state_dir / "seen_hashes.jsonl")
    adapters = [
        CrossrefAdapter(),
        YouTubeRssAdapter(load_channel_ids(args.youtube_config)),
    ]

    if args.no_export:
        counters = run_miner(
            adapters,
            DryRunSink(),
            budget=args.budget,
            checkpoints=checkpoints,
            seen_hashes=seen_hashes,
        )
        print(_safe_summary(counters, export_enabled=False))
        return 0

    token = os.environ.get("HOOPHUB_MINER_TOKEN", "")
    if not token:
        raise SystemExit("export token is not configured")
    target_repo = os.environ.get(
        "HOOPHUB_TARGET_REPO",
        "Rudwpahs/shooting-profile-coach-ios",
    )
    target_branch = os.environ.get("HOOPHUB_TARGET_BRANCH", "main")

    ensure_private_repo(target_repo, token)
    sink = GitHubPrivateRepoSink(
        repo=target_repo,
        branch=target_branch,
        token=token,
    )
    counters = run_miner(
        adapters,
        sink,
        budget=args.budget,
        checkpoints=checkpoints,
        seen_hashes=seen_hashes,
    )
    _save_state(args.next_state_dir, checkpoints, seen_hashes)
    print(_safe_summary(counters, export_enabled=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
