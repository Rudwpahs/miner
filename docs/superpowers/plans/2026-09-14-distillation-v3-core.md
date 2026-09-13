# Distillation V3 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, retry-safe Distillation V3 core that validates raw Miner candidates, creates semantic work queues, manages leases and ledgers, indexes prior knowledge, writes immutable staging records, and enforces Judge-gated canonical promotion.

**Architecture:** Keep all deterministic processing in `Rudwpahs/miner` as small Python 3.12 modules using Pydantic models and pure functions where possible. The V3 core operates on local filesystem fixtures/dry-run state first; remote GitHub/ChatGPT orchestration and private GPU execution are separate implementation plans. Canonical promotion is represented and validated by the Auditor layer, while semantic decisions remain external inputs that must match strict contracts.

**Tech Stack:** Python 3.12, Pydantic >=2.11,<3, httpx >=0.28,<1, pytest >=8.4, ruff >=0.12.

**Spec:** `docs/superpowers/specs/2026-09-14-distillation-v3-design.md`

## Global Constraints

- Preserve the B-policy; deterministic code must never create semantic ACCEPT decisions.
- Raw inbox data must never become canonical knowledge or training data directly.
- Triage decisions are only `REJECT`, `DUPLICATE`, or `DEEP_PENDING`.
- Deep decisions are only `PROPOSE_ACCEPT`, `REVIEW`, or `REJECT`.
- Judge decisions are only `CONFIRM`, `REVIEW`, or `REJECT`.
- Only the Auditor may produce a canonical-promotion record.
- Worker/staging outputs are immutable and retry-safe.
- No vector database, paid API, GPU library, ML framework, or new runtime dependency is added in this plan.
- Existing V2 accepted/review/manifests are read-only historical inputs.
- Existing Miner collection behavior and 20,000-record budget remain unchanged.
- All external CI actions remain pinned to 40-character commit SHAs.

---

## File Structure

Create the focused package below:

```text
src/basketball_miner/distill_v3/
  __init__.py          public V3 exports only
  models.py            strict stage/decision/batch/lease/concept models
  ids.py               DOI/source normalization and stable V3 IDs
  queue.py             deterministic priority sorting, batching, lease rules
  ledger.py            processed-candidate and inbox-blob idempotence state
  router.py            exact deterministic routing into terminal/triage states
  concept_index.py     compact read-only index for V2/V3 knowledge retrieval
  staging.py           immutable JSON/JSONL local staging writer/reader
  audit.py             Judge gate and concept-action promotion validation
  metrics.py           daily counters and invariant checks

scripts/run_distill_v3.py

tests/test_v3_models.py
tests/test_v3_ids.py
tests/test_v3_queue.py
tests/test_v3_ledger_router.py
tests/test_v3_concept_index.py
tests/test_v3_staging_audit.py
tests/test_v3_metrics_cli.py
tests/fixtures/v3/inbox.jsonl
tests/fixtures/v3/v2_accepted.jsonl
tests/fixtures/v3/v2_review.jsonl
```

Do not modify the existing V1 `basketball_miner.models`, `run.py`, `normalize.py`, or `state.py` APIs unless a failing compatibility test demonstrates a concrete need.

---

### Task 1: Strict V3 domain models and legal transitions

**Files:**
- Create: `src/basketball_miner/distill_v3/__init__.py`
- Create: `src/basketball_miner/distill_v3/models.py`
- Create: `tests/test_v3_models.py`

**Interfaces:**
- Produces: `Stage`, `BatchStatus`, `TriageDecision`, `DeepDecision`, `JudgeDecision`, `ConceptAction`, `BatchRecord`, `LeaseRecord`, `CandidateStageState`, `SemanticResult`, `AuditPromotion`.
- All models use `ConfigDict(extra="forbid")` and ISO timestamp strings remain strings in V3 core to match existing repository conventions.

- [ ] **Step 1: Write failing model tests**

