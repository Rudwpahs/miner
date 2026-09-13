# Distillation V3 Stage Materializer Design

Date: 2026-09-14
Status: Approved architecture, awaiting user spec review
Branch: `work/distillation-v3`
Parent specs:
- `docs/superpowers/specs/2026-09-14-distillation-v3-design.md`
- `docs/superpowers/plans/2026-09-14-distillation-v3-storage-orchestrator.md`

## Purpose

The Stage Materializer closes the operational gap between immutable ChatGPT semantic staging outputs and the next deterministic Distillation V3 queue.

Without this component, V3 can prepare TRIAGE work and the Orchestrator can write TRIAGE staging records, but no deterministic component advances those staging decisions into DEEP, JUDGE, REVIEW, AUDIT, or terminal state. The materializer therefore owns **state advancement only**. It never makes semantic judgments and never weakens B-policy.

The governing rule remains:

> deterministic work is code; semantic judgment is GPT.

## Scope

This subsystem will:

1. discover unprocessed V3 staging files in private `Rudwpahs/hoopDB`;
2. validate every staging file against its source queue batch, candidate fingerprints, stage, and allowed decision schema;
3. apply deterministic stage-transition rules;
4. create immutable next-stage queue batches when required;
5. update the mutable V3 ledger with completed-stage state, terminal state, parked review state, and processed staging fingerprints;
6. remain idempotent across retries;
7. fail closed on malformed, conflicting, stale, incomplete, or inconsistent staging data;
8. run before new inbox preparation in the hourly V3 CPU workflow.

This subsystem will **not**:

- make semantic ACCEPT/REJECT judgments itself;
- promote to canonical V2 accepted/review/manifests;
- train from raw inbox or staging data;
- write outside `ml/coach/miner-data/v3/`;
- run GPU workloads;
- replace the ChatGPT semantic Orchestrator;
- enable canonical promotion during SHADOW MODE.

## Storage boundaries

Read-only inputs:

```text
ml/coach/miner-data/v3/queues/{triage,deep,judge,review,audit}/
ml/coach/miner-data/v3/staging/{triage,deep,judge,review,audit}/YYYY/MM/DD/
ml/coach/miner-data/v3/ledgers/distill.json
ml/coach/miner-data/v3/leases/
```

Materializer writes only:

```text
ml/coach/miner-data/v3/queues/{deep,judge,review,audit}/
ml/coach/miner-data/v3/ledgers/distill.json
ml/coach/miner-data/v3/metrics/YYYY/MM/DD.json
```

No materializer code may write:

```text
ml/coach/miner-data/distilled/accepted/
ml/coach/miner-data/distilled/review/
ml/coach/miner-data/distilled/manifests/
```

## High-level flow

```text
ChatGPT Orchestrator
      |
      v
immutable V3 staging file
      |
      v
Stage Materializer
  - validate run/batch/stage
  - validate candidate coverage
  - validate input fingerprints
  - validate legal decisions
  - check replay/idempotence
      |
      +--> terminal/parked state
      |
      +--> immutable next queue batch
      |
      v
optimistic ledger update
```

Hourly public CPU workflow order:

```text
1. private-repo preflight
2. materialize completed semantic staging
3. persist next queues + ledger/metrics
4. prepare newly arrived inbox blobs into TRIAGE
5. persist TRIAGE queues + ledger/metrics
6. emit summary
```

Stage advancement happens before new inbox preparation so completed semantic work cannot starve behind continuous Miner ingestion.

## Ledger extensions

`DistillLedger` remains the single mutable semantic-state ledger and gains:

```text
processed_staging_shas: set[str]
completed_batch_ids: set[str]
parked_review_candidate_ids: set[str]
```

Semantics:

- `processed_staging_shas`: staging blobs already fully materialized.
- `completed_batch_ids`: semantic source batches already completed.
- `parked_review_candidate_ids`: unresolved REVIEW results that must not be immediately requeued.

### Candidate routing metadata

The current `CandidateStageState` does not retain enough information to reconstruct per-candidate destination priority after TRIAGE. The implementation therefore extends it with a backward-compatible field:

```text
source_type: academic | official | coaching | interview | null
```

Rules:

- new candidates routed from inbox must store `source_type` in `CandidateStageState`;
- forward stage materialization requires `source_type` so `priority_for(source_type, destination_stage)` can be recomputed deterministically per candidate;
- old persisted V3 states may omit it because the field defaults to `null`;
- a legacy state without `source_type` cannot be silently assigned a guessed priority: materialization is blocked for that candidate until the source metadata is rehydrated from its original inbox record or another authoritative stored source record.

