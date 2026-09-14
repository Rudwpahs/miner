# Distillation V3 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, retry-safe Distillation V3 core that turns validated Miner candidates into idempotent semantic work queues and permits canonical promotion only after Judge confirmation and Auditor validation.

**Architecture:** Add a focused `basketball_miner.distill_v3` package beside the existing collector without changing V1 collection APIs. Use strict Pydantic contracts, deterministic SHA-256 identifiers, exact deduplication, bounded micro-batches, expiring leases, a read-only V2 knowledge index, immutable local staging, explicit release invariants, and a dry-run CLI. Remote hoopDB persistence, the hourly ChatGPT orchestrator, and private RTX 4060 execution are separate plans.

**Tech Stack:** Python 3.12; Pydantic >=2.11,<3; httpx >=0.28,<1; pytest >=8.4; ruff >=0.12; Python standard library `hashlib`, `json`, `datetime`, `zoneinfo`, `pathlib`, `re`.

**Spec:** `docs/superpowers/specs/2026-09-14-distillation-v3-design.md`

## Global Constraints

- B-policy thresholds do not change.
- Deterministic code never semantically ACCEPTs a candidate.
- Triage outputs only `REJECT | DUPLICATE | DEEP_PENDING`.
- Deep outputs only `PROPOSE_ACCEPT | REVIEW | REJECT`.
- Review Resolver outputs only `PROPOSE_ACCEPT | REVIEW | REJECT`; a review-originated proposal still goes through Judge.
- Judge outputs only `CONFIRM | REVIEW | REJECT`.
- Only `AuditPromotion` represents canonical promotion in V3 Core, and it requires Judge `CONFIRM`.
- Raw inbox records never become training or canonical data directly.
- Existing V2 accepted/review/manifests are read-only inputs.
- Immutable stage files may be re-written only when bytes are identical.
- No vector database, paid API, GPU package, ML framework, or new runtime dependency is added.
- Existing 40x Miner behavior, source adapters, and 20,000-record inspection ceiling remain unchanged.
- Public workflows remain free of `self-hosted`/GPU execution.

---

## File Map

```text
src/basketball_miner/distill_v3/
  __init__.py
  models.py
  ids.py
  queue.py
  ledger.py
  router.py
  concept_index.py
  staging.py
  audit.py
  metrics.py

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
tests/fixtures/v3/v2_manifest.json
```

Existing `basketball_miner.models`, `run.py`, `normalize.py`, and `state.py` stay API-compatible.

---

### Task 1: Define strict V3 contracts

**Files:**
- Create: `src/basketball_miner/distill_v3/__init__.py`
- Create: `src/basketball_miner/distill_v3/models.py`
- Create: `tests/test_v3_models.py`

**Interfaces:**

```python
Stage = Literal["TRIAGE", "DEEP", "JUDGE", "REVIEW", "AUDIT"]
BatchStatus = Literal["PENDING", "CLAIMED", "COMPLETE", "FAILED"]
TriageDecision = Literal["REJECT", "DUPLICATE", "DEEP_PENDING"]
DeepDecision = Literal["PROPOSE_ACCEPT", "REVIEW", "REJECT"]
ReviewDecision = Literal["PROPOSE_ACCEPT", "REVIEW", "REJECT"]
JudgeDecision = Literal["CONFIRM", "REVIEW", "REJECT"]
ConceptAction = Literal["CREATE", "SUPPORT", "REFINE", "CONTRADICT"]
Route = Literal["TRIAGE", "DUPLICATE", "TERMINAL_INVALID", "TERMINAL_PROCESSED"]
AuditStatus = Literal["COMPLETED", "BLOCKED"]
```

Models and exact fields:

```text
BatchRecord:
  batch_id, stage, candidate_ids, priority, created_at,
  input_fingerprints, status

LeaseRecord:
  batch_id, worker, claimed_at, expires_at, attempt

CandidateStageState:
  candidate_id, source_fingerprint, stage, status,
  batch_id(optional), attempt, updated_at

SemanticResult:
  candidate_id, stage(TRIAGE|DEEP|JUDGE|REVIEW), decision,
  reason_code, evidence_refs(default []),
  knowledge_unit_id(optional), concept_id(optional), concept_action(optional)

RouteDecision:
  candidate_id, route, reason_code, priority

AuditPromotion:
  candidate_id, knowledge_unit_id, concept_id,
  judge_decision(CONFIRM only), concept_action, canonical_date

DailyAuditRecord:
  audit_date, status, recorded_at, reason_code(optional)
```

Every model uses `ConfigDict(extra="forbid")`. Candidate IDs match `^CAND-[0-9a-f]{16}$`; fingerprints match `^[0-9a-f]{64}$`; priorities are 0..100; lease attempts are >=1; candidate-state attempts are >=0.

- [ ] **Step 1: Write RED tests for stage decisions and Judge gate**

```python
import pytest
from pydantic import ValidationError

from basketball_miner.distill_v3.models import AuditPromotion, BatchRecord, SemanticResult


def test_triage_cannot_confirm():
    with pytest.raises(ValidationError):
        SemanticResult(
            candidate_id="CAND-0123456789abcdef",
            stage="TRIAGE",
            decision="CONFIRM",
            reason_code="illegal",
        )


def test_review_proposal_is_legal_but_not_canonical():
    result = SemanticResult(
        candidate_id="CAND-0123456789abcdef",
        stage="REVIEW",
        decision="PROPOSE_ACCEPT",
        reason_code="source-resolved",
        knowledge_unit_id="KU-TEST-001",
    )
    assert result.decision == "PROPOSE_ACCEPT"


def test_audit_promotion_rejects_nonconfirm():
    with pytest.raises(ValidationError):
        AuditPromotion(
            candidate_id="CAND-0123456789abcdef",
            knowledge_unit_id="KU-TEST-001",
            concept_id="CONCEPT-0123456789ab",
            judge_decision="REVIEW",
            concept_action="CREATE",
            canonical_date="2026-09-14",
        )
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m pytest tests/test_v3_models.py -q
```

Expected: import/collection failure.

- [ ] **Step 3: Implement models and model validators**

`SemanticResult` validates decision by stage with an explicit mapping; `AUDIT` is rejected as a SemanticResult stage. `BatchRecord` rejects empty/duplicate candidate lists, fingerprint-count mismatch, malformed batch ID, and >100 candidates. `AuditPromotion` accepts only `judge_decision="CONFIRM"`.

- [ ] **Step 4: Verify focused + existing model tests**

```bash
python -m pytest tests/test_v3_models.py tests/test_models.py -q
python -m ruff check src/basketball_miner/distill_v3 tests/test_v3_models.py
```

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3 tests/test_v3_models.py
git commit -m "feat: define Distillation V3 contracts"
```

---

### Task 2: Implement stable V3 identifiers

**Files:**
- Create: `src/basketball_miner/distill_v3/ids.py`
- Create: `tests/test_v3_ids.py`

**Interfaces:**

```python
normalize_doi(value: str) -> str | None
normalized_source_key(candidate: CandidateRecord) -> str
make_batch_id(stage: Stage, candidate_ids: list[str]) -> str
make_concept_id(topic_codes: list[str], claim_signature: str) -> str
```

Rules:

```text
normalize_doi:
  strip -> casefold -> remove doi: / http(s)://doi.org/
  valid only when value begins "10." and contains "/"

normalized_source_key:
  "doi:" + normalized DOI when stable_id or URL yields DOI
  otherwise "source:" + sha256(adapter + stable_id + canonicalized URL)

make_batch_id:
  validate nonempty IDs -> sorted unique IDs -> sha256(stage + newline + joined IDs)
  `V3-{STAGE}-{12 hex}`

make_concept_id:
  normalize topic codes by strip/upper/sort/dedup
  normalize claim signature by strip/casefold
  sha256(joined topics + newline + signature)
  `CONCEPT-{12 hex}`
