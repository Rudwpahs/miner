from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from basketball_miner.models import CandidateRecord

_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class ExportError(RuntimeError):
    """Redacted export failure safe to surface in public logs."""


@dataclass(frozen=True)
class ExportReceipt:
    batch_id: str
    path: str
    count: int
    commit_sha: str | None


class GitHubPrivateRepoSink:
    def __init__(
        self,
        repo: str,
        branch: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if repo.count("/") != 1:
            raise ValueError("repo must use owner/name form")
        if not branch.strip():
            raise ValueError("branch must not be empty")
        if not token:
            raise ValueError("token must not be empty")
        self.repo = repo
        self.branch = branch
        self._token = token
        self.client = client or httpx.Client(timeout=15.0, follow_redirects=False)
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def write_batch(
        self,
        batch_id: str,
        candidates: list[CandidateRecord],
    ) -> ExportReceipt:
        if not candidates:
            raise ValueError("candidates must not be empty")
        if not _BATCH_ID_RE.fullmatch(batch_id):
            raise ValueError("batch_id contains unsupported characters")

        now = self._now_fn().astimezone(UTC)
        path = "ml/coach/miner-data/inbox/" f"{now:%Y/%m/%d}/{batch_id}.jsonl"
        jsonl = "".join(
            json.dumps(
                candidate.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for candidate in candidates
        )
        encoded = base64.b64encode(jsonl.encode("utf-8")).decode("ascii")
        endpoint = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        response = self.client.put(
            endpoint,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "message": f"miner: ingest {batch_id}",
                "content": encoded,
                "branch": self.branch,
            },
        )
        if not 200 <= response.status_code < 300:
            status_class = response.status_code // 100
            raise ExportError(f"private export failed ({status_class}xx)")

        commit_sha: str | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            commit = payload.get("commit")
            if isinstance(commit, dict) and commit.get("sha"):
                commit_sha = str(commit["sha"])

        return ExportReceipt(
            batch_id=batch_id,
            path=path,
            count=len(candidates),
            commit_sha=commit_sha,
        )
