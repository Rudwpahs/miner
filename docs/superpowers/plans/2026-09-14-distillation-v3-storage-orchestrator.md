# Distillation V3 Storage + Orchestrator Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the verified Distillation V3 deterministic core to private `Rudwpahs/hoopDB` shadow-state storage and define a single hourly ChatGPT semantic Orchestrator without enabling canonical promotion until shadow-mode invariants pass.

**Architecture:** Add a small GitHub Contents API storage adapter to the public `miner` code, then add a V3 prepare pipeline that reads immutable Miner inbox batches plus V2 history and writes only V3 shadow queues/ledgers/metrics into private `hoopDB`. A separate, versioned Orchestrator prompt consumes those queues and produces immutable semantic staging records. The existing Daily Distillation automation remains enabled until this phase is merged, deployed, shadow state is successfully prepared, and the replacement automation can be updated atomically.

**Tech Stack:** Python 3.12, Pydantic >=2.11,<3, httpx >=0.28,<1, pytest >=8.4, ruff >=0.12, GitHub Contents API, GitHub Actions, ChatGPT scheduled task.

**Spec:** `docs/superpowers/specs/2026-09-14-distillation-v3-design.md`

## Global Constraints

- Preserve B-policy. Storage/preparation code never creates semantic ACCEPT decisions.
- Raw inbox data remains candidate-only and never becomes training/canonical data directly.
- `Rudwpahs/hoopDB` must pass the existing private-repository preflight before any V3 write.
- Public `Rudwpahs/miner` workflows must remain GitHub-hosted CPU only; no self-hosted/CUDA/FormQuant/QLoRA execution.
- V3 storage writes are restricted to `ml/coach/miner-data/v3/`; this phase does not write canonical `distilled/accepted`, `distilled/review`, or `distilled/manifests` paths.
- Existing V2 data is read-only historical input.
- Immutable run/queue files permit identical-byte retries and reject conflicting overwrites.
- Mutable singleton state (ledger, index, latest metrics) uses optimistic SHA-based updates; a stale SHA is a conflict, never a blind overwrite.
- The current `FormPath Daily Distillation` task is not modified until V3 shadow storage is operational after integration.
- No new runtime dependency is added.

---

## File Structure

Create/modify:

```text
src/basketball_miner/distill_v3/
  github_store.py        restricted private GitHub reader/writer
  prepare.py             V3 shadow-state preparation from inbox + V2 history
  paths.py               canonical V3 private-path builders/guards

scripts/run_distill_v3_prepare.py

docs/formpath-v3-orchestrator-prompt.md
.github/workflows/distill_v3_prepare.yml

tests/test_v3_paths.py
tests/test_v3_github_store.py
tests/test_v3_prepare.py
tests/test_v3_orchestrator_contract.py
```

Existing core modules remain authoritative for models, IDs, routing, queueing, ledger semantics, concept indexing, staging, audit validation, and metrics.

---

### Task 1: Restrictive V3 private path contract

**Files:**
- Create: `src/basketball_miner/distill_v3/paths.py`
- Create: `tests/test_v3_paths.py`

**Interfaces:**
- `V3_ROOT = "ml/coach/miner-data/v3"`
- `assert_v3_write_path(path: str) -> str`
- `queue_path(stage: str, batch_id: str) -> str`
- `lease_path(batch_id: str) -> str`
- `staging_path(stage: str, run_date: str, run_id: str) -> str`
- `ledger_path() -> str`
- `concept_index_path() -> str`
- `metrics_path(run_date: str) -> str`

- [ ] **Step 1: Write failing path tests**

```python
import pytest

from basketball_miner.distill_v3.paths import assert_v3_write_path, queue_path


def test_v3_write_guard_accepts_only_v3_root():
    assert assert_v3_write_path("ml/coach/miner-data/v3/ledgers/distill.json") == (
        "ml/coach/miner-data/v3/ledgers/distill.json"
    )
    with pytest.raises(ValueError):
        assert_v3_write_path("ml/coach/miner-data/distilled/accepted/2026/09/14.jsonl")


def test_queue_path_is_stage_scoped_and_rejects_traversal():
    assert queue_path("TRIAGE", "V3-TRIAGE-0123456789ab") == (
        "ml/coach/miner-data/v3/queues/triage/V3-TRIAGE-0123456789ab.json"
    )
    with pytest.raises(ValueError):
        queue_path("TRIAGE", "../escape")
```

- [ ] **Step 2: Run focused test and verify RED**

```bash
python -m pytest tests/test_v3_paths.py -q
```

Expected: import failure because `paths.py` does not exist.

