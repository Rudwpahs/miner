"""Add shooting-mechanics caption excerpts for collected YouTube videos (run on the owner's PC).

Reads the YouTube candidates already in the target repository (main and the ingestion branch) from a
dedicated cache clone, fetches captions for a few untried SHOOTING videos, and exports the excerpts to
the ingestion branch. Never writes main. Progress lives in a local state file.

    python scripts/run_transcript_excerpts.py --dry-run
    python scripts/run_transcript_excerpts.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from basketball_miner.export import GitHubPrivateRepoSink, ensure_private_repo
from basketball_miner.sources.youtube_transcript import run_excerpts

TARGET_REPO = "Rudwpahs/hoopDB"
INGESTION_BRANCH = "miner-inbox"
INBOX = "ml/coach/miner-data/inbox"
WORK_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "hoopdb" / "transcript-excerpts"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def collected_records(cache: Path) -> list[dict]:
    """YouTube RSS candidates on the target's main and ingestion branches."""
    if not (cache / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=cache)
        git("remote", "add", "origin", f"https://github.com/{TARGET_REPO}.git", cwd=cache)
    records: list[dict] = []
    for branch in ("main", INGESTION_BRANCH):
        git("fetch", "-q", "--depth", "1", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", cwd=cache)
        paths = [p for p in git("ls-tree", "-r", "--name-only", f"origin/{branch}", INBOX, cwd=cache).splitlines()
                 if p.endswith(".jsonl") and "youtube_rss" in p]
        if not paths:
            continue
        batch = subprocess.run(["git", "cat-file", "--batch"], cwd=cache, check=True, capture_output=True,
                               input="".join(f"origin/{branch}:{p}\n" for p in paths).encode("utf-8")).stdout
        for line in batch.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def fetch_captions(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US", "en-GB"])
    return " ".join(snippet.text for snippet in fetched)


def github_token() -> str:
    token = os.environ.get("HOOPHUB_MINER_TOKEN", "")
    if token:
        return token
    return subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True).stdout.strip()


class DryRunSink:
    def write_batch(self, batch_id, candidates):
        for candidate in candidates:
            print(json.dumps({"batch": batch_id, "title": candidate.title, "summary": candidate.summary},
                             ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=5, help="videos to try per run")
    parser.add_argument("--max-attempts", type=int, default=3, help="give up on a video after this many failed days")
    parser.add_argument("--pause-seconds", type=float, default=15.0, help="wait between caption requests")
    parser.add_argument("--dry-run", action="store_true", help="print excerpts; export nothing, keep no state")
    args = parser.parse_args()

    state_path = WORK_DIR / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    records = collected_records(WORK_DIR / "hoopDB")

    if args.dry_run:
        sink = DryRunSink()
        state = json.loads(json.dumps(state))  # never persisted
    else:
        token = github_token()
        ensure_private_repo(TARGET_REPO, token)
        sink = GitHubPrivateRepoSink(TARGET_REPO, INGESTION_BRANCH, token)

    counts = run_excerpts(records, state, fetch_captions, sink, now=datetime.now(UTC), limit=args.limit,
                          max_attempts=args.max_attempts, pause=lambda: time.sleep(args.pause_seconds))
    if not args.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"at": datetime.now(UTC).isoformat(), "dry_run": args.dry_run, **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