```python
import pytest
from pydantic import ValidationError

from basketball_miner.distill_v3.models import (
    AuditPromotion,
    BatchRecord,
    SemanticResult,
)


def test_triage_cannot_emit_accept():
    with pytest.raises(ValidationError):
        SemanticResult(
            candidate_id="CAND-0123456789abcdef",
            stage="TRIAGE",
            decision="CONFIRM",
            reason_code="bad-contract",
        )


def test_batch_requires_nonempty_unique_candidate_ids():
    with pytest.raises(ValidationError):
        BatchRecord(
            batch_id="V3-TRIAGE-0123456789ab",
            stage="TRIAGE",
            candidate_ids=[],
            priority=50,
            created_at="2026-09-14T00:00:00Z",
            input_fingerprints=[],
            status="PENDING",
        )


def test_audit_promotion_requires_judge_confirmation():
    with pytest.raises(ValidationError):
        AuditPromotion(
            candidate_id="CAND-0123456789abcdef",
            knowledge_unit_id="KU-TEST-001",
            judge_decision="REVIEW",
            concept_action="CREATE",
            canonical_date="2026-09-14",
        )
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_v3_models.py -q
```

Expected: collection/import failure because `basketball_miner.distill_v3.models` does not exist.

- [ ] **Step 3: Implement strict enums/literals and validators**

Use string literals/enums with these exact values:

```python
Stage = Literal["TRIAGE", "DEEP", "JUDGE", "REVIEW", "AUDIT"]
BatchStatus = Literal["PENDING", "CLAIMED", "COMPLETE", "FAILED"]
TriageDecision = Literal["REJECT", "DUPLICATE", "DEEP_PENDING"]
DeepDecision = Literal["PROPOSE_ACCEPT", "REVIEW", "REJECT"]
JudgeDecision = Literal["CONFIRM", "REVIEW", "REJECT"]
ConceptAction = Literal["CREATE", "SUPPORT", "REFINE", "CONTRADICT"]
```

`SemanticResult` must use a model validator that checks the decision set against `stage`. `BatchRecord` must reject empty candidate lists, duplicate candidate IDs, length mismatch between `candidate_ids` and `input_fingerprints`, priorities outside 0..100, and malformed candidate IDs. `AuditPromotion` must reject any `judge_decision` other than `CONFIRM`.

- [ ] **Step 4: Run model tests and full model compatibility tests**

```bash
python -m pytest tests/test_v3_models.py tests/test_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/basketball_miner/distill_v3 tests/test_v3_models.py
git add src/basketball_miner/distill_v3 tests/test_v3_models.py
git commit -m "feat: define Distillation V3 contracts"
```

---

### Task 2: Stable source, DOI, candidate, batch, and concept identifiers

**Files:**
- Create: `src/basketball_miner/distill_v3/ids.py`
- Create: `tests/test_v3_ids.py`

**Interfaces:**
- Consumes: existing `CandidateRecord` from `basketball_miner.models`.
- Produces:
  - `normalize_doi(value: str) -> str | None`
  - `normalized_source_key(candidate: CandidateRecord) -> str`
  - `make_batch_id(stage: Stage, candidate_ids: list[str]) -> str`
  - `make_concept_id(topic_codes: list[str], claim_signature: str) -> str`

- [ ] **Step 1: Write failing identifier tests**

```python
from basketball_miner.distill_v3.ids import make_batch_id, normalize_doi


def test_normalize_doi_removes_doi_url_prefix_and_case():
    assert normalize_doi("https://doi.org/10.1519/R-15944.1") == "10.1519/r-15944.1"
    assert normalize_doi("doi:10.1519/R-15944.1") == "10.1519/r-15944.1"


def test_batch_id_is_order_independent_and_stable():
    a = make_batch_id("TRIAGE", ["CAND-aaaaaaaaaaaaaaaa", "CAND-bbbbbbbbbbbbbbbb"])
    b = make_batch_id("TRIAGE", ["CAND-bbbbbbbbbbbbbbbb", "CAND-aaaaaaaaaaaaaaaa"])
    assert a == b
    assert a.startswith("V3-TRIAGE-")
```

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_ids.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement deterministic normalization and SHA-256-derived IDs**

Rules:

```text
normalize_doi:
  strip whitespace -> casefold -> remove https://doi.org/, http://doi.org/, doi:
  accept only strings beginning with 10. followed by a slash-containing suffix
  otherwise return None

normalized_source_key:
  DOI when stable_id or doi.org URL yields a DOI
  otherwise adapter + casefolded stable_id + canonicalized URL

make_batch_id:
  sort unique candidate IDs
  sha256(stage + newline + joined IDs)
  output V3-{STAGE}-{first 12 hex chars}
```

Reuse `basketball_miner.normalize.canonicalize_url`; do not duplicate URL-normalization logic.

- [ ] **Step 4: Run focused and existing normalization tests**

```bash
python -m pytest tests/test_v3_ids.py tests/test_normalize.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/ids.py tests/test_v3_ids.py
git commit -m "feat: add stable V3 identifiers"
```

---

### Task 3: Priority queue, micro-batching, and expiring leases

**Files:**
- Create: `src/basketball_miner/distill_v3/queue.py`
- Create: `tests/test_v3_queue.py`

**Interfaces:**
- Consumes: `BatchRecord`, `LeaseRecord`, `Stage`.
- Produces:
  - `priority_for(source_type: str, stage: Stage, *, is_review: bool = False) -> int`
  - `build_batches(stage: Stage, candidate_ids: list[str], fingerprints: dict[str, str], *, batch_size: int, priorities: dict[str, int]) -> list[BatchRecord]`
  - `claim_lease(batch: BatchRecord, existing: LeaseRecord | None, *, worker: str, now: datetime, ttl: timedelta) -> LeaseRecord`
  - `lease_is_active(lease: LeaseRecord, now: datetime) -> bool`

- [ ] **Step 1: Write failing queue/lease tests**

```python
from datetime import UTC, datetime, timedelta

import pytest

from basketball_miner.distill_v3.models import BatchRecord, LeaseRecord
from basketball_miner.distill_v3.queue import build_batches, claim_lease


def test_build_batches_is_deterministic_and_bounded():
    ids = [f"CAND-{i:016x}" for i in range(5)]
    batches = build_batches(
        "TRIAGE",
        ids,
        {candidate_id: "0" * 64 for candidate_id in ids},
        batch_size=2,
        priorities={candidate_id: 50 for candidate_id in ids},
    )
    assert [len(batch.candidate_ids) for batch in batches] == [2, 2, 1]
    assert batches == build_batches(
        "TRIAGE",
        list(reversed(ids)),
        {candidate_id: "0" * 64 for candidate_id in ids},
        batch_size=2,
        priorities={candidate_id: 50 for candidate_id in ids},
    )


def test_active_lease_prevents_double_claim():
    batch = BatchRecord(
        batch_id="V3-TRIAGE-0123456789ab",
        stage="TRIAGE",
        candidate_ids=["CAND-0123456789abcdef"],
        priority=50,
        created_at="2026-09-14T00:00:00Z",
        input_fingerprints=["0" * 64],
        status="PENDING",
    )
    now = datetime(2026, 9, 14, tzinfo=UTC)
    lease = claim_lease(batch, None, worker="GPT-V3", now=now, ttl=timedelta(hours=2))
    with pytest.raises(ValueError, match="active lease"):
        claim_lease(batch, lease, worker="GPT-V3-B", now=now, ttl=timedelta(hours=2))
```

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_queue.py -q
```

- [ ] **Step 3: Implement deterministic sorting and reclaim semantics**

Batch ordering key must be `(-priority, candidate_id)`. Reclaim is legal only when `now >= expires_at`; reclaimed lease increments `attempt` by one and replaces worker/timestamps. Reject `batch_size < 1`, blank worker IDs, and non-positive TTL.

- [ ] **Step 4: Run focused tests**

```bash
python -m pytest tests/test_v3_queue.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/queue.py tests/test_v3_queue.py
git commit -m "feat: add V3 queues and leases"
```

---

### Task 4: Idempotent ledger and exact deterministic router

**Files:**
- Create: `src/basketball_miner/distill_v3/ledger.py`
- Create: `src/basketball_miner/distill_v3/router.py`
- Create: `tests/test_v3_ledger_router.py`
- Create: `tests/fixtures/v3/inbox.jsonl`

**Interfaces:**
- Produces:
  - `DistillLedger` with sets/maps for processed blob SHAs, terminal candidate IDs, normalized source keys, canonical hashes, and current `CandidateStageState`.
  - `RouteDecision` model or dataclass with `candidate_id`, `route`, `reason_code`, `priority`.
  - `route_candidate(candidate: CandidateRecord, ledger: DistillLedger) -> RouteDecision`.

- [ ] **Step 1: Add a minimal fixture with three strict `CandidateRecord` JSONL rows**

Fixture requirements:

```text
row 1: unique Crossref basketball candidate with DOI 10.1234/v3-a
row 2: different candidate_id but same normalized DOI/source as row 1
row 3: distinct source and canonical_hash
```

All records must use HTTPS URLs and existing CandidateRecord fields only.

- [ ] **Step 2: Write failing ledger/router tests**

```python
from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.router import route_candidate


