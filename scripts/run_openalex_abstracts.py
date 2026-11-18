"""Add OpenAlex abstracts for collected Crossref papers that have none (run on the owner's PC).

Reads the Crossref candidates already in the target repository (main and the ingestion branch) from a
dedicated cache clone, looks up abstracts in batches of 50 DOIs, and exports the found ones to the
ingestion branch as candidates that supersede the Crossref records. Never writes main. Progress lives
in a local state file; DOIs already enriched in the inbox are skipped even without it.

    python scripts/run_openalex_abstracts.py --dry-run --limit 100
    python scripts/run_openalex_abstracts.py --limit 500
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from basketball_miner.export import GitHubPrivateRepoSink, ensure_private_repo
from basketball_miner.sources.openalex_abstract import ADAPTER, reconstruct_abstract, run_enrichment

TARGET_REPO = "Rudwpahs/hoopDB"
INGESTION_BRANCH = "miner-inbox"
INBOX = "ml/coach/miner-data/inbox"
WORK_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "hoopdb" / "openalex-abstracts"
OPENALEX = "https://api.openalex.org/works"
DOI_PREFIX = "https://doi.org/"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def collected_records(cache: Path) -> list[dict]:
    """Crossref and earlier OpenAlex candidates on the target's main and ingestion branches."""
    if not (cache / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=cache)
        git("remote", "add", "origin", f"https://github.com/{TARGET_REPO}.git", cwd=cache)
    records: list[dict] = []
    for branch in ("main", INGESTION_BRANCH):
        git("fetch", "-q", "--depth", "1", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", cwd=cache)
        paths = [p for p in git("ls-tree", "-r", "--name-only", f"origin/{branch}", INBOX, cwd=cache).splitlines()
                 if p.endswith(".jsonl") and ("-crossref-" in p or f"-{ADAPTER}-" in p)]
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


def make_fetch(client: httpx.Client):
    def fetch(dois: list[str]) -> dict[str, str | None]:
        found: dict[str, str | None] = {}
        # The filter syntax splits on "|" and ","; the rare DOI holding one is looked up alone.
        plain = [d for d in dois if "|" not in d and "," not in d]
        for odd in (d for d in dois if d not in plain):
            response = client.get(f"{OPENALEX}/doi:{odd}", params={"select": "doi,abstract_inverted_index"})
            if response.status_code == 404:
                continue
            response.raise_for_status()
            found[odd.lower()] = reconstruct_abstract(response.json().get("abstract_inverted_index"))
        if plain:
            response = client.get(OPENALEX, params={
                "filter": "doi:" + "|".join(plain),
                "select": "doi,abstract_inverted_index",
                "per-page": str(len(plain)),
            })
            response.raise_for_status()
            for work in response.json().get("results", []):
                doi = str(work.get("doi") or "").lower().removeprefix(DOI_PREFIX)
                if doi:
                    found[doi] = reconstruct_abstract(work.get("abstract_inverted_index"))
        return found

    return fetch


def github_token() -> str:
    token = os.environ.get("HOOPHUB_MINER_TOKEN", "")
    if token:
        return token
    return subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True).stdout.strip()


class DryRunSink:
    def write_batch(self, batch_id, candidates):
        for candidate in candidates[:3]:
            print(json.dumps({"batch": batch_id, "title": candidate.title, "topics": candidate.topic_codes,
                              "summary": candidate.summary[:160]}, ensure_ascii=False))
        print(json.dumps({"batch": batch_id, "candidates": len(candidates)}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=500, help="papers to look up per run")
    parser.add_argument("--dry-run", action="store_true", help="print candidates; export nothing, keep no state")
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

    with httpx.Client(timeout=30.0, headers={"User-Agent": "basketball-miner (abstract lookup)"}) as client:
        counts = run_enrichment(records, state, make_fetch(client), sink, now=datetime.now(UTC), limit=args.limit)
    if not args.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"at": datetime.now(UTC).isoformat(), "dry_run": args.dry_run, **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
