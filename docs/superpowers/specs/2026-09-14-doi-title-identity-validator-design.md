# DOI–Title Identity Validator Design

Date: 2026-09-14
Status: Design complete; implementation requires Phase 11 approval
Scope: `Rudwpahs/miner` Crossref academic-source identity validation

## 1. Problem

The miner currently accepts Crossref search metadata as a `SourceRecord`, then performs duplicate filtering and basketball relevance classification before exporting a raw candidate. The Crossref adapter does not re-check that the DOI returned by a bibliographic search resolves to canonical metadata consistent with the returned title. This creates a failure mode where stale, duplicate, supplement-level, or misassociated metadata can produce a basketball-relevant candidate whose DOI points to a different source.

Phase 9 also exposed a related validation issue: a multi-source synthesis knowledge unit can legitimately have a synthetic title that does not match any one anchor DOI title. Therefore the validator must distinguish true single-source DOI identity checks from `MULTISOURCE_SYNTHESIS` records rather than applying one title rule to every record.

## 2. Classification and chosen approach

This is treated as an architectural-but-bounded subsystem change because it inserts a new identity-verification step into an existing collection pipeline and affects candidate lineage, counters, and tests.

Three approaches were considered:

1. **Distillation-only validation.** Keep the miner unchanged and detect DOI/title mismatches only in `hoopDB` during distillation. This is simplest but allows malformed metadata to enter the raw inbox repeatedly and makes downstream correction harder.
2. **Crossref adapter-only validation.** Re-fetch every search hit by exact DOI inside `CrossrefAdapter.fetch`. This verifies early but doubles Crossref traffic even for irrelevant records and couples source retrieval to identity policy.
3. **Pipeline identity validator after preliminary relevance — selected.** Let adapters collect metadata normally, perform a cheap preliminary basketball relevance check, then exact-resolve only relevant Crossref DOI records before final dedupe/relevance/export. This reduces extra API calls while keeping identity policy isolated and testable.

The selected design keeps the validator in a new module instead of embedding matching logic inside `crossref.py`.

## 3. New component

Add `src/basketball_miner/source_identity.py` with three responsibilities:

- `normalize_title(value: str) -> str`
- `compare_titles(observed: str, canonical: str) -> TitleIdentityResult`
- `CrossrefIdentityVerifier.verify(source: SourceRecord) -> SourceIdentityResult`

The verifier receives an injected `httpx.Client`, allowing deterministic tests with `MockTransport` and no live network dependency in the test suite.

### Identity decisions

Use these terminal decisions:

- `EXACT_MATCH`: normalized titles are identical.
- `HIGH_CONFIDENCE_VARIANT`: punctuation, Unicode, subtitle, or small editorial differences are highly likely to represent the same work.
- `AMBIGUOUS`: similarity is substantial but insufficient for automatic trust.
- `MISMATCH`: titles are materially different.
- `UNVERIFIED`: exact DOI lookup failed, was rate-limited, timed out, or returned unusable metadata.
- `NOT_APPLICABLE`: record type is intentionally exempt, including `MULTISOURCE_SYNTHESIS` validation mode.

Raw candidate collection must never convert `AMBIGUOUS`, `MISMATCH`, or `UNVERIFIED` into an evidence-quality ACCEPT decision. They remain candidate-level states only.

## 4. Exact DOI lookup

For a Crossref academic source whose `stable_id` is a DOI, call:

`GET https://api.crossref.org/works/{percent_encoded_doi}`

The exact-work response is the canonical Crossref metadata for this check.

The exact lookup should request/use at minimum:

- DOI
- title
- author
- published-online / published-print
- abstract
- URL

If exact metadata is available, construct a canonical `SourceRecord` using the same parsing rules already used by `CrossrefAdapter`.

The adapter's query result is the **observed record**. The exact DOI result is the **canonical record**.

## 5. Title normalization