def test_router_sends_new_candidate_to_triage(candidate_a):
    result = route_candidate(candidate_a, DistillLedger())
    assert result.route == "TRIAGE"


def test_router_terminally_deduplicates_same_source(candidate_a, candidate_same_source):
    ledger = DistillLedger()
    first = route_candidate(candidate_a, ledger)
    ledger.record_routed(candidate_a, first)
    second = route_candidate(candidate_same_source, ledger)
    assert second.route == "DUPLICATE"
    assert second.reason_code == "EXACT_SOURCE"


def test_processed_blob_is_idempotent():
    ledger = DistillLedger()
    assert ledger.mark_blob_processed("a" * 40) is True
    assert ledger.mark_blob_processed("a" * 40) is False
```

- [ ] **Step 3: Verify RED**

```bash
python -m pytest tests/test_v3_ledger_router.py -q
```

- [ ] **Step 4: Implement only deterministic terminal rules**

Routing order:

```text
invalid/known terminal candidate ID -> terminal
same normalized source key -> DUPLICATE / EXACT_SOURCE
same canonical_hash -> DUPLICATE / EXACT_CANONICAL_HASH
otherwise -> TRIAGE
```

Do not implement semantic off-topic rejection here; Miner relevance has already run, and V3 semantic rejection belongs to GPT Triage.

`DistillLedger` must serialize with sorted keys/IDs so round trips are deterministic.

- [ ] **Step 5: Test and commit**

```bash
python -m pytest tests/test_v3_ledger_router.py -q
python -m ruff check src/basketball_miner/distill_v3 tests/test_v3_ledger_router.py
git add src/basketball_miner/distill_v3/ledger.py src/basketball_miner/distill_v3/router.py tests/test_v3_ledger_router.py tests/fixtures/v3/inbox.jsonl
git commit -m "feat: add idempotent V3 routing ledger"
```

---

### Task 5: Compact concept index with V2 read-only compatibility

**Files:**
- Create: `src/basketball_miner/distill_v3/concept_index.py`
- Create: `tests/test_v3_concept_index.py`
- Create: `tests/fixtures/v3/v2_accepted.jsonl`
- Create: `tests/fixtures/v3/v2_review.jsonl`

**Interfaces:**
- Produces:
  - `ConceptIndexRecord` model with `knowledge_unit_id`, optional `concept_id`, `normalized_source_id`, `topic_codes`, `claim_signature`, `status`.
  - `claim_signature(claim: str) -> str` using deterministic normalized token selection, not embeddings.
  - `build_index(accepted_rows: list[dict], review_rows: list[dict]) -> list[ConceptIndexRecord]`.
  - `shortlist(index, *, source_id: str | None, topic_codes: list[str], claim: str, limit: int = 20) -> list[ConceptIndexRecord]`.

- [ ] **Step 1: Add historical-shaped fixtures**

Accepted fixture must include fields already present in hoopDB-style KUs: `knowledge_unit_id`, `claim`, `source_identifier`, and `topic_codes`. Review fixture must include a plausible candidate ID and title/source fields but no fabricated canonical acceptance.

- [ ] **Step 2: Write failing tests**

```python
from basketball_miner.distill_v3.concept_index import build_index, shortlist


