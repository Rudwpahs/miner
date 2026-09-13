# Distillation V3 Stage Materializer Design

Date: 2026-09-14
Status: Approved architecture, awaiting implementation-plan approval
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

Read-only historical/private inputs:

```text
ml/coach/miner-data/v3/queues/{triage,deep,judge,review,audit}/
ml/coach/miner-data/v3/staging/{triage,deep,judge,review,audit}/YYYY/MM/DD/
ml/coach/miner-data/v3/ledgers/distill.json
ml/coach/miner-data/v3/leases/
```

Materializer writes only under:

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

The hourly public CPU workflow becomes:

```text
1. private-repo preflight
2. materialize completed semantic staging
3. persist next queues + ledger/metrics
4. prepare newly arrived inbox blobs into TRIAGE
5. persist TRIAGE queues + ledger/metrics
6. emit summary
```

Stage advancement happens before new inbox preparation so completed semantic work is never starved by continuous Miner ingestion.

## New ledger state

The existing `DistillLedger` remains the single mutable semantic-state ledger. It is extended with deterministic replay guards:

```text
processed_staging_shas: set[str]
completed_batch_ids: set[str]
parked_review_candidate_ids: set[str]
```

Semantics:

- `processed_staging_shas`: staging blobs already fully and successfully materialized.
- `completed_batch_ids`: source semantic batches whose validated output has already been committed.
- `parked_review_candidate_ids`: unresolved review items that have already completed a REVIEW attempt but still require evidence; they are not immediately requeued into REVIEW.

Existing fields continue to track candidate states, terminal candidates, source hashes, and processed inbox blobs.

A staging SHA is added only after the entire staging file validates and all deterministic transitions for that file are successfully represented in the returned in-memory result. Persistent mutation follows the same optimistic-SHA discipline already used by V3 shadow preparation.

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

The materializer treats the staging file as an atomic unit.

Before any candidate transition is applied, it validates all of the following:

1. `batch_id` resolves to an existing source queue file.
2. source queue stage exactly equals staging stage.
3. source batch is not already completed unless this is an exact idempotent replay.
4. staging record candidate IDs exactly equal the source batch candidate IDs; no missing or extra candidates.
5. candidate IDs are unique.
6. staging `input_fingerprints` exactly match the source batch fingerprints in the same candidate mapping.
7. every decision is legal for that stage.
8. every record preserves its source candidate ID.
9. no record attempts canonical promotion.
10. no record instructs raw-to-training use.

If any row fails, the entire staging blob is rejected from advancement. No partial queue or ledger state is produced for that blob.

## Legal stage transitions

### TRIAGE

Allowed semantic decisions:

```text
REJECT
DUPLICATE
DEEP_PENDING
```

Materialization:

```text
REJECT       -> terminal candidate
DUPLICATE    -> terminal candidate
DEEP_PENDING -> DEEP queue
```

Triage never ACCEPTs and never creates JUDGE or AUDIT work directly.

### DEEP

Allowed semantic decisions:

```text
PROPOSE_ACCEPT
REVIEW
REJECT
```

Materialization:

```text
PROPOSE_ACCEPT -> JUDGE queue
REVIEW         -> REVIEW queue, unless already parked for unresolved review
REJECT         -> terminal candidate
```

Deep never canonicalizes.

### JUDGE

Allowed semantic decisions:

```text
CONFIRM
REVIEW
REJECT
```

Materialization during SHADOW MODE:

```text
CONFIRM -> AUDIT queue
REVIEW  -> REVIEW queue, unless already parked for unresolved review
REJECT  -> terminal candidate
```

`CONFIRM` is not canonical acceptance. It is only an audited-promotion candidate in immutable V3 shadow state.

### REVIEW

Allowed semantic decisions:

```text
PROPOSE_ACCEPT
REJECT
REVIEW
BLOCKED
```

Materialization:

```text
PROPOSE_ACCEPT -> JUDGE queue
REJECT         -> terminal candidate
REVIEW         -> park as ACTIVE_REVIEW; do not immediately requeue
BLOCKED        -> park as ACTIVE_REVIEW/BLOCKED with blocking prerequisite
```

This rule prevents REVIEW -> REVIEW infinite loops on every hourly execution.

