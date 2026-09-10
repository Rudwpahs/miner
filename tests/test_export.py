import base64
import json
from datetime import UTC, datetime

import httpx

from basketball_miner.export import GitHubPrivateRepoSink
from basketball_miner.models import CandidateRecord


def make_candidate() -> CandidateRecord:
    return CandidateRecord(
        adapter="crossref",
        source_type="academic",
        stable_id="10.1234/example",
        url="https://doi.org/10.1234/example",
        title="Basketball shooting biomechanics",
        authors=["A. Author"],
        published_at="2026-09-01",
        summary="Release mechanics in basketball shooting.",
        candidate_id="CAND-0123456789abcdef",
        canonical_hash="0" * 64,
        topic_codes=["SHOOTING", "BIOMECHANICS"],
        relevance_signals=["basketball"],
        provenance="LINKED",
        warnings=[],
        discovered_at="2026-09-11T00:00:00Z",
    )


def test_export_puts_base64_jsonl_to_private_inbox():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"commit": {"sha": "abc123"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = GitHubPrivateRepoSink(
        repo="Rudwpahs/shooting-profile-coach-ios",
        branch="main",
        token="test-secret-token",
        client=client,
        now_fn=lambda: datetime(2026, 9, 11, tzinfo=UTC),
    )
    receipt = sink.write_batch("MINER-test-0001", [make_candidate()])

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "PUT"
    assert request.url.path.endswith(
        "/repos/Rudwpahs/shooting-profile-coach-ios/contents/"
        "ml/coach/miner-data/inbox/2026/09/11/MINER-test-0001.jsonl"
    )
    assert request.headers["Authorization"] == "Bearer test-secret-token"

    body = captured["body"]
    assert isinstance(body, dict)
    decoded = base64.b64decode(body["content"]).decode("utf-8")
    rows = [json.loads(line) for line in decoded.splitlines()]
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "CAND-0123456789abcdef"
    assert body["branch"] == "main"
    assert receipt.count == 1
    assert receipt.commit_sha == "abc123"


def test_export_rejects_empty_batch_without_network_call():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = GitHubPrivateRepoSink(
        repo="Rudwpahs/shooting-profile-coach-ios",
        branch="main",
        token="test-secret-token",
        client=client,
    )
    try:
        sink.write_batch("MINER-empty", [])
    except ValueError:
        pass
    else:
        raise AssertionError("empty export batch must be rejected")
    assert calls == 0
