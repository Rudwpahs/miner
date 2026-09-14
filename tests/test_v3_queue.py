from datetime import UTC, datetime, timedelta

import pytest

from basketball_miner.distill_v3.models import BatchRecord
from basketball_miner.distill_v3.queue import (
    build_batches,
    claim_lease,
    lease_is_active,
    priority_for,
)


def make_batch() -> BatchRecord:
    return BatchRecord(
        batch_id="V3-TRIAGE-0123456789ab",
        stage="TRIAGE",
        candidate_ids=["CAND-0123456789abcdef"],
        priority=50,
        created_at="2026-09-14T00:00:00Z",
        input_fingerprints=["0" * 64],
        status="PENDING",
    )


def test_priority_policy_is_fixed():
    assert priority_for("official", "TRIAGE") == 90
    assert priority_for("academic", "DEEP") == 88
    assert priority_for("coaching", "JUDGE") == 70
    assert priority_for("interview", "REVIEW") == 65
    assert priority_for("interview", "TRIAGE", is_review=True) == 100


def test_batches_are_stable_and_bounded():
    ids = [f"CAND-{i:016x}" for i in range(5)]
    kwargs = {
        "fingerprints": {candidate_id: "0" * 64 for candidate_id in ids},
        "batch_size": 2,
        "priorities": {candidate_id: 50 for candidate_id in ids},
        "created_at": "2026-09-14T00:00:00Z",
    }
    forward = build_batches("TRIAGE", ids, **kwargs)
    reverse = build_batches("TRIAGE", ids[::-1], **kwargs)
    assert forward == reverse
    assert [len(batch.candidate_ids) for batch in forward] == [2, 2, 1]


def test_active_lease_cannot_be_reclaimed():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    lease = claim_lease(make_batch(), None, worker="GPT-V3", now=now, ttl=timedelta(hours=2))
    assert lease_is_active(lease, now)
    with pytest.raises(ValueError, match="active lease"):
        claim_lease(make_batch(), lease, worker="OTHER", now=now, ttl=timedelta(hours=2))


def test_expired_lease_reclaims_with_incremented_attempt():
    now = datetime(2026, 9, 14, tzinfo=UTC)
    lease = claim_lease(make_batch(), None, worker="GPT-V3", now=now, ttl=timedelta(hours=1))
    later = now + timedelta(hours=1)
    reclaimed = claim_lease(
        make_batch(),
        lease,
        worker="GPT-V3",
        now=later,
        ttl=timedelta(hours=1),
    )
    assert reclaimed.attempt == 2
    assert reclaimed.claimed_at == "2026-09-14T01:00:00Z"


def test_build_batches_rejects_missing_fingerprint():
    with pytest.raises(ValueError, match="fingerprint"):
        build_batches(
            "TRIAGE",
            ["CAND-0123456789abcdef"],
            fingerprints={},
            batch_size=1,
            priorities={"CAND-0123456789abcdef": 50},
            created_at="2026-09-14T00:00:00Z",
        )