A future semantic run may explicitly reactivate parked review candidates after new evidence, source availability, or a manual review-refresh operation. Automatic reactivation is outside this materializer's initial scope.

### AUDIT

In the current SHADOW MODE, AUDIT staging is terminal shadow evidence only.

Allowed decisions:

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
- update candidate state to `AUDIT/COMPLETE` or parked REVIEW/BLOCKED as appropriate;
- do **not** write canonical accepted/review/manifest files;
- do not create another semantic queue automatically.

Canonical promotion remains disabled until a separate rollout gate explicitly changes SHADOW MODE.

## Next-queue construction

Next queues reuse the existing deterministic `build_batches()` and stable V3 batch-ID rules.

For each destination stage:

1. collect candidate IDs that legally transition there;
2. preserve source fingerprints from ledger/source queue data;
3. compute deterministic priority with existing stage/source priority rules;
4. sort deterministically;
5. group using the existing stage-specific maximum batch-size policy;
6. write each destination queue immutably.

A pre-existing next queue with identical bytes is an idempotent success.
A pre-existing queue path with different bytes is a hard conflict and aborts the materialization write set.

No rename-on-collision behavior is allowed.

## Batch completion and lease semantics

A source semantic batch becomes `COMPLETE` only after its full staging output has passed validation and the materializer has produced a deterministic transition result.

After batch completion:

- the batch ID is placed in `completed_batch_ids`;
- all relevant candidate states are advanced;
- its staging SHA is placed in `processed_staging_shas`;
- future Orchestrator selection must exclude the completed batch even if an old lease later expires.

Leases remain claim guards for in-flight work, not completion markers.

This distinction closes the retry bug where a completed batch could otherwise become claimable again after lease expiration.

## Candidate-state updates

For each materialized record, `CandidateStageState` is advanced deterministically.

Examples:

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
```

The transition timestamp comes from the materializer run timestamp, while source fingerprint remains unchanged.

An existing candidate state with an incompatible stage/fingerprint is a hard conflict.

## Materializer result model

Pure deterministic core:

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

This function performs no network or filesystem I/O.

Remote orchestration is a separate helper that:

1. discovers staging files recursively;
2. loads source queue files referenced by unprocessed staging blobs;
3. calls the pure core;
4. validates hard invariants;
5. creates immutable next queues first;
6. updates ledger/metrics with the observed remote SHA;
7. returns a machine-readable summary.

## Write ordering and fail-closed behavior

Because GitHub Contents API does not offer a multi-file transaction, writes use a recoverable ordering:

1. fully compute and validate the entire materialization in memory;
2. preflight every immutable destination queue path by reading it;
3. if any path conflicts with different bytes, abort before any write;
4. create missing immutable next queues;
5. optimistic-SHA update ledger;
6. optimistic-SHA update metrics.

If the process fails after immutable queue creation but before ledger update, rerun sees the exact same queue bytes and treats those queue writes idempotently. The ledger is then safely retried.

The materializer never performs a blind mutable overwrite.

## Hard invariants

Every run must keep these values at zero:

```text
raw_to_training_bypass
raw_to_canonical_bypass
canonical_without_judge
simultaneous_valid_lease_conflicts
audit_duplicate_leakage
illegal_stage_transition
staging_candidate_set_mismatch
staging_fingerprint_mismatch
```

Additional release invariants:

1. a staging SHA is processed at most once semantically;
2. a completed source batch is never re-claimed solely because its lease expired;
3. no partial candidate advancement occurs from a malformed staging blob;
4. Triage cannot create ACCEPT/JUDGE/AUDIT directly;
5. Deep cannot canonicalize;
6. Judge CONFIRM creates AUDIT work only during SHADOW MODE;
7. repeated REVIEW does not generate an infinite hourly queue loop;
8. immutable next queue collisions fail closed;
9. mutable ledger/metrics writes are optimistic-SHA only;
10. canonical V2 output paths remain untouched.

## Hourly workflow integration

The existing `.github/workflows/distill_v3_prepare.yml` remains the only new public V3 CPU write workflow.

Its script evolves from preparation-only behavior to a two-phase deterministic cycle:

```text
PHASE A: MATERIALIZE
  discover unprocessed semantic staging
  validate and advance stages
  persist next queues/ledger/metrics