Normalization must be deterministic and dependency-free:

1. HTML-unescape.
2. Unicode NFKC normalization.
3. `casefold()`.
4. Convert Unicode dash/quote variants to ordinary separators.
5. Replace punctuation with spaces while preserving letters and numbers.
6. Collapse repeated whitespace.
7. Strip leading/trailing whitespace.

Do not remove numbers, years, anatomical terms, sport terms, or other content words. Do not use a language-specific stopword list in V1.

## 6. Similarity rules

Use two independent signals from the Python standard library:

- `sequence_ratio`: `difflib.SequenceMatcher(..., autojunk=False).ratio()` on normalized full titles.
- `token_containment`: unique-token intersection divided by the smaller unique-token set.

Also calculate `length_ratio = min(len(a), len(b)) / max(len(a), len(b))`.

Decision rules:

### EXACT_MATCH

`normalized_observed == normalized_canonical`

### HIGH_CONFIDENCE_VARIANT

For titles with at least 5 tokens on the shorter side, either:

- `sequence_ratio >= 0.94`, or
- `token_containment >= 0.95` and `length_ratio >= 0.60`.

This allows common subtitle addition/removal while remaining conservative.

For titles shorter than 5 tokens, require `sequence_ratio >= 0.97`; short titles are too collision-prone for relaxed token rules.

A high-confidence variant is downgraded to `AMBIGUOUS` when both records provide author/year metadata and either:

- first-author family names conflict, or
- publication years differ by more than 1.

Missing author/year metadata does not itself cause failure.

### MISMATCH

Classify as `MISMATCH` when both are true:

- `sequence_ratio < 0.75`
- `token_containment < 0.80`

### AMBIGUOUS

Everything between the high-confidence and mismatch bands is `AMBIGUOUS`.

These thresholds are intentionally conservative and must be pinned by fixtures in Phase 11. They are policy constants, not hidden magic numbers.

## 7. Pipeline integration

Change the Crossref path in `run_miner` conceptually to:

1. Inspect source and run existing cheap preliminary duplicate/relevance checks.
2. If not basketball-relevant, do not spend an exact-DOI request.
3. If `adapter == "crossref"` and `source_type == "academic"`, run exact DOI identity verification.
4. When canonical metadata is available, use the canonical exact-DOI `SourceRecord` for final fingerprint, final relevance classification, and candidate export.
5. Re-run relevance on canonical metadata. A search hit that looked basketball-related but whose canonical DOI metadata is not basketball-related must not be exported as a basketball candidate.
6. Recompute fingerprint after canonicalization and perform a second duplicate check to catch two search hits that collapse onto the same canonical work.
7. Build `CandidateRecord` from the final canonical record.

YouTube and other non-DOI adapters bypass this validator.

## 8. Warning policy

Keep raw inbox data recoverable without pretending uncertainty is verified truth.

Candidate warning codes:

- `DOI_TITLE_VARIANT`
- `DOI_TITLE_AMBIGUOUS`
- `DOI_TITLE_MISMATCH`
- `DOI_IDENTITY_UNVERIFIED`
- `DOI_CANONICAL_METADATA_USED`
- `DOI_IDENTITY_COLLISION`
- `CORRECTED_SOURCE`

Behavior:

- `EXACT_MATCH`: export canonical record, no identity warning required.
- `HIGH_CONFIDENCE_VARIANT`: export canonical record with `DOI_TITLE_VARIANT` and `DOI_CANONICAL_METADATA_USED`.
- `AMBIGUOUS`: export canonical record with `DOI_TITLE_AMBIGUOUS`; downstream auto-promotion must treat it as non-auto-acceptable.
- `MISMATCH`: do not export the observed search metadata. If the canonical exact-DOI record itself passes basketball relevance, export only the canonical record with `DOI_TITLE_MISMATCH` and `DOI_CANONICAL_METADATA_USED`; otherwise discard the hit from basketball output.
- `UNVERIFIED`: preserve the observed raw candidate with `DOI_IDENTITY_UNVERIFIED`; it is candidate-only and must not be auto-accepted downstream.