This avoids using a source batch's max priority as a lossy per-candidate substitute.

A staging SHA is added to `processed_staging_shas` only after the entire staging file validates and all transitions are represented in the returned in-memory result.

## Staging file identity and atomicity

Each staging file represents one Orchestrator run against exactly one source queue batch.

Required run-level fields:

```text
run_id
batch_id
stage
worker
created_at
input_fingerprints
records
```

Before any transition is applied, the whole file must satisfy:

1. `batch_id` resolves to an existing queue file.
2. queue stage exactly equals staging stage.
3. batch is not completed unless this is an exact idempotent replay.
4. staging candidate IDs exactly equal source batch candidate IDs; no missing or extra candidates.
5. candidate IDs are unique.
6. staging input fingerprints exactly match the source batch candidate-to-fingerprint mapping.
7. every record uses a decision legal for that stage.
8. every record preserves its source candidate ID.
9. no record attempts canonical promotion.
10. no record instructs raw-to-training use.

Any failure rejects the entire staging blob from advancement. No partial state or queue is produced.

## Semantic result models

Existing `SemanticResult` remains authoritative for TRIAGE, DEEP, JUDGE, and REVIEW.

The existing REVIEW contract is retained exactly:

```text
PROPOSE_ACCEPT
REVIEW
REJECT
```

There is **no new REVIEW `BLOCKED` decision** in this phase. When evidence is inaccessible, REVIEW is used with a `reason_code` that states the blocking prerequisite, and the candidate is parked.

### New shadow AUDIT result model

The current Core lacks a candidate-level AUDIT staging model, so this phase adds a separate model rather than overloading `SemanticResult`:

```text
ShadowAuditResult
  candidate_id
  stage = AUDIT
  decision = CREATE | SUPPORT | REFINE | CONTRADICT | REVIEW | BLOCKED
  reason_code
  knowledge_unit_id | null
  concept_id | null
  evidence_refs[]
```

Rules:

- `CREATE|SUPPORT|REFINE|CONTRADICT` require the relevant knowledge/concept identifiers required by the approved audit contract;
- `REVIEW|BLOCKED` do not promote and instead park the candidate;
- during SHADOW MODE this model is evidence/state only and cannot write canonical V2 outputs.

`DailyAuditRecord(status=COMPLETED|BLOCKED)` remains the day-level audit completion record and is not replaced by `ShadowAuditResult`.

## Legal stage transitions

### TRIAGE

```text
REJECT       -> terminal
DUPLICATE    -> terminal
DEEP_PENDING -> DEEP queue
```

Triage never ACCEPTs and never creates JUDGE/AUDIT work directly.

### DEEP

```text
PROPOSE_ACCEPT -> JUDGE queue
REVIEW         -> REVIEW queue unless currently parked
REJECT         -> terminal
```

Deep never canonicalizes.

### JUDGE

```text
CONFIRM -> AUDIT queue
REVIEW  -> REVIEW queue unless currently parked
REJECT  -> terminal
```

`CONFIRM` is not canonical acceptance. In SHADOW MODE it only creates AUDIT work.

### REVIEW

```text
PROPOSE_ACCEPT -> JUDGE queue
REJECT         -> terminal
REVIEW         -> park as ACTIVE_REVIEW; no immediate requeue
```

A parked REVIEW record keeps its `reason_code`/blocking prerequisite in the staging evidence. Automatic reactivation is outside initial materializer scope.

### AUDIT

Allowed `ShadowAuditResult.decision`:

```text
CREATE
SUPPORT
REFINE
CONTRADICT
REVIEW
BLOCKED
```

Materialization:

- mark the AUDIT batch completed;
- `CREATE|SUPPORT|REFINE|CONTRADICT` become completed shadow-audit evidence only;
- `REVIEW|BLOCKED` park the candidate;
- create no next semantic queue automatically;
- write no canonical accepted/review/manifest output.

Canonical promotion remains disabled until a later explicit rollout gate.

## Deterministic next-queue construction

Next queues reuse `build_batches()` and stable V3 batch IDs.

Per destination stage:

1. collect legal transitioning candidates;
2. retrieve each candidate's source fingerprint from ledger/source batch state;
3. require `CandidateStageState.source_type`;
4. compute `priority_for(source_type, destination_stage)` per candidate;
5. sort deterministically using existing queue logic;
6. group by explicit destination batch cap;
7. write destination queue immutably.

