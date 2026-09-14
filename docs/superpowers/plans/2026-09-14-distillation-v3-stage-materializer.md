# Distillation V3 Stage Materializer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic Stage Materializer that validates immutable GPT staging output, advances V3 candidate state into DEEP/JUDGE/REVIEW/AUDIT/terminal states, and integrates that advancement before new inbox preparation in the hourly shadow workflow.

**Architecture:** Extend the existing strict Pydantic V3 state model and ledger with replay guards and source-type persistence. Add one pure `materialize_staging()` core with no I/O, then layer remote GitHub discovery/persistence on top using the existing `GitHubPrivateRepoStore`. The existing hourly prepare workflow becomes a deterministic two-phase cycle: materialize completed semantic staging first, then prepare newly arrived inbox candidates.

**Tech Stack:** Python 3.12, Pydantic 2.x, httpx, pytest, Ruff, GitHub Contents API through the existing V3 store abstraction, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-14-distillation-v3-stage-materializer-design.md`

## Global Constraints

- Branch remains `work/distillation-v3`; do not touch `main` during implementation.
- No new third-party runtime dependency.
- Miner `mine.yml` must remain `cron: "17 */3 * * *"` with exactly two `--budget 20000` occurrences.
- Public workflows must not use `self-hosted`, CUDA, FormQuant, or local GPU execution.
- Materializer writes only under `ml/coach/miner-data/v3/`.
- No materializer code may write V2 canonical `distilled/accepted`, `distilled/review`, or `distilled/manifests` paths.
- Semantic decisions remain GPT-owned; code only validates and deterministically advances states.
- Triage never ACCEPTs; Deep never canonicalizes; Judge CONFIRM creates AUDIT work only in SHADOW MODE.
- Staging blobs are atomic: one malformed/conflicting row blocks the whole blob.
- Queue files are immutable. Same-byte replay is idempotent; different-byte collision is a hard failure.
- Mutable ledger/metrics writes use observed SHA optimistic updates only.
- Completed batches must never become eligible again only because a lease expires.
- REVIEW -> REVIEW/BLOCKED parks the candidate and does not immediately create another REVIEW queue.

---

## File Map

**Create**
- `src/basketball_miner/distill_v3/materialize.py` — pure stage advancement plus remote staging orchestration helpers.
- `tests/test_v3_materialize.py` — pure transition, validation, atomicity, determinism, and idempotency tests.

**Modify**
- `src/basketball_miner/distill_v3/models.py` — persist `source_type` in candidate state and add strict `ShadowAuditResult`.
- `src/basketball_miner/distill_v3/ledger.py` — replay guards, parked reviews, serialization, source-type preservation.
- `src/basketball_miner/distill_v3/metrics.py` — materializer counters and hard invariant fields.
- `src/basketball_miner/distill_v3/prepare.py` — call remote materialization before inbox preparation and share one ledger safely.
- `scripts/run_distill_v3_prepare.py` — expose two-phase shadow dry-run/write summary without widening write scope.
- `docs/formpath-v3-orchestrator-prompt.md` — completed-batch eligibility and staging envelope contract.
- `tests/test_v3_prepare.py` — backward-compatible source-type state creation.
- `tests/test_v3_prepare_remote.py` — remote materialization ordering and collision/optimistic-SHA behavior.
- `tests/test_v3_orchestrator_contract.py` — completed-batch exclusion and staging contract.
- `tests/test_workflow_policy.py` — materialize-before-prepare policy plus existing 40x/GPU safety rules.

---

### Task 1: Extend V3 State Models and Ledger Replay Guards

**Files:**
- Modify: `src/basketball_miner/distill_v3/models.py`
- Modify: `src/basketball_miner/distill_v3/ledger.py`
- Modify: `src/basketball_miner/distill_v3/metrics.py`
- Modify: `tests/test_v3_models.py`
- Modify: `tests/test_v3_ledger_router.py`
- Modify: `tests/test_v3_metrics_cli.py`
- Modify: `tests/test_v3_prepare.py`

**Interfaces:**
- Existing `CandidateStageState` gains `source_type: Literal["official", "academic", "coaching", "interview"] | None = None` for backward-compatible V2/V3 deserialization.
- Add `ShadowAuditResult` with strict `candidate_id`, `stage="AUDIT"`, `decision`, `reason_code`, `knowledge_unit_id`, `concept_id`, and `concept_action` validation.
- `DistillLedger` gains `processed_staging_shas`, `completed_batch_ids`, and `parked_review_candidate_ids` sets.
- `ledger_payload()` serializes all new sets in sorted order.
- `DailyMetrics` gains materializer counters plus `illegal_stage_transition`, `staging_candidate_set_mismatch`, and `staging_fingerprint_mismatch`; these three become release-invariant fields.

- [ ] **Step 1: Write failing model tests**

Add tests asserting:

```python
def test_candidate_state_source_type_is_optional_for_backward_compatibility():
    legacy = CandidateStageState(
        candidate_id="CAND-1111111111111111",
        source_fingerprint="1" * 64,
        stage="TRIAGE",
        status="PENDING",
        updated_at="2026-09-14T00:00:00Z",
    )
    assert legacy.source_type is None