This is fail-closed for knowledge promotion while remaining loss-tolerant for raw candidate collection.

## 9. Supplement / duplicate identity collision rule

Crossref and conference supplements can expose multiple DOI records with very similar or identical title/author/year metadata. An exact DOI lookup alone cannot prove which of two DOI records should represent a work when the provider metadata itself is duplicated.

Maintain a per-run identity signature for verified academic records:

`normalized_title + normalized_authors + publication_year`

If one signature maps to more than one DOI in the same run:

- add `DOI_IDENTITY_COLLISION` to all involved candidates;
- do not auto-promote any of them downstream;
- preserve both raw candidates for later disambiguation.

V1 does not attempt to choose a winner automatically.

The historical pattern around `10.1519/00126548-200012000-00012` motivates this regression class, but Phase 11 fixtures should use synthetic Crossref payloads rather than assert unverified facts about a live historical DOI.

## 10. `MULTISOURCE_SYNTHESIS` exception

A synthesis KU may intentionally have:

- a synthetic title;
- a non-DOI source identifier such as `MULTISOURCE_SYNTHESIS_...`;
- one or more anchor DOI sources.

Therefore title↔DOI identity validation must expose a mode parameter:

- `SINGLE_SOURCE_DOI` — normal DOI/title validation.
- `MULTISOURCE_SYNTHESIS` — return `NOT_APPLICABLE` for synthetic-title comparison and instead require at least one separately validated supporting DOI/URL.

A synthesis record must never pass merely because validation was skipped. `NOT_APPLICABLE` is only valid when its supporting sources are independently traceable.

## 11. Corrected-source lineage

Two correction cases must be distinguished.

### Metadata canonicalization, same DOI

If the DOI is the same and only canonical Crossref metadata differs, this is **not a new source**. Before export, use the canonical metadata. Do not manufacture a second candidate ID merely because punctuation, subtitle, author formatting, or stale title metadata changed.

### Source identity correction, different DOI

If downstream review later proves the original DOI itself was wrong and discovers a different correct DOI:

1. Keep the original candidate immutable and terminally reject/supersede it.
2. Create a new `SourceRecord` with the corrected DOI and canonical URL.
3. `stable_candidate_id()` naturally generates a different candidate ID because stable ID/URL changed.
4. Mark the new candidate `CORRECTED_SOURCE`.
5. Add an optional `supersedes_candidate_id` field to `CandidateRecord` so the new record can point back to the rejected original.
6. Never reuse or overwrite the original candidate ID.

`supersedes_candidate_id` is optional and defaults to `None`, preserving backward compatibility for existing JSONL.

## 12. Counters / observability

Extend `RunCounters` with default-zero fields:

- `identity_verified`
- `identity_variants`
- `identity_ambiguous`
- `identity_mismatches`
- `identity_unverified`
- `identity_collisions`

No title, URL, summary, or DOI needs to be printed in normal aggregate logs; existing redaction principles remain intact.

## 13. Network and error behavior

- Exact DOI lookup HTTP 429: do not abort the miner run; mark source `UNVERIFIED` and keep the original adapter checkpoint behavior independent.
- Exact DOI lookup 404: `UNVERIFIED` with a specific internal reason code `DOI_NOT_FOUND`.
- Exact DOI lookup 5xx/timeout: bounded retry consistent with existing Crossref behavior, then `UNVERIFIED`.
- Malformed exact-work response: `UNVERIFIED`, increment identity-unverified counter, continue run.
- One identity lookup failure must never abort unrelated adapters or later Crossref records.

## 14. Phase 11 test specification

Phase 11 must follow TDD and first demonstrate failing tests for the new behavior.

Required tests:

