# FormPath Distillation V3 Design

Date: 2026-09-14
Status: Approved architecture, pre-implementation specification
Base: `work/miner-40x-throughput` at `df22a25cac138654e5ef39c1e31c7312b44e95e5`

## Purpose

Distillation V3 turns the 40x Basketball Knowledge Miner output into an auditable, idempotent knowledge pipeline while keeping the B-policy unchanged and avoiding additional cloud-GPU/API requirements.

V3 separates four responsibilities:

1. `Rudwpahs/miner` (public): deterministic CPU preprocessing, queue creation, validation, indexes, ledgers, metrics.
2. `Rudwpahs/hoopDB` (private): raw candidates, queues, immutable staging, concepts, canonical outputs, manifests, GPU job/result state.
3. One ChatGPT scheduled task: semantic orchestration for Triage, Deep, Judge, Review Resolver, and Daily Audit.
4. `Rudwpahs/formpath-compute` (private): RTX 4060 self-hosted compute for Qwen/FormQuant/QLoRA/benchmarks. GPU execution is isolated from the public miner repository.

## Core principles

- Deterministic work is code; semantic judgment is GPT.
- Triage never ACCEPTs. It emits only `REJECT`, `DUPLICATE`, or `DEEP_PENDING`.
- Deep emits only `PROPOSE_ACCEPT`, `REVIEW`, or `REJECT`.
- A proposed acceptance must pass Judge before it can be considered for canonical promotion.
- Worker outputs are immutable run files. Only the Daily Auditor writes canonical daily accepted/review/manifest outputs.
- Every stage is retry-safe through stable IDs, source fingerprints, ledgers, and expiring leases.
- Raw inbox data never becomes training data or canonical knowledge directly.

## Data flow

```text
40x Miner
  -> hoopDB inbox
  -> deterministic preprocessor
  -> TRIAGE queue
  -> ChatGPT Triage
  -> DEEP queue
  -> ChatGPT Deep
  -> JUDGE queue
  -> ChatGPT Judge
  -> REVIEW when needed
  -> immutable staging
  -> Daily Auditor
  -> CREATE / SUPPORT / REFINE / CONTRADICT
  -> canonical concepts and knowledge
```

Heavy ML work is separate:

```text
hard cases / approved calibration data
  -> private GPU job queue
  -> RTX 4060 worker
  -> Qwen / FormQuant / QLoRA / benchmark result
  -> private result state
  -> later GPT orchestration cycle interprets the result
```

## Public miner package

Implementation target:

```text
src/basketball_miner/distill_v3/
  models.py
  ids.py
  router.py
  ledger.py
  queue.py
  concept_index.py
  staging.py
  audit.py
  metrics.py

scripts/run_distill_v3.py
```

Tests mirror those units under `tests/`.

The public repository contains only reusable processing code and public workflow definitions. Private candidate data and local GPU execution stay outside it.

## hoopDB V3 layout

```text
ml/coach/miner-data/v3/
  queues/{triage,deep,judge,review,audit}/
  leases/
  ledgers/
  staging/{triage,deep,judge,review,audit}/YYYY/MM/DD/
  concepts/concept_index.jsonl
  concepts/concept_support.jsonl
  metrics/YYYY/MM/DD.json
  gpu-jobs/{pending,claimed,complete,failed}/
  gpu-results/
```

Historical V2 `distilled/accepted`, `distilled/review`, and `distilled/manifests` remain immutable and are indexed as prior knowledge.

Canonical outputs continue to use:

```text
ml/coach/miner-data/distilled/accepted/YYYY/MM/DD.jsonl
ml/coach/miner-data/distilled/review/YYYY/MM/DD.jsonl
ml/coach/miner-data/distilled/manifests/YYYY/MM/DD.json
```

## State model

A semantic batch has:

- `batch_id`
- `stage`
- ordered `candidate_ids`
- priority
- created time
- input fingerprints
- status: `PENDING | CLAIMED | COMPLETE | FAILED`

A lease records batch ID, worker identity, claim time, expiry time, and attempt number. A non-expired lease prevents duplicate claims; an expired lease makes the same batch safely reclaimable.

Candidate stage state records candidate ID, source fingerprint, current stage, current status, batch ID, attempt number, and update time.

## Deterministic preprocessing

For each previously unseen inbox blob, the CPU preprocessor performs:

1. schema validation
2. stable-ID validation
3. source/DOI normalization
4. exact candidate-ID duplicate check
5. exact normalized-source duplicate check
6. exact canonical-content hash check
7. prior terminal/accepted/review check
8. source-based priority calculation
9. queue creation for unresolved semantic candidates

It does not make semantic ACCEPT decisions.

## Scheduling policy

The single ChatGPT scheduled task selects work in this order:

1. unresolved REVIEW
2. JUDGE
3. DEEP
4. TRIAGE
5. AUDIT when due
6. no-op when no semantic work is pending

Initial micro-batch targets:

- TRIAGE: 50-100
- DEEP: 10-30
- JUDGE: 10-30
- REVIEW: 10-20

Adaptive sizing is deferred until measured metrics exist.

## Semantic contracts

### Triage

Outputs only `REJECT`, `DUPLICATE`, or `DEEP_PENDING`. Uncertain but potentially useful evidence escalates rather than being aggressively rejected.

### Deep