def test_v2_rows_build_without_mutating_input(v2_accepted_rows, v2_review_rows):
    before = [row.copy() for row in v2_accepted_rows]
    index = build_index(v2_accepted_rows, v2_review_rows)
    assert index
    assert v2_accepted_rows == before


def test_shortlist_prefers_exact_source_then_topic_overlap(v2_index):
    rows = shortlist(
        v2_index,
        source_id="10.1519/r-15944.1",
        topic_codes=["PLAYER_PROFILE", "POSITION"],
        claim="Elite players differ by positional role",
        limit=5,
    )
    assert rows[0].normalized_source_id == "10.1519/r-15944.1"
    assert len(rows) <= 5
```

- [ ] **Step 3: Verify RED**

```bash
python -m pytest tests/test_v3_concept_index.py -q
```

- [ ] **Step 4: Implement deterministic retrieval scoring**

Use score components only:

```text
+100 exact normalized source ID
+10 per overlapping topic code
+1 per overlapping claim-signature token, capped at 10
```

Tie-break by `knowledge_unit_id`. `claim_signature` must casefold, remove punctuation, collapse whitespace, drop tokens shorter than 3 characters, deduplicate, sort, and join at most 32 tokens.

- [ ] **Step 5: Test and commit**

```bash
python -m pytest tests/test_v3_concept_index.py -q
git add src/basketball_miner/distill_v3/concept_index.py tests/test_v3_concept_index.py tests/fixtures/v3/v2_accepted.jsonl tests/fixtures/v3/v2_review.jsonl
git commit -m "feat: index V2 knowledge for V3 retrieval"
```

---

### Task 6: Immutable staging and Judge-gated audit promotion

**Files:**
- Create: `src/basketball_miner/distill_v3/staging.py`
- Create: `src/basketball_miner/distill_v3/audit.py`
- Create: `tests/test_v3_staging_audit.py`

**Interfaces:**
- Produces:
  - `write_immutable_json(path: Path, payload: BaseModel | dict) -> None`
  - `write_immutable_jsonl(path: Path, rows: list[BaseModel | dict]) -> None`
  - `read_jsonl(path: Path) -> list[dict]`
  - `validate_promotion(result: SemanticResult, *, knowledge_unit_id: str, concept_action: ConceptAction, canonical_date: str) -> AuditPromotion`
  - `apply_concept_action(existing_concept: dict | None, promotion: AuditPromotion) -> dict` with explicit CREATE/SUPPORT/REFINE/CONTRADICT branches.

- [ ] **Step 1: Write failing immutability/audit tests**

```python
from pathlib import Path

import pytest

from basketball_miner.distill_v3.audit import validate_promotion
from basketball_miner.distill_v3.models import SemanticResult
from basketball_miner.distill_v3.staging import write_immutable_json


def test_immutable_writer_allows_idempotent_same_bytes_but_refuses_change(tmp_path: Path):
    path = tmp_path / "run.json"
    write_immutable_json(path, {"status": "COMPLETE"})
    write_immutable_json(path, {"status": "COMPLETE"})
    with pytest.raises(FileExistsError):
        write_immutable_json(path, {"status": "FAILED"})


def test_only_judge_confirm_can_promote():
    result = SemanticResult(
        candidate_id="CAND-0123456789abcdef",
        stage="JUDGE",
        decision="REVIEW",
        reason_code="needs-source",
    )
    with pytest.raises(ValueError, match="CONFIRM"):
        validate_promotion(
            result,
            knowledge_unit_id="KU-TEST-001",
            concept_action="CREATE",
            canonical_date="2026-09-14",
        )
```

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_staging_audit.py -q
```

- [ ] **Step 3: Implement canonical byte serialization and promotion gate**