1. Exact normalized title match → `EXACT_MATCH`.
2. Unicode quotes/dashes/case/punctuation differences → `EXACT_MATCH` after normalization.
3. Long title with canonical subtitle addition → `HIGH_CONFIDENCE_VARIANT`.
4. One-word/short-title similarity below strict threshold → `AMBIGUOUS`, not auto-match.
5. Materially different basketball vs non-basketball titles for same returned DOI → `MISMATCH`.
6. Borderline 0.75–0.94 similarity → `AMBIGUOUS`.
7. Fuzzy title match plus conflicting first author → downgrade to `AMBIGUOUS`.
8. Fuzzy title match plus publication year difference >1 → downgrade to `AMBIGUOUS`.
9. Exact DOI lookup 429 → miner continues and exported raw candidate contains `DOI_IDENTITY_UNVERIFIED`.
10. Exact DOI lookup timeout/5xx exhaustion → same non-aborting behavior.
11. Query title mismatch but canonical exact DOI record is basketball-relevant → export canonical metadata only with mismatch warning.
12. Query title mismatch and canonical exact DOI record is not basketball-relevant → no basketball candidate exported.
13. Two different DOI values with the same title/author/year signature → both flagged `DOI_IDENTITY_COLLISION`.
14. Synthetic conference-supplement collision fixture → no automatic winner.
15. `MULTISOURCE_SYNTHESIS` mode → `NOT_APPLICABLE` only when at least one supporting source is supplied.
16. `MULTISOURCE_SYNTHESIS` without supporting source → validation failure.
17. Corrected DOI differs from rejected DOI → new candidate ID and `supersedes_candidate_id` points to original.
18. Same-DOI metadata canonicalization → candidate ID is not artificially forked.
19. Existing `CandidateRecord` JSON without `supersedes_candidate_id` still validates.
20. End-to-end dry run remains bounded and exports only final canonical/relevant candidates.
21. Full existing pytest suite passes.
22. Ruff passes on `src tests scripts`.

## 15. Files expected in Phase 11

Likely production files:

- `src/basketball_miner/source_identity.py` — new.
- `src/basketball_miner/run.py` — integrate verifier/counters/final canonicalization.
- `src/basketball_miner/models.py` — optional correction lineage field and counters.
- `src/basketball_miner/sources/crossref.py` — expose/reuse exact-item parsing helper if needed; avoid embedding policy here.

Likely tests/fixtures:

- `tests/test_source_identity.py` — new.
- `tests/test_run.py` — integration cases.
- `tests/test_models.py` — backward-compatible correction field.
- `tests/fixtures/crossref_identity_*.json` — static exact, variant, mismatch and supplement/collision payloads.

No new runtime dependency is required; use `unicodedata`, `html`, `re`, and `difflib` from the standard library.

## 16. Acceptance criteria for Phase 11

Implementation is acceptable only when:

- all 22 specified behaviors are represented by tests or explicitly combined equivalent tests;
- the test that would fail without DOI/title identity verification is observed RED before production implementation;
- exact/variant/ambiguous/mismatch/unverified decisions are deterministic;
- Crossref exact verification failures do not stop unrelated mining;
- corrected DOI sources never overwrite the original candidate ID;
- `MULTISOURCE_SYNTHESIS` does not generate false DOI-title mismatch failures;
- existing candidate JSON remains backward compatible;
- full pytest and Ruff checks pass;
- no raw candidate is promoted to training truth by this validator itself.

## 17. Out of scope

- Automatically searching the internet for the correct DOI when Crossref's exact DOI record is wrong.
- Building a general scholarly entity-resolution service across Crossref, PubMed, OpenAlex, Semantic Scholar, etc.
- Automatically resolving two real publications that genuinely share identical titles/authors.
- Changing the recurring Miner schedule or the one-off YouTube deep-dive policy.
- Implementing downstream terminal REVIEW elimination; that remains Phase 12.