- [ ] **Step 3: Implement explicit path builders**

Rules:
- normalize `/` separators only; reject leading slash, `..`, backslash, empty path component.
- queue stage must be one of `TRIAGE|DEEP|JUDGE|REVIEW|AUDIT`; directory is lowercase.
- queue batch ID must begin `V3-{STAGE}-` and match the existing 12-hex suffix convention.
- dates must match `YYYY-MM-DD`; storage path converts to `YYYY/MM/DD` only for staging/metrics path partitioning.
- every generated write path must call `assert_v3_write_path` before returning.

- [ ] **Step 4: Run focused tests and lint**

```bash
python -m pytest tests/test_v3_paths.py -q
python -m ruff check src/basketball_miner/distill_v3/paths.py tests/test_v3_paths.py
```

Expected: PASS / All checks passed.

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/paths.py tests/test_v3_paths.py
git commit -m "feat: define V3 private storage paths"
```

---

### Task 2: GitHub private-store reader and optimistic writer

**Files:**
- Create: `src/basketball_miner/distill_v3/github_store.py`
- Create: `tests/test_v3_github_store.py`

**Interfaces:**

```python
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
    def __init__(repo: str, branch: str, token: str, client: httpx.Client | None = None): ...
    def list_dir(path: str) -> list[RemoteEntry]: ...
    def read_file(path: str) -> RemoteFile | None: ...
    def create_immutable(path: str, content: bytes, message: str) -> str | None: ...
    def update_mutable(path: str, content: bytes, *, expected_sha: str | None, message: str) -> str | None: ...
```

- [ ] **Step 1: Write failing HTTP-mock tests**

Cover these exact behaviors:
- constructor rejects blank token or non-`owner/name` repo.
- `list_dir` GET decodes GitHub directory metadata and returns sorted entries.
- `read_file` returns `None` on 404, decodes base64 on 200, rejects non-file payload.
- `create_immutable` first GETs the path. If absent, PUT without `sha`; if present with identical bytes, return idempotently without PUT; if bytes differ, raise `FileExistsError`.
- `update_mutable` GETs current state. If file is absent, `expected_sha` must be `None`; create with no `sha`. If present, supplied `expected_sha` must exactly equal current SHA before PUT; PUT includes that SHA. Mismatch raises `RuntimeError("stale remote SHA")` before write.
- write methods call `assert_v3_write_path`; a canonical `distilled/...` target is rejected before any network write.
- authorization token never appears in raised errors.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest tests/test_v3_github_store.py -q
```

- [ ] **Step 3: Implement with existing HTTP conventions**

Use the same API headers and redacted status-class errors as `basketball_miner.export`. Use `follow_redirects=False`, base64 Contents API payloads, and `X-GitHub-Api-Version: 2022-11-28`. Do not add retries in this phase; surface conflict/rate-limit status without leaking body/token.

- [ ] **Step 4: Run focused + existing export security tests**

```bash
python -m pytest tests/test_v3_github_store.py tests/test_export.py -q
python -m ruff check src/basketball_miner/distill_v3/github_store.py tests/test_v3_github_store.py
```

Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/github_store.py tests/test_v3_github_store.py
git commit -m "feat: add private V3 GitHub store"
```

---

### Task 3: Shadow-state preparer

**Files:**
- Create: `src/basketball_miner/distill_v3/prepare.py`
- Create: `tests/test_v3_prepare.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class InboxBlob:
    path: str
    sha: str
    candidates: tuple[CandidateRecord, ...]

@dataclass(frozen=True)
class ShadowPreparation:
    ledger: DistillLedger
    concept_index: tuple[ConceptIndexRecord, ...]
    triage_batches: tuple[BatchRecord, ...]
    metrics: DailyMetrics
    processed_blob_shas: tuple[str, ...]

prepare_shadow(
    *,
    inbox_blobs: list[InboxBlob],
    existing_ledger: DistillLedger | None,
    accepted_rows: list[dict],
    review_rows: list[dict],
    manifests: list[dict],
    run_date: str,
    created_at: str,
    triage_batch_size: int = 100,
) -> ShadowPreparation
```

- [ ] **Step 1: Write failing pure-function tests**

Required tests:
1. V2 history seeds terminal/review state before routing new inbox candidates.
2. already-processed blob SHA is skipped completely on rerun.
3. new blob exact-source/canonical duplicates become terminal duplicates; only unresolved candidates enter TRIAGE.
4. every newly consumed blob is added to returned ledger only after all rows in that blob validate; malformed row makes that blob absent from `processed_blob_shas` and increments `invalid_records` without partially routing its valid rows.
5. TRIAGE batches are deterministic and max 100.
6. concept index contains V2 accepted + active V2 review records and does not mutate inputs.
7. returned `DailyMetrics.raw_to_training_bypass`, `raw_to_canonical_bypass`, `canonical_without_judge`, `simultaneous_valid_lease_conflicts`, and `audit_duplicate_leakage` are zero.
8. rerunning with the returned ledger and same blobs produces zero new TRIAGE batches.

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_prepare.py -q
```