Initial destination caps are fixed to the approved scheduling targets:

```text
DEEP   = 30
JUDGE  = 30
REVIEW = 20
AUDIT  = 30
```

TRIAGE remains prepared separately by the existing preparer, with its current configurable cap up to 100.

A pre-existing next queue with identical bytes is idempotent success. Different bytes at the same deterministic path are a hard conflict; no rename/overwrite is allowed.

## Batch completion and leases

A source semantic batch becomes complete only after its full staging file validates and deterministic transitions have been computed.

After completion:

- add batch ID to `completed_batch_ids`;
- advance all candidate states;
- add staging SHA to `processed_staging_shas`;
- future Orchestrator selection must exclude the completed batch even after its lease expires.

Leases are only in-flight claim guards. They are not completion markers.

## Candidate state examples

```text
TRIAGE DEEP_PENDING:
  TRIAGE/COMPLETE -> DEEP/PENDING

DEEP PROPOSE_ACCEPT:
  DEEP/COMPLETE -> JUDGE/PENDING

JUDGE CONFIRM:
  JUDGE/COMPLETE -> AUDIT/PENDING

REVIEW REVIEW:
  REVIEW/COMPLETE + parked_review_candidate_ids

REJECT/DUPLICATE:
  current-stage/COMPLETE + terminal_candidate_ids

AUDIT CREATE/SUPPORT/REFINE/CONTRADICT:
  AUDIT/COMPLETE
```

Source fingerprint never changes across stage transitions. Source type also remains stable.

An incompatible existing stage, fingerprint, or source type is a hard conflict.

## Pure materializer interface

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

This function performs no HTTP/filesystem writes.

Remote orchestration separately:

1. discovers staging files recursively;
2. loads queue files referenced by unprocessed staging blobs;
3. rehydrates missing legacy source type only from authoritative candidate/source data;
4. calls the pure core;
5. validates hard invariants;
6. preflights all immutable queue destinations;
7. writes missing next queues;
8. optimistic-SHA updates ledger/metrics;
9. returns a machine-readable summary.

## Write ordering and recovery

GitHub Contents API is not transactional, so writes use recoverable ordering:

1. compute and validate full materialization in memory;
2. preflight every immutable destination path;
3. abort before writes if any different-byte collision exists;
4. create missing immutable next queues;
5. optimistic-SHA update ledger;
6. optimistic-SHA update metrics.

If execution dies after queue creation but before ledger update, the retry sees identical queue bytes and treats them idempotently, then safely retries the ledger update.

No blind mutable overwrite is permitted.

## Hard invariants

Must remain zero:

```text
raw_to_training_bypass
raw_to_canonical_bypass
canonical_without_judge
simultaneous_valid_lease_conflicts
audit_duplicate_leakage
illegal_stage_transition
staging_candidate_set_mismatch
staging_fingerprint_mismatch
source_type_rehydration_failure_after_write
```

Release invariants:

1. each staging SHA is semantically materialized at most once;
2. completed batches cannot become eligible again merely because leases expire;
3. malformed staging never causes partial advancement;
4. Triage cannot create ACCEPT/JUDGE/AUDIT directly;
5. Deep cannot canonicalize;
6. Judge CONFIRM creates AUDIT work only in SHADOW MODE;
7. repeated REVIEW is parked instead of infinitely requeued;
8. immutable queue collisions fail closed;
9. mutable ledger/metrics are optimistic-SHA only;
10. canonical V2 paths remain untouched;
11. destination priority is recomputed from preserved source type, never guessed from source-batch max priority.

## Hourly workflow integration

`.github/workflows/distill_v3_prepare.yml` remains the only V3 public CPU write workflow.

Its deterministic cycle becomes:

```text
PHASE A: MATERIALIZE
  discover unprocessed staging
  validate and advance stages
  persist next queues/ledger/metrics

PHASE B: PREPARE
  discover new Miner inbox blobs
  exact dedup/history checks
  persist TRIAGE queues/ledger/metrics
```

Schedule remains:

```text
42 * * * * UTC
```

Miner `mine.yml` remains unchanged at `17 */3 * * *` and `--budget 20000`.

Manual workflow dispatch remains dry-run by default unless `write_shadow=true` is explicitly selected.

## Orchestrator contract tightening