Canonical JSON serialization must use `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, UTF-8, and newline termination. Existing file with identical bytes is an idempotent success; existing file with different bytes raises `FileExistsError`.

`validate_promotion` must require `stage == "JUDGE"` and `decision == "CONFIRM"`.

- [ ] **Step 4: Implement explicit concept actions**

Concept representation for core tests:

```python
{
    "concept_id": "CONCEPT-...",
    "primary_knowledge_unit_id": "KU-...",
    "supporting_knowledge_unit_ids": [],
    "refinement_knowledge_unit_ids": [],
    "contradicting_knowledge_unit_ids": [],
}
```

`CREATE` requires no existing concept; the other three require one. Never silently convert an invalid action into another action.

- [ ] **Step 5: Test and commit**

```bash
python -m pytest tests/test_v3_staging_audit.py -q
git add src/basketball_miner/distill_v3/staging.py src/basketball_miner/distill_v3/audit.py tests/test_v3_staging_audit.py
git commit -m "feat: enforce immutable V3 staging and Judge gate"
```

---

### Task 7: Metrics, release invariants, and daily-audit starvation check

**Files:**
- Create: `src/basketball_miner/distill_v3/metrics.py`
- Create: `tests/test_v3_metrics_cli.py`

**Interfaces:**
- Produces `DailyMetrics` Pydantic model and:
  - `check_release_invariants(metrics: DailyMetrics) -> list[str]`
  - `audit_due(*, now: datetime, last_completed_audit_date: date | None, timezone: ZoneInfo) -> date | None`

- [ ] **Step 1: Write failing tests for all hard invariants**

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from basketball_miner.distill_v3.metrics import DailyMetrics, audit_due, check_release_invariants


def test_release_invariants_flag_raw_bypass_and_unconfirmed_accept():
    metrics = DailyMetrics(
        date="2026-09-14",
        raw_candidates=10,
        unique_candidates=9,
        exact_duplicates=1,
        invalid_records=0,
        canonical_accepts=2,
        canonical_without_judge=1,
        raw_to_canonical_bypass=1,
        simultaneous_valid_lease_conflicts=0,
    )
    failures = check_release_invariants(metrics)
    assert "raw_to_canonical_bypass" in failures
    assert "canonical_without_judge" in failures


def test_previous_day_audit_is_due_after_seoul_day_boundary():
    due = audit_due(
        now=datetime.fromisoformat("2026-09-14T01:10:00+09:00"),
        last_completed_audit_date=None,
        timezone=ZoneInfo("Asia/Seoul"),
    )
    assert due.isoformat() == "2026-09-13"
```

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_metrics_cli.py -q
```

- [ ] **Step 3: Implement metrics without inferred semantic counts**

Metrics fields are explicit counters supplied by processing/audit outputs. `check_release_invariants` returns stable machine-readable failure keys, including at minimum:

```text
raw_to_canonical_bypass
canonical_without_judge
simultaneous_valid_lease_conflicts
```

`audit_due` compares Asia/Seoul local dates and returns yesterday when yesterday has not been completed. It never marks an audit complete itself.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_v3_metrics_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/metrics.py tests/test_v3_metrics_cli.py
git commit -m "feat: add V3 quality invariants and audit cadence"
```

---

### Task 8: Dry-run CLI integrating validation, routing, batching, and state output

**Files:**
- Create: `scripts/run_distill_v3.py`
- Modify: `tests/test_v3_metrics_cli.py`
- Modify: `src/basketball_miner/distill_v3/__init__.py`

**Interfaces:**
- CLI arguments:
  - `--inbox-jsonl PATH` required
  - `--state-dir PATH` required
  - `--batch-size INTEGER` default `100`
  - `--dry-run` required for V3 core plan
- Output: one JSON summary line containing `status`, `raw_records`, `valid_candidates`, `duplicates`, `triage_candidates`, `triage_batches`, and `would_write_state`.

- [ ] **Step 1: Add failing subprocess test**