```

- [ ] **Step 1: Write RED tests including concept determinism**

```python
from basketball_miner.distill_v3.ids import make_batch_id, make_concept_id, normalize_doi


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1519/R-15944.1") == "10.1519/r-15944.1"
    assert normalize_doi("doi:10.1519/R-15944.1") == "10.1519/r-15944.1"
    assert normalize_doi("not-a-doi") is None


def test_batch_id_is_order_independent():
    ids = ["CAND-aaaaaaaaaaaaaaaa", "CAND-bbbbbbbbbbbbbbbb"]
    assert make_batch_id("TRIAGE", ids) == make_batch_id("TRIAGE", list(reversed(ids)))


def test_concept_id_normalizes_topic_order():
    assert make_concept_id(["SHOOTING", "VISION"], "target visibility") == make_concept_id(
        ["vision", "shooting"], "TARGET VISIBILITY"
    )
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_v3_ids.py -q
```

- [ ] **Step 3: Implement using SHA-256 and existing `canonicalize_url`**

Do not duplicate URL normalization from `basketball_miner.normalize`.

- [ ] **Step 4: Verify**

```bash
python -m pytest tests/test_v3_ids.py tests/test_normalize.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/ids.py tests/test_v3_ids.py
git commit -m "feat: add stable V3 identifiers"
```

---

### Task 3: Implement deterministic priority queues and leases

**Files:**
- Create: `src/basketball_miner/distill_v3/queue.py`
- Create: `tests/test_v3_queue.py`

**Interfaces:**

```python
priority_for(source_type: str, stage: Stage, *, is_review: bool = False) -> int
build_batches(stage: Stage, candidate_ids: list[str], fingerprints: dict[str, str], *, batch_size: int, priorities: dict[str, int], created_at: str) -> list[BatchRecord]
lease_is_active(lease: LeaseRecord, now: datetime) -> bool
claim_lease(batch: BatchRecord, existing: LeaseRecord | None, *, worker: str, now: datetime, ttl: timedelta) -> LeaseRecord
```

Priority formula is fixed:

```text
source base: official=90, academic=85, coaching=65, interview=55
stage bonus: TRIAGE=0, DEEP=3, JUDGE=5, REVIEW=10, AUDIT=10
is_review=True => 100
final priority = min(100, base + stage bonus)
```

Batch ordering is `(-priority, candidate_id)`; each batch priority is the maximum member priority. Reject missing fingerprints/priorities, `batch_size < 1`, blank worker, or TTL <=0. Expired lease reclaim increments attempt; active lease double-claim raises `ValueError`.

- [ ] **Step 1: Write RED tests**

```python
from datetime import UTC, datetime, timedelta

import pytest

from basketball_miner.distill_v3.queue import build_batches, claim_lease, priority_for


def test_priority_policy_is_fixed():
    assert priority_for("official", "TRIAGE") == 90
    assert priority_for("academic", "DEEP") == 88
    assert priority_for("coaching", "JUDGE") == 70
    assert priority_for("interview", "REVIEW") == 65
    assert priority_for("interview", "TRIAGE", is_review=True) == 100


def test_batches_are_stable_and_bounded():
    ids = [f"CAND-{i:016x}" for i in range(5)]
    kwargs = {
        "fingerprints": {x: "0" * 64 for x in ids},
        "batch_size": 2,
        "priorities": {x: 50 for x in ids},
        "created_at": "2026-09-14T00:00:00Z",
    }
    assert build_batches("TRIAGE", ids, **kwargs) == build_batches("TRIAGE", ids[::-1], **kwargs)


