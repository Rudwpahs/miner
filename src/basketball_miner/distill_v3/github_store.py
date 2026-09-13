from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import httpx

from .paths import assert_v3_write_path


@dataclass(frozen=True)
class RemoteFile:
    path: str
    sha: str
    content: bytes


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    path: str
    sha: str
    type: Literal["file", "dir"]


class GitHubV3Store:
    def __init__(
        self,
        repo: str,
        branch: str,
        token: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if repo.count("/") != 1:
            raise ValueError("repo must use owner/name form")
        if not branch.strip():
            raise ValueError("branch must not be empty")
        if not token:
            raise ValueError("token must not be empty")
        self.repo = repo
        self.branch = branch.strip()
        self._token = token
        self.client = client or httpx.Client(timeout=15.0, follow_redirects=False)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _safe_read_path(self, path: str) -> str:
        if path.startswith("/") or "\\" in path:
            raise ValueError("invalid repository path")
        parts = path.split("/")
        if not path or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("invalid repository path")
        return path

    def _endpoint(self, path: str) -> str:
        safe_path = self._safe_read_path(path)
        return f"https://api.github.com/repos/{self.repo}/contents/{quote(safe_path, safe='/')}"

    def _raise_status(self, operation: str, status_code: int) -> None:
        raise RuntimeError(f"{operation} failed ({status_code // 100}xx)")

    def list_dir(self, path: str) -> list[RemoteEntry]:
        response = self.client.get(
            self._endpoint(path),
            headers=self._headers(),
            params={"ref": self.branch},
        )
        if response.status_code == 404:
            return []
        if not 200 <= response.status_code < 300:
            self._raise_status("private directory read", response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("private directory read returned invalid metadata") from exc
        if not isinstance(payload, list):
            raise RuntimeError("private directory read did not return a directory")

        entries: list[RemoteEntry] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("type") not in {"file", "dir"}:
                raise RuntimeError("private directory read returned invalid entry")
            entries.append(
                RemoteEntry(
                    name=str(item.get("name", "")),
                    path=str(item.get("path", "")),
                    sha=str(item.get("sha", "")),
                    type=item["type"],
                )
            )
        return sorted(entries, key=lambda entry: (entry.name, entry.path))

    def read_file(self, path: str) -> RemoteFile | None:
        response = self.client.get(
            self._endpoint(path),
            headers=self._headers(),
            params={"ref": self.branch},
        )
        if response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            self._raise_status("private file read", response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("private file read returned invalid metadata") from exc
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise RuntimeError("private path is not a file")
        if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            raise RuntimeError("private file read returned unsupported encoding")
        try:
            content = base64.b64decode(payload["content"], validate=False)
        except ValueError as exc:
            raise RuntimeError("private file read returned invalid base64") from exc
        return RemoteFile(
            path=str(payload.get("path", path)),
            sha=str(payload.get("sha", "")),
            content=content,
        )

    def _put(
        self,
        path: str,
        content: bytes,
        *,
        message: str,
        sha: str | None,
    ) -> str | None:
        if not message.strip():
            raise ValueError("message must not be empty")
        body: dict[str, str] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": self.branch,
        }
        if sha is not None:
            body["sha"] = sha
        response = self.client.put(
            self._endpoint(path),
            headers=self._headers(),
            json=body,
        )
        if not 200 <= response.status_code < 300:
            self._raise_status("private V3 write", response.status_code)
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        commit = payload.get("commit")
        if isinstance(commit, dict) and commit.get("sha"):
            return str(commit["sha"])
        return None

    def create_immutable(self, path: str, content: bytes, message: str) -> str | None:
        safe_path = assert_v3_write_path(path)
        current = self.read_file(safe_path)
        if current is not None:
            if current.content == content:
                return None
            raise FileExistsError(f"immutable V3 path already exists with different bytes: {safe_path}")
        return self._put(safe_path, content, message=message, sha=None)

    def update_mutable(
        self,
        path: str,
        content: bytes,
        *,
        expected_sha: str | None,
        message: str,
    ) -> str | None:
        safe_path = assert_v3_write_path(path)
        current = self.read_file(safe_path)
        if current is None:
            if expected_sha is not None:
                raise RuntimeError("stale remote SHA")
            return self._put(safe_path, content, message=message, sha=None)
        if expected_sha != current.sha:
            raise RuntimeError("stale remote SHA")
        if current.content == content:
            return None
        return self._put(safe_path, content, message=message, sha=current.sha)