def test_shadow_audit_result_accepts_only_shadow_audit_decisions():
    result = ShadowAuditResult(
        candidate_id="CAND-1111111111111111",
        stage="AUDIT",
        decision="SUPPORT",
        reason_code="SUPPORTED_EXISTING_CONCEPT",
        knowledge_unit_id="KU-1",
        concept_id="CONCEPT-111111111111",
        concept_action="SUPPORT",
    )
    assert result.stage == "AUDIT"
    with pytest.raises(ValidationError):
        ShadowAuditResult(
            candidate_id="CAND-1111111111111111",
            stage="AUDIT",
            decision="CONFIRM",
            reason_code="ILLEGAL",
        )
```

Allowed AUDIT decisions are exactly `CREATE | SUPPORT | REFINE | CONTRADICT | REVIEW | BLOCKED`. For CREATE/SUPPORT/REFINE/CONTRADICT require `knowledge_unit_id`, `concept_id`, and matching `concept_action`; REVIEW/BLOCKED may omit those fields.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_models.py
```

Expected: FAIL because `source_type` and `ShadowAuditResult` do not exist yet.

- [ ] **Step 3: Implement minimal model changes**

Use a strict source type alias:

```python
SourceType = Literal["official", "academic", "coaching", "interview"]
```

Add `source_type: SourceType | None = None` to `CandidateStageState`. Implement `ShadowAuditResult` with `extra="forbid"` and a model validator enforcing the decision/metadata requirements above.

- [ ] **Step 4: Write failing ledger replay-guard tests**

Add:

```python
def test_ledger_serializes_materializer_replay_guards_deterministically():
    ledger = DistillLedger(
        processed_staging_shas={"b" * 40, "a" * 40},
        completed_batch_ids={"V3-TRIAGE-111111111111"},
        parked_review_candidate_ids={"CAND-1111111111111111"},
    )
    payload = ledger_payload(ledger)
    assert payload["processed_staging_shas"] == ["a" * 40, "b" * 40]
    assert payload["completed_batch_ids"] == ["V3-TRIAGE-111111111111"]
    assert payload["parked_review_candidate_ids"] == ["CAND-1111111111111111"]
```

Also update the existing `record_route()` expectation so a new candidate state stores `candidate.source_type`.

- [ ] **Step 5: Run ledger tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_ledger_router.py tests/test_v3_prepare.py
```

Expected: FAIL for missing ledger fields/source type persistence.

- [ ] **Step 6: Implement minimal ledger changes**

Add the three sets with `default_factory=set`; serialize them sorted. In `record_route()` pass `source_type=candidate.source_type` into `CandidateStageState`.

- [ ] **Step 7: Write failing metrics tests**

Add a test creating `DailyMetrics(illegal_stage_transition=1)` and assert `check_release_invariants()` includes `illegal_stage_transition`; repeat for candidate-set and fingerprint mismatch. Add nonnegative validation expectations for all new counters.

Required new counters:

```text
staging_files_seen
staging_files_materialized
staging_files_invalid
batches_completed
candidates_advanced
terminalized_candidates
parked_review_candidates
illegal_stage_transition
staging_candidate_set_mismatch
staging_fingerprint_mismatch
```

`next_batches_created_by_stage` is `dict[str, int]` with the same nonnegative-value validator rule as `backlog_by_stage`.

- [ ] **Step 8: Run metrics tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_metrics_cli.py
```