def test_active_lease_cannot_be_reclaimed(batch_record):
    now = datetime(2026, 9, 14, tzinfo=UTC)
    lease = claim_lease(batch_record, None, worker="GPT-V3", now=now, ttl=timedelta(hours=2))
    with pytest.raises(ValueError, match="active lease"):
        claim_lease(batch_record, lease, worker="OTHER", now=now, ttl=timedelta(hours=2))
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_v3_queue.py -q
```

- [ ] **Step 3: Implement minimal queue/lease logic**

Timestamp output is UTC ISO 8601 ending `Z`; parsing accepts `Z` by replacing it with `+00:00` before `datetime.fromisoformat`.

- [ ] **Step 4: Verify and lint**

```bash
python -m pytest tests/test_v3_queue.py -q
python -m ruff check src/basketball_miner/distill_v3/queue.py tests/test_v3_queue.py
```

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/queue.py tests/test_v3_queue.py
git commit -m "feat: add V3 queues and leases"
```

---

### Task 4: Implement idempotent ledger, V2 history seeding, and exact router

**Files:**
- Create: `src/basketball_miner/distill_v3/ledger.py`
- Create: `src/basketball_miner/distill_v3/router.py`
- Create: `tests/test_v3_ledger_router.py`
- Create: `tests/fixtures/v3/inbox.jsonl`
- Create: `tests/fixtures/v3/v2_manifest.json`

**Interfaces:**

```python
class DistillLedger(BaseModel):
    processed_blob_shas: set[str]
    terminal_candidate_ids: set[str]
    review_candidate_ids: set[str]
    normalized_source_keys: set[str]
    canonical_hashes: set[str]
    candidate_states: dict[str, CandidateStageState]

    def mark_blob_processed(self, blob_sha: str) -> bool: ...
    def record_route(self, candidate: CandidateRecord, route: RouteDecision) -> None: ...

seed_from_v2_history(ledger: DistillLedger, *, accepted_rows: list[dict], review_rows: list[dict], manifests: list[dict]) -> DistillLedger
route_candidate(candidate: CandidateRecord, ledger: DistillLedger) -> RouteDecision
```

V2 seeding precedence:

```text
1. manifest input_files[].blob_sha -> processed_blob_shas
2. manifest processed_candidate_ids -> terminal by default
3. accepted rows source_candidate_id/candidate_id -> terminal
4. current review rows candidate_id/source_candidate_id -> remove from terminal and add to review
```

If the same ID appears in accepted and review fixtures, accepted wins because canonical acceptance is terminal. Therefore final precedence is `accepted terminal > current review > generic processed terminal`.

Exact route order:

```text
candidate ID in terminal -> TERMINAL_PROCESSED / ALREADY_PROCESSED
candidate ID in review -> TERMINAL_PROCESSED / ACTIVE_REVIEW
normalized source key already seen -> DUPLICATE / EXACT_SOURCE
canonical_hash already seen -> DUPLICATE / EXACT_CANONICAL_HASH
otherwise -> TRIAGE / NEW_SEMANTIC_CANDIDATE
```

No semantic off-topic rejection exists in deterministic router.

- [ ] **Step 1: Add strict fixtures**

`inbox.jsonl` contains three valid CandidateRecord rows: one unique DOI candidate, one distinct candidate ID sharing its DOI, and one distinct candidate/hash. `v2_manifest.json` contains one processed blob SHA, processed candidate IDs, and one unresolved review ID.

- [ ] **Step 2: Write RED tests**

```python
from basketball_miner.distill_v3.ledger import DistillLedger, seed_from_v2_history
from basketball_miner.distill_v3.router import route_candidate


def test_new_candidate_routes_to_triage(candidate_a):
    assert route_candidate(candidate_a, DistillLedger()).route == "TRIAGE"


def test_same_normalized_source_is_duplicate(candidate_a, candidate_same_source):
    ledger = DistillLedger()
    first = route_candidate(candidate_a, ledger)
    ledger.record_route(candidate_a, first)
    second = route_candidate(candidate_same_source, ledger)
    assert (second.route, second.reason_code) == ("DUPLICATE", "EXACT_SOURCE")


def test_v2_manifest_seeds_blob_and_keeps_current_review_nonterminal(v2_history):
    ledger = seed_from_v2_history(DistillLedger(), **v2_history)
    assert "a" * 40 in ledger.processed_blob_shas
    assert "CAND-1111111111111111" in ledger.review_candidate_ids
    assert "CAND-1111111111111111" not in ledger.terminal_candidate_ids
```

