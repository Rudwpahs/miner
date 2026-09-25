from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from basketball_miner.collection_stats import (
    CollectionStats,
    apply_export,
    bootstrap_collection_stats,
    load_collection_stats,
    load_target_config,
    reconcile_collection_stats,
    remaining_target,
)
from basketball_miner.distill_v3.github_store import GitHubV3Store
from basketball_miner.export import GitHubPrivateRepoSink, ensure_private_repo
from basketball_miner.models import CandidateRecord, Checkpoint, RunCounters
from basketball_miner.run import MAX_BUDGET, run_miner
from basketball_miner.source_identity import CrossrefIdentityVerifier
from basketball_miner.sources.crossref import CrossrefAdapter
from basketball_miner.sources.youtube_rss import (
    YouTubeRssAdapter,
    load_channel_ids,
    load_legacy_users,
)
from basketball_miner.state import load_checkpoint, save_checkpoint

ROOT = Path(__file__).parents[1]
DEFAULT_CONFIG = ROOT / "config" / "youtube_channels.json"
DEFAULT_TARGET_CONFIG = ROOT / "config" / "miner_target.json"


class DryRunSink:
    def write_batch(self, batch_id: str, candidates: list[CandidateRecord]) -> dict[str, object]:
        return {"batch_id": batch_id, "count": len(candidates)}


def _load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _save_lines(path: Path, values: set[str]) -> None:
    path.write_text(
        "".join(f"{value}\n" for value in sorted(values)),
        encoding="utf-8",
        newline="\n",
    )


def _save_state(
    directory: Path,
    checkpoints: dict[str, Checkpoint],
    seen_hashes: set[str],
    seen_crossref_dois: set[str],
    collection_stats: CollectionStats,
) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for adapter, checkpoint in sorted(checkpoints.items()):
        save_checkpoint(directory / f"{adapter}.json", checkpoint)
    _save_lines(directory / "seen_hashes.jsonl", seen_hashes)
    _save_lines(directory / "seen_crossref_dois.jsonl", seen_crossref_dois)
    (directory / "collection_stats.json").write_text(
        collection_stats.model_dump_json() + "\n",
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
            "identity_verified": counters.identity_verified,
            "identity_variants": counters.identity_variants,
            "identity_ambiguous": counters.identity_ambiguous,
            "identity_mismatches": counters.identity_mismatches,
            "identity_unverified": counters.identity_unverified,
            "identity_collisions": counters.identity_collisions,
        },
        sort_keys=True,
    )


def _load_mining_inputs(state_dir: Path):
    checkpoints = {
        "crossref": load_checkpoint(state_dir / "crossref.json", "crossref"),
        "youtube_rss": load_checkpoint(state_dir / "youtube_rss.json", "youtube_rss"),
    }
    seen_hashes = _load_seen(state_dir / "seen_hashes.jsonl")
    seen_crossref_dois = _load_seen(state_dir / "seen_crossref_dois.jsonl")
    return checkpoints, seen_hashes, seen_crossref_dois


def _build_adapters(youtube_config: Path):
    return [
        CrossrefAdapter(),
        YouTubeRssAdapter(
            load_channel_ids(youtube_config),
            legacy_users=load_legacy_users(youtube_config),
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Basketball Knowledge Miner")
    parser.add_argument("--budget", type=int, default=MAX_BUDGET)
    parser.add_argument("--until-target", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=Path("_state_current"))
    parser.add_argument("--next-state-dir", type=Path, default=Path("_state_next"))
    parser.add_argument("--youtube-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--target-config", type=Path, default=DEFAULT_TARGET_CONFIG)
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.budget <= MAX_BUDGET:
        parser.error(f"--budget must be between 1 and {MAX_BUDGET}")
    if args.until_target and args.no_export:
        parser.error("--until-target requires export mode")

    checkpoints, seen_hashes, seen_crossref_dois = _load_mining_inputs(args.state_dir)

    if args.no_export:
        counters = run_miner(
            _build_adapters(args.youtube_config),
            DryRunSink(),
            budget=args.budget,
            checkpoints=checkpoints,
            seen_hashes=seen_hashes,
            seen_crossref_dois=seen_crossref_dois,
            identity_verifier=CrossrefIdentityVerifier(),
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

    config = load_target_config(args.target_config)
    now = datetime.now(ZoneInfo(config.timezone))
    store = GitHubV3Store(target_repo, target_branch, token)
    collection_path = args.state_dir / "collection_stats.json"
    if collection_path.exists():
        collection_stats = load_collection_stats(collection_path, now)
        collection_stats = reconcile_collection_stats(
            store,
            collection_stats,
            now,
            seen_hashes=seen_hashes,
            seen_crossref_dois=seen_crossref_dois,
        )
    else:
        collection_stats = bootstrap_collection_stats(
            store,
            now,
            seen_hashes=seen_hashes,
            seen_crossref_dois=seen_crossref_dois,
        )

    remaining = remaining_target(config, collection_stats)
    if remaining == 0:
        _save_state(
            args.next_state_dir,
            checkpoints,
            seen_hashes,
            seen_crossref_dois,
            collection_stats,
        )
        print(_safe_summary(RunCounters(), export_enabled=True))
        return 0

    ensure_private_repo(target_repo, token)
    sink = GitHubPrivateRepoSink(
        repo=target_repo,
        branch=target_branch,
        token=token,
    )
    counters = run_miner(
        _build_adapters(args.youtube_config),
        sink,
        budget=None if args.until_target else args.budget,
        chunk_size=100 if args.until_target else 50,
        checkpoints=checkpoints,
        seen_hashes=seen_hashes,
        seen_crossref_dois=seen_crossref_dois,
        identity_verifier=CrossrefIdentityVerifier(),
        max_exports=remaining,
    )
    finished_at = datetime.now(ZoneInfo(config.timezone))
    collection_stats = apply_export(collection_stats, counters.exported, finished_at)
    _save_state(
        args.next_state_dir,
        checkpoints,
        seen_hashes,
        seen_crossref_dois,
        collection_stats,
    )
    print(_safe_summary(counters, export_enabled=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