```python
import json
import subprocess
import sys


def test_v3_cli_dry_run_is_deterministic_and_does_not_write_state(tmp_path, v3_fixture_path):
    state_dir = tmp_path / "state"
    command = [
        sys.executable,
        "scripts/run_distill_v3.py",
        "--inbox-jsonl",
        str(v3_fixture_path),
        "--state-dir",
        str(state_dir),
        "--batch-size",
        "2",
        "--dry-run",
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert not state_dir.exists()
```

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_metrics_cli.py::test_v3_cli_dry_run_is_deterministic_and_does_not_write_state -q
```

- [ ] **Step 3: Implement CLI with strict JSONL validation**

Read each nonblank line with `json.loads` then `CandidateRecord.model_validate`. Invalid rows increment `invalid_records` and are not routed. In dry-run mode, construct ledger/batches entirely in memory and never create `state_dir`.

Do not add private repository access or secrets to this script in the core plan.

- [ ] **Step 4: Run focused CLI tests and existing dry-run tests**

```bash
python -m pytest tests/test_v3_metrics_cli.py tests/test_end_to_end_dry_run.py -q
```

- [ ] **Step 5: Commit**

```bash
git add scripts/run_distill_v3.py src/basketball_miner/distill_v3/__init__.py tests/test_v3_metrics_cli.py
git commit -m "feat: add Distillation V3 dry-run pipeline"
```

---

### Task 9: Full regression gate and core documentation

**Files:**
- Modify: `README.md`
- Modify: `tests/test_workflow_policy.py` only if necessary to assert the public repository contains no self-hosted/GPU workflow in this plan.

**Interfaces:**
- No new runtime API.
- Documentation must distinguish V1 Miner collection from V3 dry-run distillation core.

- [ ] **Step 1: Add a failing policy assertion before README/workflow changes**

```python
def test_public_workflows_do_not_request_self_hosted_or_gpu_runner():
    combined = _workflow_text(TEST_WORKFLOW) + _workflow_text(MINE_WORKFLOW)
    assert "self-hosted" not in combined
    assert "cuda" not in combined.casefold()
```

- [ ] **Step 2: Run workflow policy tests**

```bash
python -m pytest tests/test_workflow_policy.py -q
```

Expected: PASS on current workflows. This is a characterization test protecting the V3 boundary rather than a forced RED; record that fact in the commit message/review notes.

- [ ] **Step 3: Update README with exact V3 core commands**

Document:

```bash
python scripts/run_distill_v3.py \
  --inbox-jsonl tests/fixtures/v3/inbox.jsonl \
  --state-dir _v3_state \
  --batch-size 100 \
  --dry-run
```

State explicitly that this core command performs no semantic ACCEPT, no canonical promotion, no GPU work, and no private write.

- [ ] **Step 4: Run complete verification**

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

Expected: all tests pass and ruff reports no errors.

- [ ] **Step 5: Inspect branch diff for scope**

```bash
git diff --stat work/miner-40x-throughput...HEAD
git diff work/miner-40x-throughput...HEAD -- src/basketball_miner/distill_v3 scripts/run_distill_v3.py tests README.md
```

Confirm there are no changes to Miner source adapters, 40x schedule, external source policy, or GPU execution.

- [ ] **Step 6: Commit documentation/policy guard**

```bash
git add README.md tests/test_workflow_policy.py
git commit -m "docs: document Distillation V3 core boundary"
```

---

## Core Completion Gate

Before starting the ChatGPT Orchestrator plan, all of these must be evidenced by tests or code review:

```text
[ ] Candidate/source/hash exact duplicates are deterministic.
[ ] Queue batches are deterministic and bounded.
[ ] Active leases cannot be double-claimed; expired leases are reclaimable.
[ ] Reprocessing the same blob/candidate is idempotent.
[ ] V2 accepted/review files are read-only index inputs.
[ ] Triage cannot ACCEPT.
[ ] Deep cannot directly canonicalize.
[ ] Judge REVIEW/REJECT cannot canonicalize.
[ ] Immutable staging refuses changed overwrite.
[ ] Only AuditPromotion represents canonical promotion.
[ ] Previous-day audit due state cannot starve behind ordinary backlog.
[ ] Public repository code has no GPU execution path.
[ ] Full pytest and ruff gates are green.
```

## Deferred Plans

The following are intentionally excluded from this file and must each receive a separate Superpowers implementation plan after V3 Core passes its completion gate:

1. **Distillation V3 Orchestrator:** private hoopDB storage adapter, queue/lease persistence, hourly ChatGPT scheduled-task contract, shadow-mode semantic processing, daily canonical writer integration.
2. **FormPath Private Compute:** `Rudwpahs/formpath-compute`, private self-hosted RTX 4060 runner, declarative GPU jobs/results, Qwen/FormQuant/QLoRA execution boundary.