- [ ] **Step 3: Run RED**

```bash
python -m pytest tests/test_v3_ledger_router.py -q
```

- [ ] **Step 4: Implement sorted deterministic serialization**

`DistillLedger.model_dump(mode="json")` output must be normalized before file persistence by sorting every set/list and dictionary key in a helper `ledger_payload(ledger) -> dict`.

- [ ] **Step 5: Verify and commit**

```bash
python -m pytest tests/test_v3_ledger_router.py -q
git add src/basketball_miner/distill_v3/ledger.py src/basketball_miner/distill_v3/router.py tests/test_v3_ledger_router.py tests/fixtures/v3/inbox.jsonl tests/fixtures/v3/v2_manifest.json
git commit -m "feat: add idempotent V3 routing ledger"
```

---

### Task 5: Build compact V2/V3 concept index

**Files:**
- Create: `src/basketball_miner/distill_v3/concept_index.py`
- Create: `tests/test_v3_concept_index.py`
- Create: `tests/fixtures/v3/v2_accepted.jsonl`
- Create: `tests/fixtures/v3/v2_review.jsonl`

**Interfaces:**

```python
class ConceptIndexRecord(BaseModel):
    knowledge_unit_id: str
    concept_id: str | None
    normalized_source_id: str | None
    topic_codes: list[str]
    claim_signature: str
    status: Literal["ACCEPTED", "REVIEW"]

claim_signature(claim: str) -> str
build_index(accepted_rows: list[dict], review_rows: list[dict]) -> list[ConceptIndexRecord]
shortlist(index: list[ConceptIndexRecord], *, source_id: str | None, topic_codes: list[str], claim: str, limit: int = 20) -> list[ConceptIndexRecord]
```

`claim_signature` casefolds, replaces non-alphanumeric Unicode word characters with spaces, drops tokens shorter than 3 characters, deduplicates/sorts, and joins at most 32 tokens.

Retrieval score:

```text
+100 exact normalized source ID
+10 per topic overlap
+1 per claim-signature token overlap, capped +10
```

Sort by score descending then `knowledge_unit_id`; omit zero-score rows. No embeddings.

- [ ] **Step 1: Add historical-shaped accepted/review fixtures**

Accepted rows include `knowledge_unit_id`, `source_candidate_id`, `claim`, `source_identifier`, `topic_codes`. Review rows include `candidate_id`, `source_identifier` or URL, title/claim-like text, and topic codes when available.

- [ ] **Step 2: Write RED tests**

```python
from basketball_miner.distill_v3.concept_index import build_index, shortlist


def test_build_index_does_not_mutate_v2_rows(v2_accepted_rows, v2_review_rows):
    before = [row.copy() for row in v2_accepted_rows]
    assert build_index(v2_accepted_rows, v2_review_rows)
    assert v2_accepted_rows == before


def test_exact_source_outranks_topic_only(v2_index):
    rows = shortlist(
        v2_index,
        source_id="10.1519/r-15944.1",
        topic_codes=["PLAYER_PROFILE", "POSITION"],
        claim="elite positional player profile",
        limit=5,
    )
    assert rows[0].normalized_source_id == "10.1519/r-15944.1"
```

- [ ] **Step 3: Run RED**

```bash
python -m pytest tests/test_v3_concept_index.py -q
```

- [ ] **Step 4: Implement tolerant read-only adapters**

Missing optional V2 fields become `None`/empty lists. Malformed rows that lack both a usable KU/candidate identifier and useful text raise `ValueError` in fixture/core calls rather than silently inventing data.

- [ ] **Step 5: Verify and commit**

```bash
python -m pytest tests/test_v3_concept_index.py -q
git add src/basketball_miner/distill_v3/concept_index.py tests/test_v3_concept_index.py tests/fixtures/v3/v2_accepted.jsonl tests/fixtures/v3/v2_review.jsonl
git commit -m "feat: index historical knowledge for V3"
```