Expected: FAIL for missing fields/invariant registration.

- [ ] **Step 9: Implement metrics additions**

Add all counters with `ge=0`, validate `next_batches_created_by_stage`, and append the three new hard invariant fields to `_INVARIANT_FIELDS`.

- [ ] **Step 10: Run Task 1 test set and lint**

Run:

```bash
python -m pytest -q tests/test_v3_models.py tests/test_v3_ledger_router.py tests/test_v3_prepare.py tests/test_v3_metrics_cli.py
python -m ruff check src tests scripts
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/basketball_miner/distill_v3/models.py src/basketball_miner/distill_v3/ledger.py src/basketball_miner/distill_v3/metrics.py tests/test_v3_models.py tests/test_v3_ledger_router.py tests/test_v3_prepare.py tests/test_v3_metrics_cli.py
git commit -m "feat: extend V3 state for stage materialization"
```

---

### Task 2: Build the Pure Atomic Stage Materializer

**Files:**
- Create: `src/basketball_miner/distill_v3/materialize.py`
- Create: `tests/test_v3_materialize.py`

**Interfaces:**

Create:

```python
@dataclass(frozen=True)
class StagingBlob:
    path: str
    sha: str
    payload: dict[str, object]

@dataclass(frozen=True)
class StageMaterialization:
    ledger: DistillLedger
    next_batches: tuple[BatchRecord, ...]
    processed_staging_shas: tuple[str, ...]
    completed_batch_ids: tuple[str, ...]
    metrics: DailyMetrics

materialize_staging(
    *,
    staging_blobs: list[StagingBlob],
    source_batches: dict[str, BatchRecord],
    existing_ledger: DistillLedger,
    run_date: str,
    created_at: str,
) -> StageMaterialization
```

Staging payload shape:

```python
{
    "run_id": "RUN-...",
    "batch_id": "V3-TRIAGE-...",
    "stage": "TRIAGE",
    "worker": "GPT-V3",
    "created_at": "2026-09-14T00:00:00Z",
    "input_fingerprints": ["..."],
    "records": [SemanticResult-like dicts],
}
```

For AUDIT records validate using `ShadowAuditResult`; all other stages use `SemanticResult`.

Destination batch sizes:

```python
_STAGE_BATCH_SIZE = {
    "DEEP": 30,
    "JUDGE": 30,
    "REVIEW": 20,
    "AUDIT": 30,
}
```

Priority is always recomputed with existing `priority_for(state.source_type, destination_stage, is_review=destination_stage == "REVIEW")`. A missing `source_type` on a candidate that must advance is a hard conflict; legacy states that never advance remain readable.

- [ ] **Step 1: Write transition tests first**

Cover all transition cases in separate tests:

```text
TRIAGE: REJECT/DUPLICATE -> terminal; DEEP_PENDING -> DEEP
DEEP: PROPOSE_ACCEPT -> JUDGE; REVIEW -> REVIEW; REJECT -> terminal
JUDGE: CONFIRM -> AUDIT; REVIEW -> REVIEW; REJECT -> terminal
REVIEW: PROPOSE_ACCEPT -> JUDGE; REVIEW/BLOCKED -> parked; REJECT -> terminal
AUDIT: CREATE/SUPPORT/REFINE/CONTRADICT -> AUDIT/COMPLETE with no next queue;
       REVIEW/BLOCKED -> parked with no canonical write
```

Assert `Judge CONFIRM` produces only an AUDIT batch and never an `AuditPromotion` or canonical path.