Batch eligibility must require:

```text
batch.status == PENDING
batch_id not in ledger.completed_batch_ids
no active lease for batch_id
```

The Orchestrator claims exactly one eligible batch per run.

It writes only its immutable staging result. It does **not** create next-stage queues or directly advance the ledger. The Stage Materializer is the sole deterministic owner of next-queue creation.

## Metrics additions

```text
staging_files_seen
staging_files_materialized
staging_files_invalid
batches_completed
candidates_advanced
terminalized_candidates
parked_review_candidates
next_batches_created_by_stage
illegal_stage_transition
staging_candidate_set_mismatch
staging_fingerprint_mismatch
legacy_source_type_rehydrations
```

Existing `DailyMetrics` release invariants remain authoritative.

## TDD requirements

Implementation is test-first.

Required tests:

1. TRIAGE `DEEP_PENDING` -> DEEP queue.
2. TRIAGE `REJECT`/`DUPLICATE` -> terminal.
3. DEEP `PROPOSE_ACCEPT` -> JUDGE.
4. DEEP `REVIEW` -> REVIEW.
5. JUDGE `CONFIRM` -> AUDIT, never canonical.
6. JUDGE `REVIEW` -> REVIEW.
7. REVIEW `PROPOSE_ACCEPT` -> JUDGE.
8. REVIEW `REVIEW` -> parked/no immediate requeue.
9. AUDIT `CREATE|SUPPORT|REFINE|CONTRADICT` -> shadow complete only.
10. AUDIT `REVIEW|BLOCKED` -> parked.
11. exact candidate-set validation.
12. exact candidate/fingerprint mapping validation.
13. illegal decision blocks entire staging blob.
14. malformed one-row record blocks entire staging blob.
15. completed batch cannot process twice.
16. processed staging SHA rerun is no-op.
17. output is deterministic regardless of staging discovery order.
18. next queue caps and IDs are deterministic.
19. per-candidate destination priority uses preserved source type.
20. missing legacy source type fails closed unless authoritatively rehydrated.
21. same-byte queue collision is idempotent.
22. different-byte queue collision aborts before writes.
23. observed-SHA ledger update enforced.
24. canonical V2 path writes absent/rejected.
25. Orchestrator excludes completed batches.
26. workflow runs MATERIALIZE before PREPARE.
27. Miner schedule/budget remains 3-hour/20,000.
28. `ShadowAuditResult` validation is strict and separate from `DailyAuditRecord`.

## Expected files

Create:

```text
src/basketball_miner/distill_v3/materialize.py
tests/test_v3_materialize.py
```

Modify:

```text
src/basketball_miner/distill_v3/models.py
src/basketball_miner/distill_v3/ledger.py
src/basketball_miner/distill_v3/metrics.py
src/basketball_miner/distill_v3/prepare.py
scripts/run_distill_v3_prepare.py
docs/formpath-v3-orchestrator-prompt.md
tests/test_v3_models.py
tests/test_v3_ledger_router.py
tests/test_v3_prepare_remote.py
tests/test_v3_orchestrator_contract.py
tests/test_workflow_policy.py
```

No new third-party runtime dependency is allowed.

## Rollout gate

After implementation:

1. full pytest and Ruff pass at branch HEAD;
2. compare against approved 40x base remains ahead-only;
3. `mine.yml` still contains `--budget 20000` twice and `17 */3 * * *`;
4. no new code targets canonical V2 write paths;
5. branch remains unmerged until explicit integration choice;
6. after integration, run one V3 dry-run;
7. run one shadow write;
8. verify `hoopDB` contains only legal next-stage queues/updated ledger under V3 root;
9. only after the shadow gate succeeds may the live `FormPath Daily Distillation` automation be converted into the hourly V3 Orchestrator.

## Definition of done

The Stage Materializer is complete when every valid immutable semantic staging output can be deterministically advanced to the correct next V3 state; malformed/conflicting staging fails atomically; per-candidate priority is reconstructed from preserved source type rather than guessed; completed batches cannot be reclaimed after lease expiry; REVIEW loops are parked; AUDIT has a strict shadow-only result contract; all writes remain confined to the V3 root; Judge CONFIRM creates AUDIT work rather than canonical acceptance; retries are idempotent; and the hourly deterministic workflow can continuously advance `TRIAGE -> DEEP -> JUDGE -> REVIEW/AUDIT` without requiring the semantic Orchestrator to mutate the state machine itself.