---

### Task 6: Enforce immutable staging and concept-aware Audit promotion

**Files:**
- Create: `src/basketball_miner/distill_v3/staging.py`
- Create: `src/basketball_miner/distill_v3/audit.py`
- Create: `tests/test_v3_staging_audit.py`

**Interfaces:**

```python
write_immutable_json(path: Path, payload: BaseModel | dict) -> None
write_immutable_jsonl(path: Path, rows: list[BaseModel | dict]) -> None
read_jsonl(path: Path) -> list[dict]
validate_promotion(result: SemanticResult, *, knowledge_unit_id: str, concept_id: str, concept_action: ConceptAction, canonical_date: str) -> AuditPromotion
apply_concept_action(existing: dict | None, promotion: AuditPromotion) -> dict
```

Canonical bytes use UTF-8, `ensure_ascii=False`, sorted keys, compact separators, and one final newline. Same bytes at existing path are idempotent success; different bytes raise `FileExistsError`.

Concept shape:

```python
{
    "concept_id": "CONCEPT-0123456789ab",
    "primary_knowledge_unit_id": "KU-...",
    "supporting_knowledge_unit_ids": [],
    "refinement_knowledge_unit_ids": [],
    "contradicting_knowledge_unit_ids": [],
}
```

`CREATE` requires `existing is None`. `SUPPORT`, `REFINE`, `CONTRADICT` require matching existing concept ID. Duplicate KU insertion is idempotent, not duplicated.

- [ ] **Step 1: Write RED tests**

```python
from pathlib import Path

import pytest

from basketball_miner.distill_v3.audit import validate_promotion
from basketball_miner.distill_v3.models import SemanticResult
from basketball_miner.distill_v3.staging import write_immutable_json


def test_immutable_file_refuses_changed_overwrite(tmp_path: Path):
    path = tmp_path / "run.json"
    write_immutable_json(path, {"status": "COMPLETE"})
    write_immutable_json(path, {"status": "COMPLETE"})
    with pytest.raises(FileExistsError):
        write_immutable_json(path, {"status": "FAILED"})


def test_deep_proposal_cannot_promote_without_judge():
    result = SemanticResult(
        candidate_id="CAND-0123456789abcdef",
        stage="DEEP",
        decision="PROPOSE_ACCEPT",
        reason_code="supported",
        knowledge_unit_id="KU-TEST-001",
    )
    with pytest.raises(ValueError, match="Judge CONFIRM"):
        validate_promotion(
            result,
            knowledge_unit_id="KU-TEST-001",
            concept_id="CONCEPT-0123456789ab",
            concept_action="CREATE",
            canonical_date="2026-09-14",
        )
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_v3_staging_audit.py -q
```

- [ ] **Step 3: Implement immutable serialization and strict Judge gate**

`validate_promotion` requires `result.stage == "JUDGE"`, `result.decision == "CONFIRM"`, and matching nonblank KU/concept identifiers.

- [ ] **Step 4: Implement four concept actions and tests for each**

Add one positive and one invalid-state test for CREATE/SUPPORT/REFINE/CONTRADICT.

- [ ] **Step 5: Verify and commit**

```bash
python -m pytest tests/test_v3_staging_audit.py -q
git add src/basketball_miner/distill_v3/staging.py src/basketball_miner/distill_v3/audit.py tests/test_v3_staging_audit.py
git commit -m "feat: enforce V3 staging and audit gate"
```

---

### Task 7: Add complete daily metrics and audit starvation prevention

**Files:**
- Create: `src/basketball_miner/distill_v3/metrics.py`
- Create: `tests/test_v3_metrics_cli.py`

**Interfaces:**

`DailyMetrics` contains these fields, all integer counters defaulting to 0 except `date` and structured backlog fields:

```text
date
raw_candidates
unique_candidates
exact_duplicates
invalid_records
triage_processed
triage_escalated
deep_processed
deep_proposed_accept
judge_confirm
judge_review
judge_reject
review_queue_size
review_resolved
concept_create
concept_support
concept_refine
concept_contradict
oldest_unprocessed_candidate_age_seconds
backlog_by_stage: dict[str, int]
lease_retries
audit_duplicate_leakage
raw_to_training_bypass
raw_to_canonical_bypass
canonical_accepts
canonical_without_judge
simultaneous_valid_lease_conflicts
```

Functions:

```python
check_release_invariants(metrics: DailyMetrics) -> list[str]
audit_due(*, now: datetime, last_audit: DailyAuditRecord | None, timezone: ZoneInfo) -> date | None
```

Invariant failure keys:

```text
raw_to_training_bypass
raw_to_canonical_bypass
canonical_without_judge
simultaneous_valid_lease_conflicts
audit_duplicate_leakage
```

`audit_due` returns the previous local date when no `COMPLETED` or `BLOCKED` audit record exists for that date. Both completed and explicit blocked records satisfy the daily accounting invariant; a blocked record never counts as canonical success.

- [ ] **Step 1: Write RED tests**

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from basketball_miner.distill_v3.metrics import DailyMetrics, audit_due, check_release_invariants


def test_hard_release_invariants_are_machine_readable():
    metrics = DailyMetrics(
        date="2026-09-14",
        raw_to_training_bypass=1,
        raw_to_canonical_bypass=1,
        canonical_without_judge=1,
    )
    assert check_release_invariants(metrics) == [
        "raw_to_training_bypass",
        "raw_to_canonical_bypass",
        "canonical_without_judge",
    ]


def test_previous_seoul_day_is_due_without_audit_record():
    due = audit_due(
        now=datetime.fromisoformat("2026-09-14T01:10:00+09:00"),
        last_audit=None,
        timezone=ZoneInfo("Asia/Seoul"),
    )
    assert due.isoformat() == "2026-09-13"
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_v3_metrics_cli.py -q
```

- [ ] **Step 3: Implement deterministic invariant order and local-day logic**

Do not infer semantic counters from text. Functions consume explicit counts only.

- [ ] **Step 4: Verify**

```bash
python -m pytest tests/test_v3_metrics_cli.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/metrics.py tests/test_v3_metrics_cli.py
git commit -m "feat: add V3 metrics and audit cadence"
```

---

### Task 8: Integrate deterministic V3 dry-run CLI

**Files:**
- Create: `scripts/run_distill_v3.py`
- Modify: `src/basketball_miner/distill_v3/__init__.py`
- Modify: `tests/test_v3_metrics_cli.py`

**CLI:**

```text
--inbox-jsonl PATH   required
--state-dir PATH     required but not created in --dry-run
--batch-size INT     default 100, range 1..100
--dry-run            required by this core implementation
```

One-line JSON output keys:

```text
status
raw_records
valid_candidates
invalid_records
duplicates
terminal_processed
triage_candidates
triage_batches
would_write_state
```

`would_write_state` is `False` in dry-run. Non-dry-run exits through argparse with a message that persistent mode belongs to the orchestrator/storage integration plan; it must not silently write private state.

- [ ] **Step 1: Write RED subprocess test**

```python
import json
import subprocess
import sys