- [ ] **Step 2: Run transition tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_materialize.py -k "transition or audit"
```

Expected: import failure because `materialize.py` does not exist.

- [ ] **Step 3: Implement stage-transition mapping minimally**

Use explicit dictionaries/branches; do not infer or accept unknown decisions. Mutate only a deep copy of the incoming ledger. Terminal decisions add the candidate to `terminal_candidate_ids`; REVIEW/BLOCKED add it to both `review_candidate_ids` and `parked_review_candidate_ids`; advancing decisions remove stale review/parked membership and set the destination state to `PENDING`.

- [ ] **Step 4: Write atomic validation tests**

Tests must assert the entire blob is rejected without partial ledger changes for:

```text
missing candidate
extra candidate
candidate-set duplicate
fingerprint mismatch
staging stage != source batch stage
record stage != staging stage
illegal decision
malformed one-row record
unknown source batch
completed batch with non-idempotent new staging SHA
```

Snapshot `existing_ledger.model_dump(mode="json")` before the call and assert it is unchanged after every exception.

- [ ] **Step 5: Run validation tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_materialize.py -k "mismatch or malformed or illegal or unknown"
```

Expected: FAIL until validation is implemented.

- [ ] **Step 6: Implement run-level validation before transition mutation**

Validate all rows and mappings into temporary validated structures first. Candidate matching is by ID, while `input_fingerprints` must correspond exactly to the source batch's candidate/fingerprint mapping. Only after the entire blob validates should a temporary ledger be advanced.

- [ ] **Step 7: Write idempotency and completion tests**

Assert:

```python
# Already processed SHA: skipped, no next batches.
# completed_batch_ids + same processed SHA: idempotent skip.
# completed_batch_ids + different unprocessed staging SHA for same batch: ValueError.
# Successful materialization adds both staging SHA and batch ID.
```

Also assert completed source batch candidate states never return to `PENDING` merely due to lease state.

- [ ] **Step 8: Implement replay guards**

Check `processed_staging_shas` before parsing the blob. Check `completed_batch_ids` after resolving `batch_id`; reject a second distinct staging blob for a completed batch.

- [ ] **Step 9: Write deterministic batching tests**

Feed the same staging blobs in reversed discovery order and assert identical `next_batches` and ledger payload. Verify sizes `[30, 30, ...]` for DEEP/JUDGE/AUDIT and `[20, 20, ...]` for REVIEW. Verify stable batch IDs.

- [ ] **Step 10: Implement deterministic aggregation/batching**

Collect destination members across all successfully processed staging blobs, recompute priority from persisted `source_type`, sort via existing `build_batches()`, then assign each advanced candidate's `batch_id` to the created next batch.

- [ ] **Step 11: Run Task 2 tests and lint**

Run:

```bash
python -m pytest -q tests/test_v3_materialize.py
python -m ruff check src tests scripts
```

Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add src/basketball_miner/distill_v3/materialize.py tests/test_v3_materialize.py
git commit -m "feat: materialize V3 semantic stage transitions"
```

---

### Task 3: Add Remote Discovery and Recoverable Persistence

**Files:**
- Modify: `src/basketball_miner/distill_v3/materialize.py`
- Modify: `src/basketball_miner/distill_v3/prepare.py`
- Modify: `tests/test_v3_prepare_remote.py`
- Modify: `tests/test_v3_github_store.py` only if an existing store behavior needs an explicit regression test; do not widen the store API without a failing test.

**Interfaces:**

Add to `materialize.py`:

```python
discover_staging_blobs(store) -> list[StagingBlob]
load_source_batches(store, staging_blobs: list[StagingBlob]) -> dict[str, BatchRecord]
run_remote_materialization(
    *,
    store,
    run_date: str,
    created_at: str,
    write_shadow: bool,
) -> dict[str, object]
```

`run_remote_materialization()` reads the existing ledger with its observed SHA, computes pure materialization, checks release invariants, preflights every next queue destination, writes missing immutable next queues, then optimistic-SHA updates ledger and metrics.

- [ ] **Step 1: Write remote dry-run tests**

Build a `MemoryStore` fixture with one source TRIAGE queue and one matching staging file. Assert:

```text
write_shadow=False -> zero writes
summary reports staging_files_seen=1
summary reports staging_files_materialized=1
summary reports DEEP next batch count=1
```

- [ ] **Step 2: Run dry-run test and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_prepare_remote.py -k materialization
```

Expected: FAIL because remote materialization helpers do not exist.

- [ ] **Step 3: Implement recursive staging/source-batch loading**