- [ ] **Step 3: Implement by composing existing Core only**

Do not duplicate routing/ID/queue logic. Use `seed_from_v2_history`, `route_candidate`, `record_route`, `build_batches`, `build_index`, and `DailyMetrics`. `prepare_shadow` performs no HTTP and no filesystem write.

- [ ] **Step 4: Run focused + Core regression**

```bash
python -m pytest tests/test_v3_prepare.py tests/test_v3_ledger_router.py tests/test_v3_queue.py tests/test_v3_concept_index.py -q
python -m ruff check src/basketball_miner/distill_v3/prepare.py tests/test_v3_prepare.py
```

- [ ] **Step 5: Commit**

```bash
git add src/basketball_miner/distill_v3/prepare.py tests/test_v3_prepare.py
git commit -m "feat: prepare V3 shadow state"
```

---

### Task 4: Remote shadow-preparation CLI

**Files:**
- Create: `scripts/run_distill_v3_prepare.py`
- Extend: `tests/test_v3_prepare.py`

**CLI:**

```text
--repo (default Rudwpahs/hoopDB)
--branch (default main)
--run-date YYYY-MM-DD
--batch-size 1..100 (default 100)
--dry-run
--write-shadow
```

Exactly one of `--dry-run` or `--write-shadow` is required.

Environment:
- `HOOPHUB_MINER_TOKEN` required only for remote access; no token is printed.

- [ ] **Step 1: Write failing CLI/store-integration tests with MockTransport**

Test helper-level functions rather than real GitHub network. Required behavior:
- recursive discovery under `ml/coach/miner-data/inbox/YYYY/MM/DD/` returns only `.jsonl` files.
- historical readers ingest existing `distilled/accepted`, `distilled/review`, `distilled/manifests` JSON/JSONL without writing them.
- dry-run returns JSON summary and performs no PUT.
- write-shadow persists only:
  - `v3/ledgers/distill.json`
  - `v3/concepts/concept_index.jsonl`
  - `v3/queues/triage/<batch>.json` for new immutable batches
  - `v3/metrics/YYYY/MM/DD.json`
- queue collision with different bytes aborts; it is not renamed or overwritten.
- mutable ledger/index/metrics updates use observed SHA.

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_prepare.py -q
```

- [ ] **Step 3: Implement orchestration around `GitHubV3Store`**

Private-repo preflight must run before reading/writing. Write summary fields: `new_inbox_blobs`, `new_candidates`, `duplicates`, `triage_candidates`, `triage_batches`, `review_seeded`, `invariant_failures`, `write_enabled`. If any hard invariant fails, exit nonzero and perform no writes.

- [ ] **Step 4: Run focused/full tests and lint**

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

- [ ] **Step 5: Commit**

```bash
git add scripts/run_distill_v3_prepare.py tests/test_v3_prepare.py
git commit -m "feat: add V3 shadow preparation CLI"
```

---

### Task 5: Public hourly V3 shadow-preparation workflow

**Files:**
- Create: `.github/workflows/distill_v3_prepare.yml`
- Modify: `tests/test_workflow_policy.py`

**Workflow contract:**
- schedule `42 * * * *` UTC, plus manual `workflow_dispatch` with `write_shadow` boolean default false.
- `concurrency.group: formpath-distillation-v3-prepare`, `cancel-in-progress: false`.
- GitHub-hosted `ubuntu-latest` only.
- checkout `main`, `persist-credentials: false`.
- Python 3.12.
- install package.
- scheduled runs use `--write-shadow`; manual runs default to `--dry-run` unless explicit `write_shadow=true`.
- token is passed only as `HOOPHUB_MINER_TOKEN` environment variable.
- target repo fixed to `Rudwpahs/hoopDB` and branch `main`.
- timeout 15 minutes.
- external actions pinned to 40-char SHAs.

- [ ] **Step 1: Extend workflow policy tests first**

Require:
- exact cron `42 * * * *`.
- no `pull_request` or `pull_request_target` in V3 write workflow.
- no `self-hosted`, CUDA, FormQuant, QLoRA.
- no secret echo/printf.
- only `contents: read` permission in public repo; private target mutation occurs through the scoped token, not repo workflow permission.
- workflow calls `scripts/run_distill_v3_prepare.py` and contains both `--dry-run` and `--write-shadow` branches.

- [ ] **Step 2: Run policy test and verify RED**

```bash
python -m pytest tests/test_workflow_policy.py -q
```

- [ ] **Step 3: Add workflow exactly to contract**

Do not edit `mine.yml` schedule/budget.

- [ ] **Step 4: Full regression**

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/distill_v3_prepare.yml tests/test_workflow_policy.py
git commit -m "ops: add V3 shadow preparation workflow"
```

