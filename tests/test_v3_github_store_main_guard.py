import httpx
import pytest

from basketball_miner.distill_v3.github_store import GitHubV3Store

V3_FILE = "ml/coach/miner-data/v3/metrics/2026/10/06.json"


def _store(branch: str, calls: list[httpx.Request]) -> GitHubV3Store:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(201, json={"commit": {"sha": "abc123"}})

    return GitHubV3Store(
        "Rudwpahs/hoopDB",
        branch,
        "test-secret-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("branch", ["main", " main ", "refs/heads/main", "master"])
def test_v3_store_never_writes_the_target_main_branch(branch: str) -> None:
    calls: list[httpx.Request] = []
    store = _store(branch, calls)
    with pytest.raises(PermissionError):
        store.create_immutable(V3_FILE, b"{}\n", "distill-v3: test")
    with pytest.raises(PermissionError):
        store.update_mutable(V3_FILE, b"{}\n", expected_sha=None, message="distill-v3: test")
    assert [request.method for request in calls] == ["GET", "GET"], "main may be read, never written"


def test_v3_store_still_writes_other_branches() -> None:
    calls: list[httpx.Request] = []
    store = _store("miner-inbox", calls)
    assert store.create_immutable(V3_FILE, b"{}\n", "distill-v3: test") == "abc123"
    assert [request.method for request in calls] == ["GET", "PUT"]
