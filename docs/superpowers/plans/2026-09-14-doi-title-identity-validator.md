# DOI–Title Identity Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Verify relevant Crossref DOI records against exact DOI metadata before candidate export.

**Architecture:** Add `source_identity.py` for title comparison and exact DOI verification. Integrate it into `run_miner` after preliminary relevance, then repeat fingerprint/relevance on canonical metadata. Extend models only for correction lineage and identity counters.

**Tech Stack:** Python 3.12, Pydantic, httpx, pytest, Ruff, Python stdlib.

**Spec:** `docs/superpowers/specs/2026-09-14-doi-title-identity-validator-design.md`

## Global Constraints
- No new runtime dependency.
- TDD: failing test before production code.
- Identity lookup failure must not abort unrelated mining.
- Existing candidate JSON stays backward compatible.
- Same DOI metadata cleanup must not fork candidate identity.
- Corrected DOI gets a new candidate ID and lineage link.

### Task 1: Pure title comparison
**Files:** create `src/basketball_miner/source_identity.py`, create `tests/test_source_identity.py`.
- [ ] Write failing tests for normalization, exact, variant, ambiguous, mismatch, author/year downgrade.
- [ ] Run `python -m pytest tests/test_source_identity.py -q` and confirm RED.
- [ ] Implement minimal comparison logic.
- [ ] Re-run and confirm GREEN.

### Task 2: Exact DOI verification
**Files:** modify `source_identity.py`, `sources/crossref.py`, tests/fixtures.
- [ ] Write failing tests for exact lookup, 429, 5xx/timeout, mismatch canonical metadata, synthesis exception.
- [ ] Confirm RED.
- [ ] Reuse Crossref item parsing and implement bounded verifier.
- [ ] Confirm GREEN with `test_source_identity.py` and `test_crossref.py`.

### Task 3: Models, lineage, collision
**Files:** modify `models.py`, `source_identity.py`, `test_models.py`, `test_source_identity.py`.
- [ ] Test old candidate JSON compatibility.
- [ ] Test `supersedes_candidate_id` lineage and corrected DOI new ID.
- [ ] Test same-DOI metadata keeps ID.
- [ ] Test duplicate title/author/year signature with different DOI is collision.
- [ ] Add default-zero identity counters.
- [ ] Confirm GREEN.

### Task 4: Pipeline integration
**Files:** modify `run.py`, `test_run.py`, `test_end_to_end_dry_run.py`.
- [ ] Write failing tests for preliminary relevance, exact verification, canonical re-check, mismatch drop/preserve, unverified warning, post-canonical dedupe, collision, counters.
- [ ] Confirm RED.
- [ ] Implement minimal pipeline integration.
- [ ] Run targeted GREEN.
- [ ] Run `python -m pytest -q`.
- [ ] Run `python -m ruff check src tests scripts`.

### Task 5: Completion evidence
- [ ] Record first RED failure.
- [ ] Record final pytest result.
- [ ] Record final Ruff result.
- [ ] Record branch/commit SHA.
- [ ] Re-fetch changed files and verify contents.
