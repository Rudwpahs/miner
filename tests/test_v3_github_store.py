import base64
import json

import httpx
import pytest

from basketball_miner.distill_v3.github_store import GitHubV3Store


def make_store(handler) -> GitHubV3Store:
    return GitHubV3Store(
        repo="Rudwpahs/hoopDB",
        branch="main",
        token="secret-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_constructor_rejects_invalid_repo_or_blank_token():
    with pytest.raises(ValueError):
        GitHubV3Store(repo="invalid", branch="main", token="x")
    with pytest.raises(ValueError):
        GitHubV3Store(repo="Rudwpahs/hoopDB", branch="main", token="")


def test_list_dir_returns_sorted_entries():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["ref"] == "main"
        return httpx.Response(
            200,
            json=[
                {"name": "z.json", "path": "x/z.json", "sha": "z" * 40, "type": "file"},
                {"name": "a", "path": "x/a", "sha": "a" * 40, "type": "dir"},
            ],
        )

    entries = make_store(handler).list_dir("x")
    assert [entry.name for entry in entries] == ["a", "z.json"]


def test_read_file_decodes_base64_and_404_is_none():
    content = b'{"ok":true}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing.json"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "name": "state.json",
                "path": "state.json",
                "sha": "a" * 40,
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            },
        )

    store = make_store(handler)
    assert store.read_file("missing.json") is None
    remote = store.read_file("state.json")
    assert remote is not None
    assert remote.content == content
    assert remote.sha == "a" * 40


def test_read_file_fetches_raw_bytes_when_contents_api_uses_encoding_none():
    content = b'{"large":true}\n'
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        accept = request.headers.get("Accept", "")
        if "raw" in accept:
            return httpx.Response(200, content=content)
        return httpx.Response(
            200,
            json={
                "name": "distill.json",
                "path": "ml/coach/miner-data/v3/ledgers/distill.json",
                "sha": "d" * 40,
                "type": "file",
                "encoding": "none",
                "content": "",
            },
        )

    remote = make_store(handler).read_file("ml/coach/miner-data/v3/ledgers/distill.json")

    assert remote is not None
    assert remote.content == content
    assert remote.sha == "d" * 40
    assert len(requests) == 2
    assert "raw" in requests[1].headers["Accept"]


def test_read_file_rejects_directory_payload():
    store = make_store(lambda request: httpx.Response(200, json={"type": "dir"}))
    with pytest.raises(RuntimeError, match="not a file"):
        store.read_file("some-dir")


def test_create_immutable_creates_absent_file_without_sha():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        assert "sha" not in body
        assert base64.b64decode(body["content"]) == b"same\n"
        return httpx.Response(201, json={"commit": {"sha": "c" * 40}})

    commit_sha = make_store(handler).create_immutable(
        "ml/coach/miner-data/v3/queues/triage/V3-TRIAGE-0123456789ab.json",
        b"same\n",
        "v3: queue batch",
    )
    assert commit_sha == "c" * 40
    assert [request.method for request in requests] == ["GET", "PUT"]


def test_create_immutable_identical_retry_does_not_put():
    content = b"same\n"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "x",
                "sha": "a" * 40,
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            },
        )

    result = make_store(handler).create_immutable(
        "ml/coach/miner-data/v3/queues/triage/V3-TRIAGE-0123456789ab.json",
        content,
        "v3: queue batch",
    )
    assert result is None
    assert calls == 1


def test_create_immutable_conflicting_bytes_fail_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "x",
                "sha": "a" * 40,
                "encoding": "base64",
                "content": base64.b64encode(b"old\n").decode("ascii"),
            },
        )

    with pytest.raises(FileExistsError):
        make_store(handler).create_immutable(
            "ml/coach/miner-data/v3/queues/triage/V3-TRIAGE-0123456789ab.json",
            b"new\n",
            "v3: queue batch",
        )


def test_update_mutable_requires_observed_sha_and_includes_it_in_put():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "path": "x",
                    "sha": "a" * 40,
                    "encoding": "base64",
                    "content": base64.b64encode(b"old\n").decode("ascii"),
                },
            )
        body = json.loads(request.content)
        assert body["sha"] == "a" * 40
        return httpx.Response(200, json={"commit": {"sha": "b" * 40}})

    result = make_store(handler).update_mutable(
        "ml/coach/miner-data/v3/ledgers/distill.json",
        b"new\n",
        expected_sha="a" * 40,
        message="v3: update ledger",
    )
    assert result == "b" * 40
    assert [request.method for request in requests] == ["GET", "PUT"]


def test_update_mutable_stale_sha_fails_before_put():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "x",
                "sha": "a" * 40,
                "encoding": "base64",
                "content": base64.b64encode(b"old\n").decode("ascii"),
            },
        )

    with pytest.raises(RuntimeError, match="stale remote SHA"):
        make_store(handler).update_mutable(
            "ml/coach/miner-data/v3/ledgers/distill.json",
            b"new\n",
            expected_sha="b" * 40,
            message="v3: update ledger",
        )
    assert calls == 1


def test_writes_outside_v3_are_rejected_without_network_call():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(ValueError):
        make_store(handler).create_immutable(
            "ml/coach/miner-data/distilled/accepted/2026/09/14.jsonl",
            b"x\n",
            "bad write",
        )
    assert calls == 0


def test_errors_do_not_leak_token():
    token = "VERY-SECRET-V3-TOKEN"
    store = GitHubV3Store(
        repo="Rudwpahs/hoopDB",
        branch="main",
        token=token,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )
    with pytest.raises(RuntimeError) as exc_info:
        store.list_dir("ml/coach/miner-data/inbox")
    assert token not in str(exc_info.value)