Reuse `discover_remote_files()` for `ml/coach/miner-data/v3/staging` `.jsonl` files. Parse each staging file as exactly one JSON object envelope or, if the approved staging writer uses one envelope line in JSONL, require exactly one nonblank object line. Fetch the referenced queue path with `queue_path(stage, batch_id)` and validate it with `BatchRecord.model_validate()`.

- [ ] **Step 4: Write persistence ordering tests**

Assert:

1. different-byte immutable next-queue collision causes zero writes;
2. same-byte pre-existing queue is idempotent and ledger can still advance;
3. ledger update receives the observed ledger SHA;
4. stale ledger SHA aborts rather than blind-overwriting;
5. no write path is outside `ml/coach/miner-data/v3/`;
6. invariant failure produces zero PUTs.

- [ ] **Step 5: Run persistence tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_prepare_remote.py -k "collision or observed or stale or invariant"
```

Expected: FAIL until persistence behavior exists.

- [ ] **Step 6: Implement preflight-then-write behavior**

Before any write, serialize every next `BatchRecord` exactly as the current queue writer format and read every destination. Abort if any existing file differs. Then `create_immutable()` missing/same-byte queues, then `update_mutable()` ledger and metrics using observed SHAs.

- [ ] **Step 7: Run Task 3 tests and lint**

Run:

```bash
python -m pytest -q tests/test_v3_prepare_remote.py tests/test_v3_github_store.py
python -m ruff check src tests scripts
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/basketball_miner/distill_v3/materialize.py src/basketball_miner/distill_v3/prepare.py tests/test_v3_prepare_remote.py tests/test_v3_github_store.py
git commit -m "feat: persist V3 stage materialization safely"
```

---

### Task 4: Integrate Materialize-Before-Prepare Hourly Cycle

**Files:**
- Modify: `src/basketball_miner/distill_v3/prepare.py`
- Modify: `scripts/run_distill_v3_prepare.py`
- Modify: `tests/test_v3_prepare_remote.py`
- Modify: `tests/test_workflow_policy.py`
- Modify: `.github/workflows/distill_v3_prepare.yml` only if the script invocation/summary contract requires it.

**Interfaces:**

Expose one top-level deterministic remote cycle:

```python
run_remote_v3_cycle(
    *,
    store,
    run_date: str,
    created_at: str,
    batch_size: int,
    write_shadow: bool,
) -> dict[str, object]
```

Order is fixed:

```text
A. materialize staging
B. re-read the latest ledger after materialization when writes are enabled
C. prepare new inbox into TRIAGE using that ledger
D. return combined machine-readable summary
```

For `write_shadow=False`, both phases compute against in-memory state without writes; Phase B passes the materializer-returned ledger directly into prepare so dry-run semantics match a successful write cycle.

- [ ] **Step 1: Write ordering test**

Construct a candidate currently in TRIAGE plus a staging decision that advances it to DEEP, while the same candidate appears in inbox. Assert the combined cycle first materializes it and then preparation does not generate a duplicate TRIAGE batch.

- [ ] **Step 2: Run ordering test and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_prepare_remote.py -k cycle
```

Expected: FAIL because combined cycle does not exist.

- [ ] **Step 3: Implement combined cycle**

Do not duplicate semantic transition logic in `prepare.py`; call `run_remote_materialization()`/pure materializer and existing preparation helpers. Preserve all current private-repo preflight behavior.

- [ ] **Step 4: Write workflow-policy test**

Update `tests/test_workflow_policy.py` to assert the V3 workflow invokes only `scripts/run_distill_v3_prepare.py` and that the script source contains/uses the combined `run_remote_v3_cycle` entry point. Keep all existing assertions for hourly cron, pinned actions, no PR trigger, no self-hosted/GPU, and 40x Miner preservation.

- [ ] **Step 5: Update CLI minimally**

The script prints one JSON summary with `materialize` and `prepare` sections plus `write_enabled`. `--write-shadow` remains opt-in for manual runs; scheduled workflow continues to pass it explicitly.

- [ ] **Step 6: Run Task 4 tests and lint**

Run:

```bash
python -m pytest -q tests/test_v3_prepare_remote.py tests/test_workflow_policy.py
python -m ruff check src tests scripts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/basketball_miner/distill_v3/prepare.py scripts/run_distill_v3_prepare.py tests/test_v3_prepare_remote.py tests/test_workflow_policy.py .github/workflows/distill_v3_prepare.yml
git commit -m "feat: run V3 materialization before inbox preparation"
```

---

### Task 5: Tighten the GPT Orchestrator Completion Contract

**Files:**
- Modify: `docs/formpath-v3-orchestrator-prompt.md`
- Modify: `tests/test_v3_orchestrator_contract.py`

**Interfaces:**

Batch eligibility becomes exactly:

```text
batch.status == PENDING
AND batch_id not in ledger.completed_batch_ids
AND no active lease exists for batch_id
```

GPT owns only one current batch and writes one immutable staging envelope. It must never create next-stage queue files or mutate the ledger. The materializer owns all next-queue creation.

- [ ] **Step 1: Write failing contract tests**

Assert prompt text includes all three eligibility clauses, states `exactly one eligible batch`, explicitly forbids next queue creation, and names the Stage Materializer as sole next-queue owner.

Also assert the staging envelope fields:

```text
run_id
batch_id
stage
worker
created_at
input_fingerprints
records
```

and the atomic rule that every candidate in the source batch must appear exactly once.

- [ ] **Step 2: Run prompt tests and verify RED**

Run:

```bash
python -m pytest -q tests/test_v3_orchestrator_contract.py
```

Expected: FAIL on the newly required completion/materializer clauses.

- [ ] **Step 3: Update prompt contract**

Keep existing REVIEW > JUDGE > DEEP > TRIAGE scheduling and previous-day Seoul Audit priority. Add completed-batch exclusion and staging-envelope requirements without granting GPT any deterministic queue/ledger mutation authority.

- [ ] **Step 4: Run Task 5 tests and lint**

Run:

```bash
python -m pytest -q tests/test_v3_orchestrator_contract.py
python -m ruff check src tests scripts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/formpath-v3-orchestrator-prompt.md tests/test_v3_orchestrator_contract.py
git commit -m "docs: bind V3 orchestrator to materializer state"
```

---

### Task 6: Full Regression, Spec Review, and Branch Verification

**Files:**
- No production feature additions unless a failing regression exposes a defect.
- Review all files changed since `df22a25cac138654e5ef39c1e31c7312b44e95e5`.

**Interfaces:**
- Final branch HEAD must satisfy the approved Materializer spec and all parent V3 invariants.

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run full lint**

```bash
python -m ruff check src tests scripts
```

Expected: `All checks passed!`

- [ ] **Step 3: Verify workflow policy explicitly**

Check:

```text
mine.yml cron == "17 */3 * * *"
--budget 20000 appears exactly twice
V3 shadow cron == "42 * * * *"
no public workflow contains self-hosted/cuda/formquant
```

- [ ] **Step 4: Compare branch to approved 40x base**

Run the equivalent of:

```bash
git diff --check df22a25cac138654e5ef39c1e31c7312b44e95e5...HEAD
git log --oneline df22a25cac138654e5ef39c1e31c7312b44e95e5..HEAD
```

Using connector equivalents where local git is unavailable. Expected: branch is ahead-only, behind 0.

- [ ] **Step 5: Search for forbidden canonical writes**

Review new/modified production code for `distilled/accepted`, `distilled/review`, and `distilled/manifests`. Documentation/tests may mention them only as forbidden/read-only paths; production materializer write calls must not target them.

- [ ] **Step 6: Review implementation against spec**

Confirm every TDD requirement from `2026-09-14-distillation-v3-stage-materializer-design.md` is covered by a passing test. If a requirement lacks coverage, add the failing test first, then the minimal implementation fix.

- [ ] **Step 7: Commit only if review required corrections**

Use a narrow message such as:

```bash
git commit -m "fix: close V3 materializer verification gaps"
```

- [ ] **Step 8: Invoke `superpowers:verification-before-completion`**

Use fresh CI evidence at final HEAD before claiming implementation complete.

- [ ] **Step 9: Invoke `superpowers:finishing-a-development-branch`**

Do not merge automatically. Present the integration choices required by the skill after all verification passes.
