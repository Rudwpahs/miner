# Coach Provenance Bridge V1 Design

Date: 2026-09-15
Status: Approved direction, implementation specification
Base: `main@f49503424d904568fa8484574dac1d8ae51a96dc`
Branch: `work/provenance-bridge-v1`

## Purpose

Hoop Hub Coach currently retrieves from the immutable 940-unit Knowledge Machine V2 corpus. That artifact is useful for machine-code retrieval, but the fixed B2-B.2 benchmark selected 320 evidence slots with provenance `ROW_ONLY` and zero `LINKED` selections. The limitation is not that source evidence is absent: canonical accepted records in private `Rudwpahs/hoopDB` already preserve verified source URL/DOI, source title, candidate identity, authors, publication date, limitations, safe-inference boundaries, and `provenance.status="LINKED"` for many Judge/Audit-approved knowledge units.

The bridge must preserve that source identity without fabricating provenance and without mutating the legacy 940-unit artifact.

## Decision

Use an **additive linked evidence layer**.

Do not rewrite `units.machine.jsonl`, `sources.jsonl`, the legacy SQLite corpus, or any RU's `ROW_ONLY` status merely because a semantically similar canonical KU exists.

Instead:

```text
Miner / Distillation V3
  -> hoopDB canonical accepted KU
  -> explicit Coach projection (semantic, audited, private)
  -> deterministic Provenance Bridge validator/exporter (public code)
  -> versioned LINKED evidence bundle
  -> Coach dual-source retrieval (separate integration task)
  -> LINKED evidence preferred when it directly matches the query
  -> legacy ROW_ONLY remains fallback evidence
```

This makes false provenance harder than missing provenance.

## Repository boundaries

### `Rudwpahs/miner` (this feature)

Owns deterministic public code only:

- projection schema validation
- source/provenance validation
- stable linked evidence IDs
- machine-code validation
- deterministic export
- manifest/hash generation
- collision detection
- offline tests

It does not contain private canonical data.

### `Rudwpahs/hoopDB` (private data)

Owns:

- canonical accepted KUs
- explicit Coach projection records
- generated linked-evidence bundles/manifests
- semantic projection review/audit state

A projection is not inferred by the public exporter. It must already be explicit and approved.

### `Rudwpahs/shooting-profile-coach-ios` (later integration task)

Owns:

- linked bundle vendoring/import
- dual-source retrieval
- frozen `CoachEvidenceItemV1` conversion
- regression benchmark comparing LINKED/ROW_ONLY selection

The current B2-B.2 branch is historically diverged from current app `main`; it must not be merged wholesale merely to consume this bridge.

## Why not retrofit the 940 legacy rows

A legacy RU claim can resemble a canonical KU while differing in population, context, measurement, evidence strength, or conclusion. A fuzzy or LLM-only backfill from RU to source would convert semantic similarity into false provenance.

Therefore V1 permits a legacy RU to remain `ROW_ONLY` indefinitely. A future separately audited overlay may explicitly link selected legacy RUs to canonical KUs, but that is not part of this bridge.

## Canonical input requirements

A canonical KU eligible for linked export must provide:

- stable `knowledge_unit_id`
- `claim`
- `source_candidate_id`
- non-empty `source_url`
- non-empty `source_title`
- stable `source_identifier` when available
- `provenance.status == "LINKED"`
- evidence strength/confidence fields already accepted by the canonical policy
- limitation/contradiction text
- safe-inference boundaries when present

The bridge does not promote `ROW_ONLY`, missing-source, ambiguous DOI, blocked, review-only, or unconfirmed records.

## Explicit Coach projection

Canonical KU fields do not by themselves prove which frozen Coach metric the evidence supports. The private semantic pipeline therefore supplies an explicit projection record.

`CoachProjectionV1`:

```json
{
  "schema_version": "coach-projection-v1",
  "knowledge_unit_id": "KU-...",
  "domain_codes": ["SHOOTING", "BIOMECHANICS"],
  "metric_codes": ["JOINT_ANGLE"],
  "policy_codes": ["DO_NOT_OVERINFER", "REQUIRE_CONTEXT"],
  "effect_code": "UNSPECIFIED",
  "evidence_code": "B",
  "projection_reason": "directly supports context-aware joint-angle interpretation",
  "approved": true
}
```

Rules:

- codes must come from the existing Knowledge Machine/Coach controlled vocabulary plus already-approved runtime sentinels;
- projection may be narrower than the KU but never broader;
- `approved` must be exactly `true` for export;
- no projection means no linked Coach export;
- the deterministic exporter never guesses metric/domain/policy codes from free text.

## Stable numeric Coach evidence ID

Frozen `CoachEvidenceItemV1.research_unit_id` is an integer. Canonical KUs use string IDs.

V1 derives a stable numeric ID without a mutable registry:

```text
payload = UTF-8("coach-linked-v1:" + knowledge_unit_id)
digest = SHA-256(payload)
research_unit_id = 1_000_000_000_000 + int(first_10_hex_digits(digest), 16)
```

Properties:

- deterministic across repositories and runs;
- safely below JavaScript's exact integer limit;
- disjoint from legacy RU 1..940;
- exporter hard-fails if two distinct KU IDs collide in one bundle;
- the manifest records the derivation version.

A future ID scheme change requires a new bridge schema version.

## Linked bundle schema

The exporter emits a directory such as:

```text
coach-linked-v1/
  units.jsonl
  sources.jsonl
  manifest.json
```

### `sources.jsonl`

One normalized source record per unique source identity:

```json
{
  "source_id": "SRC-...",
  "url": "https://doi.org/...",
  "identifier": "10....",
  "title": "...",
  "adapter": "crossref"
}
```

Source IDs are deterministic from normalized source identity. DOI normalization reuses the existing V3 DOI normalization rules where applicable.

### `units.jsonl`

One record per approved canonical KU projection:

```json
{
  "research_unit_id": 1000000000000,
  "knowledge_unit_id": "KU-...",
  "claim": "...",
  "domain_codes": ["SHOOTING"],
  "metric_codes": ["JOINT_ANGLE"],
  "policy_codes": ["DO_NOT_OVERINFER"],
  "effect_code": "UNSPECIFIED",
  "evidence_code": "B",
  "provenance_code": "LINKED",
  "source_ids": ["SRC-..."],
  "source_title": "...",
  "limitations": ["..."],
  "safe_may_infer": ["..."],
  "safe_may_not_infer": ["..."]
}
```

The exporter may truncate only at explicit documented field limits; it must never silently change claim meaning.

### `manifest.json`

Records:

- schema/version
- exporter version
- canonical input record count
- projection input count
- exported unit count
- skipped count by reason
- source count
- linked count
- duplicate/collision counts
- SHA-256 of `units.jsonl`
- SHA-256 of `sources.jsonl`
- deterministic source ordering rule
- deterministic unit ordering rule

No timestamp participates in content hashes. If a generated-at timestamp is included, reproducibility checks compare content hashes rather than raw manifest bytes.

## Fail-closed export rules

A unit is not exported when any of these hold:

- canonical provenance is not exactly `LINKED`;
- source URL/title is empty;
- projection missing;
- projection not approved;
- KU ID mismatch between canonical record and projection;
- unknown domain/metric/policy/effect/evidence code;
- source identity is ambiguous;
- DOI/title verification state is blocked/mismatched where that state is available;
- stable numeric ID collision;
- duplicate KU with non-identical canonical payload;
- malformed safe-inference structure.

Skipped units are counted with stable reason codes. The exporter never converts a failure into `ROW_ONLY`; the legacy layer already provides that fallback independently.

## Retrieval integration contract

The later Coach integration must merge two candidate sources:

1. legacy 940-unit Knowledge Machine V2;
2. linked canonical bundle.

Both are ranked through the same query intent. LINKED status may receive the existing provenance bonus, but provenance alone cannot outrank a direct metric/domain mismatch.

Required behavior:

- direct metric relevance remains dominant;
- at least one safety/limitation evidence item remains preserved when available;
- linked evidence may replace a semantically weaker row-only selection;
- unrelated linked evidence must never be boosted solely to improve the LINKED percentage;
- if no linked evidence matches, current ROW_ONLY behavior remains valid;
- frozen Coach request/response schemas remain unchanged.

## Benchmark policy

The B2-B.2 fixed 40-case benchmark is retained as a regression baseline. A new bridge benchmark adds metrics rather than rewriting history:

- legacy plan alignment rate
- relevance hit rate
- safety preservation rate
- determinism rate
- contract-valid rate
- linked-selection count/rate
- row-only-selection count/rate
- cases with at least one relevant LINKED item
- cases where an unrelated LINKED item displaced a stronger relevant ROW_ONLY item (must be 0)

There is no target such as "100% LINKED". The target is truthful linked coverage.

## Initial rollout

V1 rollout is deliberately narrow:

1. implement exporter schema/validator with synthetic fixtures;
2. run it against a small private hoopDB fixture containing known canonical LINKED and blocked examples;
3. emit a deterministic bundle and manifest;
4. separately integrate the bundle into Coach retrieval;
5. rerun the 40-case benchmark and report actual linked coverage;
6. only then decide whether an explicit legacy RU-to-KU provenance overlay is worth building.

## Non-goals

V1 does not:

- alter Miner B-policy;
- change Judge/Auditor acceptance thresholds;
- auto-link legacy RUs to sources;
- use embeddings/vector DB/LLM reranking;
- train QLoRA;
- change frozen Coach contracts;
- touch app UI, capture, reconstruction, Firebase, or MotionPacket;
- require every Coach metric to have linked evidence.

## Definition of done

Provenance Bridge V1 is complete when public code can deterministically validate and export an explicitly approved set of canonical LINKED KUs into a versioned Coach-linked bundle; malformed, ambiguous, unapproved, or unlinked records fail closed; stable evidence IDs and source IDs are reproducible; bundle hashes are deterministic; legacy 940 corpus bytes remain unchanged; and a later Coach integration can consume the bundle without inventing provenance or changing frozen contracts.
