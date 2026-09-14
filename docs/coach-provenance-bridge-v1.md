# Coach Provenance Bridge V1 Handoff

Date: 2026-09-15
Repository: `Rudwpahs/miner`
Base: `main@f49503424d904568fa8484574dac1d8ae51a96dc`
Branch: `work/provenance-bridge-v1`
Verified implementation head before this handoff: `cae4d65fd7cb660fd77321bfcf8074e8d0e25213`
Status: public exporter implemented and verified; not merged to `main`

## Purpose

Coach Provenance Bridge V1 creates a truthful additive source layer for Hoop Hub Coach without rewriting the immutable 940-unit legacy Knowledge Machine corpus.

The bridge accepts only:

1. canonical accepted knowledge-unit JSONL records whose `provenance.status` is exactly `LINKED`; and
2. explicit `CoachProjectionV1` records whose `approved` field is exactly `true`.

It emits deterministic `units.jsonl`, `sources.jsonl`, and `manifest.json` files that a later app-repository integration can consume alongside the existing 940-unit retrieval layer.

The exporter never creates a semantic projection by reading claim text. KU-to-Coach domain, metric, policy, effect, and evidence codes must already be explicitly reviewed and approved before export.

## Why this exists

The current Coach corpus contains real linked evidence, but existing retrieval/evaluation has not been selecting it. The Claude B2-C scenario/evaluation branch reported a 940-unit corpus with 40 `LINKED` and 900 `ROW_ONLY` units, while its selected scenario evidence remained entirely `ROW_ONLY`. The previous fixed 40-case retrieval benchmark likewise selected 0 `LINKED` / 320 `ROW_ONLY` evidence slots.

The bridge does not raise the LINKED percentage by relabeling legacy rows. Instead it preserves source identity from canonical accepted knowledge units and exposes those units as a separate, explicit evidence layer.

## Repository boundary

This branch changes public Miner bridge code, bridge configuration, tests, fixtures, and documentation only.

It does not:

- modify the legacy 940-unit corpus;
- alter Miner source adapters or collection budgets;
- alter Distillation V3 Judge/Audit acceptance policy;
- write private `hoopDB` data;
- write `Rudwpahs/shooting-profile-coach-ios`;
- change reconstruction math, MotionPacket, Firebase, privacy contracts, UI, or frozen Coach request/response contracts;
- download model weights or train a model.

`hoopDB` remains the intended owner of private canonical accepted records, approved semantic projection records, and generated linked bundles. The iOS/app repository remains the intended owner of the later additive dual-source retrieval integration.

## Export contract

### Canonical JSONL

An exportable canonical record must be `LINKED` and provide enough source identity to export safely. URL and source title are required. `source_identifier` is optional; for DOI sources the exporter normalizes DOI identity from either identifier or URL using the existing Distillation V3 DOI normalization logic.

Records with missing source URL/title are skipped as `source_missing`. Records not exactly `LINKED` are skipped as `provenance_not_linked`. A LINKED record without an approved projection is skipped as `projection_missing`.

Malformed or ambiguous structures that could create false provenance fail closed rather than becoming `ROW_ONLY` output.

### Projection JSONL

Every projection must validate against the frozen codebook in:

`config/coach_bridge_v1_codes.json`

The exporter does not infer codes. Unknown codes, extra fields, malformed KU IDs, or `approved != true` fail validation.

### Stable evidence IDs

Canonical string KU IDs are converted to numeric Coach research-unit IDs with:

```text
payload = UTF-8("coach-linked-v1:" + knowledge_unit_id)
digest = SHA-256(payload)
research_unit_id = 1_000_000_000_000 + int(first_10_hex_digits(digest), 16)
```

A collision between distinct KUs fails the export.

### Stable source identity

DOIs are normalized before source identity derivation. Equivalent DOI URL/identifier representations therefore converge on the same canonical source record. Source IDs are deterministic and begin with `SRC-LINKED-`.

## Output

The exporter writes:

```text
<output>/
  units.jsonl
  sources.jsonl
  manifest.json
```

`units.jsonl` and `sources.jsonl` use deterministic UTF-8 JSONL serialization with sorted object keys and stable ordering. The manifest binds both payloads by SHA-256 and records:

- exporter/schema/codebook/ID derivation versions;
- canonical and projection input counts;
- exported LINKED unit count;
- source count;
- stable skip counters;
- collision count;
- deterministic unit/source ordering rules;
- SHA-256 of exact `units.jsonl` and `sources.jsonl` bytes.

The writer stages temporary files before replacing final filenames and removes temporary leftovers.

## CLI

Example using one canonical input file:

```bash
python scripts/export_coach_linked_v1.py \
  --canonical path/to/canonical-accepted.jsonl \
  --projections path/to/coach-projections-v1.jsonl \
  --output path/to/coach-linked-v1
```

`--canonical` may be repeated for multiple canonical JSONL files.

The command prints only a count summary on success, for example:

```text
exported_units=1 sources=1 skipped=0
```

On validation or parse failure it returns nonzero and does not print private claim/source payloads in the error message.

## Synthetic integration fixture

Public synthetic fixtures are located at:

- `tests/fixtures/coach_bridge/canonical.jsonl`
- `tests/fixtures/coach_bridge/projections.jsonl`

They exercise the complete offline file-to-bundle CLI path without private data or network access.

## Verification evidence

At implementation head `cae4d65fd7cb660fd77321bfcf8074e8d0e25213`, GitHub Actions workflow run `34867354737` executed:

```bash
python -m pytest -q
python -m ruff check src tests scripts
```

Result:

```text
214 passed in 1.86s
All checks passed!
```

The test suite covers strict projection/codebook validation, LINKED-only export, ROW_ONLY rejection precedence, missing projection/source behavior, DOI normalization and source deduplication, conflicting KU rejection, forced numeric ID collision, byte determinism, manifest hashes/metadata, CLI success/failure, and absence of partial final bundle files on pre-write CLI failure.

## Claude B2-C compatibility

Claude's B2-C scenario/evaluation work remains a separate historical/evaluation baseline. This Miner branch does not modify that app branch or its provider.

After a linked bundle is integrated into Coach retrieval on a separate app branch, reuse the B2-C evaluator to measure whether the bridge improves evidence use without gaming provenance. In particular, report:

- actual LINKED evidence utilization, not only valid reference IDs;
- source-loss confidence/safety behavior;
- missing-evidence overconfidence;
- contradiction overconfidence;
- safety preservation and drill/retest completeness;
- whether any unrelated LINKED item displaced a more relevant ROW_ONLY item; this must remain zero.

Do not use a target such as 100% LINKED. Direct metric/domain relevance remains more important than provenance status.

## Next integration task — deliberately not performed here

After active Claude app work is finished, create a separate app-repository branch that:

1. generates/reviews a small real linked bundle from private canonical data plus explicit projections;
2. vendors/imports that bundle without changing the legacy corpus;
3. merges legacy and linked candidates through the same retrieval intent/ranking path;
4. converts selected linked records to the frozen `CoachEvidenceItemV1` contract;
5. reruns the fixed retrieval benchmark and B2-C scenario/evaluation suite;
6. reports truthful linked coverage and safety regressions before any B3 training decision.

No B3 model training should be started merely because the bridge exists.

## Review state

The branch is intentionally left unmerged for owner review. No production/main integration is authorized by this handoff.