Verifies accessible source evidence, extracts atomic claims, retrieves only a compact relevant concept/KU subset, and outputs `PROPOSE_ACCEPT`, `REVIEW`, or `REJECT`.

### Judge

Performs an independent failure-focused check of a proposed acceptance:

- source supports claim
- quantitative details are faithful
- population/context is scoped correctly
- association is not upgraded to causation
- medical/injury claims remain within guardrails
- semantic duplicate/merge action is resolved

Outputs only `CONFIRM`, `REVIEW`, or `REJECT`.

### Review Resolver

Attempts to resolve missing or ambiguous evidence without weakening B-policy. REVIEW is treated as a high-priority evidence-completion queue.

### Daily Auditor

The only semantic promotion gate. It checks cross-batch duplicates and contradictions, applies concept actions, writes canonical daily outputs, updates compact concept indexes, and records the daily manifest.

## Concept-centered model

V3 separates:

```text
candidate -> adjudicated knowledge unit -> concept
```

A new source does not automatically create a new concept. Judge/Auditor assigns one of:

- `CREATE`: genuinely new concept
- `SUPPORT`: additional evidence
- `REFINE`: narrower context or qualification
- `CONTRADICT`: meaningful conflicting evidence

This limits knowledge-unit explosion and gives future retrieval a compact semantic target.

## Retrieval

Deep/Judge do not read the entire accepted corpus. A deterministic compact index shortlists relevant records by normalized source ID, topic/context codes, claim signature, and concept ID. Only shortlisted full records are loaded for semantic comparison.

V3 initial implementation does not require a vector database. A vector index is added only if measured retrieval failures justify it.

## GPU boundary

GPU execution is optional to normal canonical distillation and occurs only in the private compute environment. First supported job classes are:

- `QWEN_BENCHMARK`
- `QUANT_BENCHMARK`
- `FORMQUANT_SENSITIVITY`
- `QLORA_REPAIR`

Jobs are declarative records with a fixed job type, model/dataset IDs, priority, and validated configuration. GPU results preserve the job ID and return status plus metrics. GPT interprets results on a later scheduled cycle; it does not perform GPU computation itself.

## Failure handling

- Interrupted semantic work: lease expiry permits safe retry.
- Partial output: not promotable without a complete validated run record.
- Canonical write conflict: auditor rereads and reconciles instead of blind overwrite.
- GPU machine offline: job remains pending; this does not block ordinary distillation.
- Source unavailable: REVIEW/blocked state, never invented evidence.
- Malformed candidate: deterministic terminal invalid state with reason and source reference.

## Metrics

Daily metrics include:

- raw and unique candidates
- exact duplicates and invalid records
- triage processed/escalation rate
- deep processed/proposed-accept rate
- judge confirm/review/reject counts
- review queue size and resolution rate
- concept CREATE/SUPPORT/REFINE/CONTRADICT counts
- oldest unprocessed candidate age
- backlog by stage
- lease retries
- audit duplicate leakage
- raw-to-canonical bypass count

Release invariants:

1. raw-to-training direct path = 0
2. raw-to-canonical bypass = 0
3. canonical ACCEPT requiring Judge but lacking confirmation = 0
4. one canonical semantic writer (Daily Auditor)
5. reruns produce no duplicate terminal/canonical records
6. one batch cannot have two valid simultaneous leases
7. public miner has no local GPU execution path

## TDD test groups

- models and legal stage transitions
- stable IDs and DOI/source normalization
- exact dedup
- priority queue and micro-batching
- lease claim/expiry/reclaim
- processed-ledger idempotence
- V2 historical compatibility
- immutable staging
- Judge gate enforcement
- auditor canonical-writer enforcement
- concept CREATE/SUPPORT/REFINE/CONTRADICT behavior
- metrics invariants
- public/private compute boundary checks

## Rollout

1. Implement models, IDs, queue, lease, ledger, and routing with fixtures.
2. Add dry-run CLI; no private writes.
3. Index existing V2 accepted/review history read-only.
4. Add V3 queue/staging writers.
5. Add public CPU preprocessing workflow.
6. Convert the single ChatGPT daily task into the V3 semantic orchestrator.
7. Run V3 in shadow mode: staging and metrics only.
8. Compare V3 outcomes with existing B-policy outcomes.
9. Enable Daily Auditor canonical promotion after invariants pass.
10. Configure private `Rudwpahs/formpath-compute` and RTX 4060 worker.
11. Add GPU job/result processing.
12. Integrate FormQuant/QLoRA feedback only after the core pipeline is stable.

## Non-goals for V3 core

V3 core does not lower B-policy thresholds, train from raw inbox, require paid APIs/cloud GPU/vector DB, implement adaptive worker scaling, or implement FormQuant itself. FormQuant starts after queue/Judge/audit/GPU-job contracts are stable.

## Definition of done

V3 core is complete when deterministic preprocessing is CI-green and idempotent; semantic contracts are structurally validated; Judge-gated promotion is enforced; staging is immutable; only the Auditor promotes canonical output; concepts support CREATE/SUPPORT/REFINE/CONTRADICT; backlog and quality invariants are measurable; the scheduled GPT task is state-driven rather than monolithic; the public CPU/private GPU boundary is enforced; and shadow mode shows no duplicate processing or raw-to-canonical bypass.