PHASE B: PREPARE
  discover new Miner inbox blobs
  exact dedup / historical checks
  persist TRIAGE queues/ledger/metrics
```

The schedule remains:

```text
42 * * * * UTC
```

No change is made to Miner `mine.yml` 3-hour/20,000 inspection configuration.

Manual workflow dispatch remains dry-run by default unless `write_shadow=true` is explicitly selected.

## Orchestrator contract change

The Orchestrator prompt must be tightened so batch eligibility requires:

```text
batch.status == PENDING
batch_id not in ledger.completed_batch_ids
no active lease for batch_id
```

The Orchestrator still claims exactly one eligible batch per hourly run.

It never creates the next semantic queue itself. It only writes the immutable staging output for its current batch. The Stage Materializer is the sole owner of deterministic next-queue creation.

This removes state-machine mutation from the semantic worker and prevents competing queue-generation logic.

## Metrics

Add materializer metrics:

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
```

Existing DailyMetrics hard release invariants remain authoritative.

## TDD requirements

Implementation must be test-first.

Core tests must cover:

1. TRIAGE `DEEP_PENDING` -> deterministic DEEP queue.
2. TRIAGE `REJECT`/`DUPLICATE` -> terminal state.
3. DEEP `PROPOSE_ACCEPT` -> JUDGE.
4. DEEP `REVIEW` -> REVIEW.
5. JUDGE `CONFIRM` -> AUDIT, never canonical output.
6. JUDGE `REVIEW` -> REVIEW.
7. REVIEW `PROPOSE_ACCEPT` -> JUDGE.
8. REVIEW `REVIEW`/`BLOCKED` -> parked, no immediate requeue.
9. AUDIT staging completes without canonical writes in SHADOW MODE.
10. exact candidate-set validation.
11. exact input-fingerprint validation.
12. illegal decision for stage blocks the whole staging blob.
13. malformed one-row record blocks the whole staging blob.
14. completed batch is not processed twice.
15. processed staging SHA is skipped idempotently.
16. deterministic output independent of staging-discovery order.
17. next queue max sizes and stable IDs are deterministic.
18. queue same-byte collision is idempotent.
19. queue different-byte collision aborts before writes.
20. observed-SHA ledger update is enforced.
21. canonical V2 path write attempts are absent/rejected.
22. Orchestrator prompt excludes completed batches from eligibility.
23. hourly workflow still runs materialize before prepare.
24. Miner `mine.yml` remains 3-hour/20,000.

## Files expected to change

Create:

```text
src/basketball_miner/distill_v3/materialize.py
tests/test_v3_materialize.py
```

Modify:

```text
src/basketball_miner/distill_v3/ledger.py
src/basketball_miner/distill_v3/metrics.py
src/basketball_miner/distill_v3/prepare.py
scripts/run_distill_v3_prepare.py
docs/formpath-v3-orchestrator-prompt.md
tests/test_v3_prepare_remote.py
tests/test_v3_orchestrator_contract.py
tests/test_workflow_policy.py
```

No new third-party runtime dependency is permitted.

## Rollout gate

After implementation:

1. full pytest and Ruff must pass at branch HEAD;
2. compare against the approved 40x base must remain ahead-only;
3. `mine.yml` must still contain `--budget 20000` twice and `17 */3 * * *`;
4. no newly added code may target canonical V2 write paths;
5. branch remains unmerged until explicit integration choice;
6. after merge, run one V3 dry-run;
7. run one shadow write;
8. verify `hoopDB` contains legal next-stage queues/updated ledger only under V3 root;
9. only after that successful shadow gate may the live `FormPath Daily Distillation` automation be converted into the hourly V3 Orchestrator.

## Definition of done

The Stage Materializer is complete when every valid immutable semantic staging output can be deterministically advanced to the correct next V3 state; malformed/conflicting staging fails atomically; completed batches cannot be reclaimed after lease expiry; REVIEW loops are parked rather than endlessly requeued; all writes remain confined to the V3 root; Judge CONFIRM remains shadow-only and creates AUDIT work rather than canonical acceptance; retries are idempotent; and the hourly deterministic workflow can continuously advance `TRIAGE -> DEEP -> JUDGE -> REVIEW/AUDIT` without requiring the semantic Orchestrator to mutate the state machine itself.
