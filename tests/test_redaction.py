import httpx

from basketball_miner.export import ExportError, GitHubPrivateRepoSink
from basketball_miner.models import CandidateRecord


def make_sensitive_candidate() -> CandidateRecord:
    return CandidateRecord(
        adapter="youtube_rss",
        source_type="coaching",
        stable_id="secret-video-id",
        url="https://www.youtube.com/watch?v=secret-video-id",
        title="Basketball private-looking test title",
        authors=["Coach Test"],
        published_at="2026-09-10",
        summary="Basketball coaching summary that must not appear in errors.",
        candidate_id="CAND-fedcba9876543210",
        canonical_hash="1" * 64,
        topic_codes=["COACHING_METHOD"],
        relevance_signals=["basketball"],
        provenance="LINKED",
        warnings=[],
        discovered_at="2026-09-11T00:00:00Z",
    )


def test_export_failures_never_leak_token_or_candidate_payload():
    token = "TOP-SECRET-MINER-TOKEN"
    candidate = make_sensitive_candidate()

    for status in (401, 403, 409, 429, 500):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request, status=status: httpx.Response(
                    status,
                    text=f"server reflected {token} {candidate.title}",
                )
            )
        )
        sink = GitHubPrivateRepoSink(
            repo="Rudwpahs/shooting-profile-coach-ios",
            branch="main",
            token=token,
            client=client,
        )
        try:
            sink.write_batch(f"MINER-fail-{status}", [candidate])
        except ExportError as exc:
            message = str(exc)
            assert token not in message
            assert candidate.title not in message
            assert candidate.summary not in message
            assert str(candidate.url) not in message
        else:
            raise AssertionError(f"HTTP {status} must fail closed")