def test_v3_dry_run_is_repeatable_and_side_effect_free(tmp_path, v3_inbox_path):
    state_dir = tmp_path / "state"
    command = [
        sys.executable,
        "scripts/run_distill_v3.py",
        "--inbox-jsonl", str(v3_inbox_path),
        "--state-dir", str(state_dir),
        "--batch-size", "2",
        "--dry-run",
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert not state_dir.exists()
```

- [ ] **Step 2: Run RED**

```bash
python -m pytest tests/test_v3_metrics_cli.py::test_v3_dry_run_is_repeatable_and_side_effect_free -q
```

- [ ] **Step 3: Implement JSONL validation -> router -> batch creation**

Each nonblank line is parsed by `json.loads` then `CandidateRecord.model_validate`. Invalid rows increment `invalid_records` and are skipped. Valid new candidates are routed and exact duplicates counted. Triage batches use `priority_for` and `build_batches`.

- [ ] **Step 4: Verify focused regression**

```bash
python -m pytest tests/test_v3_metrics_cli.py tests/test_end_to_end_dry_run.py -q
```

- [ ] **Step 5: Commit**

```bash
git add scripts/run_distill_v3.py src/basketball_miner/distill_v3/__init__.py tests/test_v3_metrics_cli.py
git commit -m "feat: add Distillation V3 dry-run pipeline"
```

---

### Task 9: Lock public/private boundary and run full regression

**Files:**
- Modify: `tests/test_workflow_policy.py`
- Modify: `README.md`

- [ ] **Step 1: Add characterization policy test**

```python
def test_public_workflows_have_no_self_hosted_gpu_execution():
    combined = _workflow_text(TEST_WORKFLOW) + _workflow_text(MINE_WORKFLOW)
    assert "self-hosted" not in combined
    assert "cuda" not in combined.casefold()
    assert "formquant" not in combined.casefold()
```

- [ ] **Step 2: Run policy test**

```bash
python -m pytest tests/test_workflow_policy.py -q
```

Expected: PASS before implementation changes; this is a boundary characterization test, not a forced RED.

- [ ] **Step 3: Document exact V3 dry-run command**

README command:

```bash
python scripts/run_distill_v3.py \
  --inbox-jsonl tests/fixtures/v3/inbox.jsonl \
  --state-dir _v3_state \
  --batch-size 100 \
  --dry-run
```

State explicitly: no semantic ACCEPT, no canonical write, no private repo write, no GPU execution.

- [ ] **Step 4: Run full verification**

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

Expected: all tests PASS and ruff clean.

- [ ] **Step 5: Review scope diff**

```bash
git diff --stat work/miner-40x-throughput...HEAD
git diff work/miner-40x-throughput...HEAD -- src/basketball_miner/distill_v3 scripts/run_distill_v3.py tests README.md
```

Confirm no source adapter, Miner schedule, collection budget, or GPU runner path changed.

- [ ] **Step 6: Commit**

```bash
git add tests/test_workflow_policy.py README.md
git commit -m "docs: lock Distillation V3 core boundary"
```

---

## Completion Gate

V3 Core is complete only when evidence shows:

```text
[ ] Exact candidate/source/hash dedup is deterministic.
[ ] Existing V2 blobs/candidates seed idempotence state without modifying V2 files.
[ ] Current V2 REVIEW remains reviewable rather than becoming generic terminal data.
[ ] Queue order and batch IDs are deterministic.
[ ] One batch cannot have two active leases.
[ ] Expired leases are reclaimable with incremented attempts.
[ ] Triage cannot ACCEPT.
[ ] Deep and Review proposals cannot canonicalize directly.
[ ] Judge REVIEW/REJECT cannot canonicalize.
[ ] Immutable staging rejects changed overwrite.
[ ] CREATE/SUPPORT/REFINE/CONTRADICT are explicit and tested.
[ ] Raw-to-training bypass is zero.
[ ] Raw-to-canonical bypass is zero.
[ ] Previous-day audit cannot starve without a COMPLETED or BLOCKED daily record.
[ ] Public Miner has no GPU/self-hosted execution path.
[ ] Full pytest and ruff gates are green.
```

## Separate Follow-on Plans

After this completion gate passes, write and execute two separate Superpowers plans:

1. **Distillation V3 Orchestrator:** private hoopDB storage adapter, persistent queue/lease/ledger state, hourly ChatGPT scheduled-task contract, shadow mode, and canonical Daily Auditor integration.
2. **FormPath Private Compute:** private `Rudwpahs/formpath-compute`, RTX 4060 self-hosted runner, declarative GPU jobs/results, then Qwen/FormQuant/QLoRA integration.