---

### Task 6: Versioned single-Orchestrator semantic contract

**Files:**
- Create: `docs/formpath-v3-orchestrator-prompt.md`
- Create: `tests/test_v3_orchestrator_contract.py`

**Orchestrator execution contract:**
- schedule target after deployment: hourly, one scheduled ChatGPT task.
- before doing semantic work, read V3 ledger/queues and the most recent audit record.
- if previous Asia/Seoul day lacks `COMPLETED` or `BLOCKED` audit, Audit has absolute priority.
- otherwise role order: REVIEW > JUDGE > DEEP > TRIAGE > manual AUDIT > no-op.
- claim exactly one eligible batch per run in V3 initial shadow mode.
- semantic outputs are written only to `v3/staging/<stage>/YYYY/MM/DD/<run-id>.jsonl` and lease/run records under V3 root.
- Triage never ACCEPTs; Deep never canonicalizes; Judge CONFIRM remains staging-only in shadow mode.
- no writes to canonical `distilled/accepted`, `distilled/review`, or `distilled/manifests` during shadow phase.
- inaccessible source/evidence is REVIEW/BLOCKED, never invented.
- all writes are idempotent/immutable; conflicting existing bytes are a blocked run.
- end summary reports batch ID, stage, processed count, decision counts, backlog snapshot, and exact blocking prerequisite when blocked.

- [ ] **Step 1: Write contract tests against the prompt file**

The test reads Markdown text and requires literal presence of:
`SHADOW MODE`, `REVIEW > JUDGE > DEEP > TRIAGE`, `Triage never ACCEPTs`, `Judge CONFIRM`, `no canonical promotion`, the V3 staging root, Asia/Seoul previous-day audit priority, and explicit repository `Rudwpahs/hoopDB`.
It also asserts the prompt does not instruct direct raw-to-training use.

- [ ] **Step 2: Verify RED**

```bash
python -m pytest tests/test_v3_orchestrator_contract.py -q
```

- [ ] **Step 3: Write the complete reusable prompt**

The prompt must be self-contained because scheduled executions do not rely on hidden prior-chat reasoning. It includes exact stage decision schemas and path rules from the approved spec/Core models.

- [ ] **Step 4: Run contract/full regression**

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

- [ ] **Step 5: Commit**

```bash
git add docs/formpath-v3-orchestrator-prompt.md tests/test_v3_orchestrator_contract.py
git commit -m "docs: define V3 semantic orchestrator contract"
```

---

### Task 7: Deployment gate; do not modify automation prematurely

**No production mutation is performed as part of branch coding.** After Tasks 1-6 are green:

- [ ] **Step 1: Fresh verification**

Require CI at branch HEAD with full `pytest -q` and `ruff check src tests scripts` success.

- [ ] **Step 2: Compare against the 40x base**

Verify the branch is ahead-only, `mine.yml` retains the 20,000 budget and 3-hour schedule, and the only new workflow is V3 prepare.

- [ ] **Step 3: Integration choice under `finishing-a-development-branch`**

Do not move `main` or update the live ChatGPT automation without the user's explicit integration choice.

- [ ] **Step 4: After integration only, run a manual V3 dry-run and then one write-shadow preparation**

Expected private paths must appear under `ml/coach/miner-data/v3/`; canonical V2 paths remain unchanged.

- [ ] **Step 5: Only after shadow preparation succeeds, update existing automation ID `6aa16a7dcf288191864db5713735c93b`**

Change title to `FormPath V3 Orchestrator`, cadence to hourly, and prompt to the exact versioned contract. Do not create a second duplicate distillation task. Keep notifications unchanged unless the user asks otherwise.

## Definition of Done

Phase 2 coding is ready for integration when: all tests/lint pass; private writes are structurally confined to V3 root; optimistic SHA conflicts and immutable collisions fail closed; shadow preparation is deterministic/idempotent; the public workflow remains CPU-only and does not modify Miner collection cadence; the Orchestrator prompt is versioned/tested and enforces shadow-only semantic staging; and the live daily automation has not been changed before the post-merge shadow gate succeeds.
