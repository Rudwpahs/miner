# Coach Provenance Bridge V1 Exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, fail-closed public exporter that converts explicitly approved canonical LINKED knowledge units plus Coach projections into a versioned linked-evidence bundle without mutating the legacy 940-unit corpus.

**Architecture:** `Rudwpahs/miner` validates only structure, source identity, controlled codes, stable IDs, collisions, and output determinism. Semantic KU→Coach code projection stays explicit/private and is never guessed from claim text. Output is `units.jsonl`, `sources.jsonl`, and `manifest.json`; a later app-repo task merges this bundle with legacy retrieval.

**Tech Stack:** Python 3.11+, Pydantic v2, hashlib/json/pathlib, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-15-provenance-bridge-v1-design.md`

## Global Constraints

- Never modify legacy Knowledge Machine V2 corpus bytes.
- Export only canonical records whose provenance status is exactly `LINKED`.
- Export only projections whose `approved` value is exactly `true`.
- Never infer domain/metric/policy/effect/evidence codes from free text.
- Unknown controlled codes fail closed.
- Stable numeric IDs use `1_000_000_000_000 + int(sha256("coach-linked-v1:" + KU_ID).hexdigest()[:10], 16)`.
- Stable ID collisions between distinct KUs fail the entire export.
- No network access is required.
- No UI, Firebase, reconstruction, MotionPacket, Coach frozen-contract, or QLoRA changes.

---

### Task 1: Freeze Bridge V1 Controlled Codes and Models

**Files:**
- Create: `config/coach_bridge_v1_codes.json`
- Create: `src/basketball_miner/distill_v3/coach_bridge.py`
- Modify: `src/basketball_miner/distill_v3/__init__.py`
- Test: `tests/test_v3_coach_bridge.py`

**Interfaces:**
- Consumes: existing Pydantic v2 dependency and V3 DOI normalization from `distill_v3.ids.normalize_doi`.
- Produces: `CoachBridgeCodebookV1`, `CanonicalLinkedKnowledgeUnitV1`, `CoachProjectionV1`, `LinkedEvidenceUnitV1`, `LinkedSourceV1`, `BridgeManifestV1`, `stable_research_unit_id()`.

- [ ] **Step 1: Add the frozen codebook fixture**

Create `config/coach_bridge_v1_codes.json` with version `coach-bridge-codes-v1`; copy the Knowledge Machine V2 domain, metric, policy, effect, and evidence-code lists exactly, then add runtime sentinels only where already approved: domain `UNCLASSIFIED`, metric `UNMAPPED_METRIC`, policy `GENERAL_GUIDANCE`.

- [ ] **Step 2: Write failing model/ID tests**

Add tests equivalent to:

```python
from basketball_miner.distill_v3.coach_bridge import CoachProjectionV1, stable_research_unit_id


def test_stable_id_is_repeatable_and_reserved():
    a = stable_research_unit_id("KU-SHOOT-DISTANCE-MILLER-1996-001")
    b = stable_research_unit_id("KU-SHOOT-DISTANCE-MILLER-1996-001")
    assert a == b
    assert 1_000_000_000_000 <= a < 2_100_000_000_000


def test_projection_is_strict_and_requires_approval():
    projection = CoachProjectionV1.model_validate({
        "schema_version": "coach-projection-v1",
        "knowledge_unit_id": "KU-SHOOT-DISTANCE-MILLER-1996-001",
        "domain_codes": ["SHOOTING", "BIOMECHANICS"],
        "metric_codes": ["JOINT_ANGLE"],
        "policy_codes": ["DO_NOT_OVERINFER"],
        "effect_code": "UNSPECIFIED",
        "evidence_code": "B",
        "projection_reason": "direct context-aware shooting evidence",
        "approved": True,
    })
    assert projection.approved is True
```

Also assert unknown extra projection keys are rejected and malformed KU IDs are rejected.

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_v3_coach_bridge.py -q`

Expected: import/module failures because bridge code does not exist.

- [ ] **Step 4: Implement strict models and stable ID derivation**

Use Pydantic models with `ConfigDict(extra="forbid")` for projection/output contracts. Canonical input may use `extra="allow"` because canonical KU files contain fields outside the bridge subset, but required bridge fields must be strict and validated.

Implement:

```python
LINKED_ID_BASE = 1_000_000_000_000


def stable_research_unit_id(knowledge_unit_id: str) -> int:
    payload = f"coach-linked-v1:{knowledge_unit_id}".encode("utf-8")
    return LINKED_ID_BASE + int(hashlib.sha256(payload).hexdigest()[:10], 16)
```

- [ ] **Step 5: Run focused tests and Ruff**

Run:
- `python -m pytest tests/test_v3_coach_bridge.py -q`
- `python -m ruff check src/basketball_miner/distill_v3/coach_bridge.py tests/test_v3_coach_bridge.py`

Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add config/coach_bridge_v1_codes.json src/basketball_miner/distill_v3/coach_bridge.py src/basketball_miner/distill_v3/__init__.py tests/test_v3_coach_bridge.py
git commit -m "feat: add provenance bridge contracts"
```

---

### Task 2: Implement Deterministic Source and Unit Export

**Files:**
- Modify: `src/basketball_miner/distill_v3/coach_bridge.py`
- Test: `tests/test_v3_coach_bridge.py`

**Interfaces:**
- Consumes: validated canonical KUs, validated projections, codebook.
- Produces: `build_linked_bundle(canonical_records, projections, codebook) -> LinkedBundleV1` with ordered units/sources and skip counters.

- [ ] **Step 1: Write failing export tests**

Create fixtures for:

1. valid Crossref LINKED KU + approved projection → one exported unit/source;
2. `ROW_ONLY` KU → skipped as `provenance_not_linked`;
3. missing title/URL → skipped with stable reason;
4. projection missing/unapproved → skipped;
5. unknown metric code → hard validation failure;
6. canonical/projection KU mismatch → no accidental join;
7. two canonical records with same KU but different payload → hard failure;
8. forced numeric-ID collision via injected ID function → hard failure.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_v3_coach_bridge.py -q`

Expected: missing exporter functions.

- [ ] **Step 3: Implement source normalization**

For DOI sources, reuse `normalize_doi` and canonicalize URL as `https://doi.org/<normalized-doi>`. For non-DOI sources, trim URL/title without resolving the network. Derive source ID from SHA-256 of normalized adapter + normalized identifier/URL, e.g. `SRC-LINKED-` plus 12 uppercase hex digits.

- [ ] **Step 4: Implement fail-closed join/export**

Join canonical records and projections only by exact `knowledge_unit_id`. Preserve canonical claim and source title. Set `provenance_code` to literal `LINKED`; never derive `ROW_ONLY` output. Copy safe-inference `may_infer`/`may_not_infer` arrays when valid and bound list lengths explicitly.

- [ ] **Step 5: Enforce deterministic ordering**

Sort units by numeric `research_unit_id`, sources by `source_id`, and skip-reason keys lexicographically. Ensure input file order cannot change output bytes.

- [ ] **Step 6: Run tests and Ruff**

Expected: focused suite PASS / clean.

- [ ] **Step 7: Commit**

```bash
git add src/basketball_miner/distill_v3/coach_bridge.py tests/test_v3_coach_bridge.py
git commit -m "feat: export linked Coach evidence bundle"
```

---

### Task 3: Add Deterministic Bundle Serialization and Manifest Hashes

**Files:**
- Modify: `src/basketball_miner/distill_v3/coach_bridge.py`
- Test: `tests/test_v3_coach_bridge.py`

**Interfaces:**
- Consumes: `LinkedBundleV1`.
- Produces: `write_linked_bundle(bundle, output_dir)` writing `units.jsonl`, `sources.jsonl`, `manifest.json` atomically.

- [ ] **Step 1: Write failing byte-determinism tests**

Generate the same logical records in reversed input order into two temporary directories and assert:

```python
assert (a / "units.jsonl").read_bytes() == (b / "units.jsonl").read_bytes()
assert (a / "sources.jsonl").read_bytes() == (b / "sources.jsonl").read_bytes()
```

Assert manifest SHA-256 values equal hashes recomputed from those exact files.

- [ ] **Step 2: Verify RED**

Run focused test.

- [ ] **Step 3: Implement canonical JSONL serialization**

Use UTF-8, `ensure_ascii=False`, compact separators, sorted object keys, and exactly one LF per record. Write temporary files in the target directory and `replace()` them only after all validation/hashing succeeds.

- [ ] **Step 4: Implement manifest**

Manifest must include schema version, ID derivation version, input counts, export counts, skip counts, source count, linked count, collision count, output hashes, and ordering rules. Do not include nondeterministic data in hashed payload files.

- [ ] **Step 5: Run focused tests and Ruff**

Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add src/basketball_miner/distill_v3/coach_bridge.py tests/test_v3_coach_bridge.py
git commit -m "feat: serialize deterministic linked evidence bundles"
```

---

### Task 4: Add Offline CLI and Synthetic Integration Fixture

**Files:**
- Create: `scripts/export_coach_linked_v1.py`
- Create: `tests/fixtures/coach_bridge/canonical.jsonl`
- Create: `tests/fixtures/coach_bridge/projections.jsonl`
- Test: `tests/test_v3_coach_bridge_cli.py`

**Interfaces:**
- Consumes CLI flags `--canonical`, `--projections`, `--codebook`, `--output`.
- Produces an offline linked bundle and exit code 0 only when export succeeds.

- [ ] **Step 1: Write failing CLI test**

Invoke the script through `subprocess.run` on the fixture pair and assert exit 0 plus exact output filenames. Add malformed projection and collision cases that must return nonzero without partial final files.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_v3_coach_bridge_cli.py -q`

- [ ] **Step 3: Implement CLI**

Read one or more canonical JSONL paths, one projection JSONL, the frozen codebook, and target directory. Print only a concise count summary; never print full private claim/source payloads on failure.

- [ ] **Step 4: Run focused tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/export_coach_linked_v1.py tests/fixtures/coach_bridge tests/test_v3_coach_bridge_cli.py
git commit -m "feat: add offline Coach provenance bridge CLI"
```

---

### Task 5: Regression, Boundary Audit, and Handoff

**Files:**
- Create: `docs/coach-provenance-bridge-v1.md`
- Modify only if needed: `.github/workflows/ci.yml` or the repository's existing Python CI workflow to include new tests.

**Interfaces:**
- Produces verification evidence and the exact handoff contract for private hoopDB generation and later Coach integration.

- [ ] **Step 1: Run full Miner tests**

Run: `python -m pytest -q`

Expected: existing suite plus bridge tests all PASS.

- [ ] **Step 2: Run full Ruff**

Run: `python -m ruff check .`

Expected: clean.

- [ ] **Step 3: Prove legacy boundaries**

Verify branch diff contains no change to mining source adapters, DOI identity semantics, Distillation V3 Judge/Audit policy, 40x budget, or any private data file. Confirm no `shooting-profile-coach-ios` or `hoopDB` repository write occurred from this branch.

- [ ] **Step 4: Document private integration contract**

Document that hoopDB must provide canonical accepted JSONL plus explicit approved projections; the exporter does not generate semantic projections. Document that app integration must remain additive and preserve B2-B.2 as a historical baseline.

- [ ] **Step 5: Commit**

```bash
git add docs/coach-provenance-bridge-v1.md .github/workflows
git commit -m "docs: hand off Coach provenance bridge v1"
```

- [ ] **Step 6: Final verification before completion**

Run fresh:
- `python -m pytest -q`
- `python -m ruff check .`
- `git diff main...HEAD --check`
- `git status --short`

Do not merge to `main`. Stop with the branch ready for owner review.

## Self-review

- Spec coverage: fail-closed provenance, stable IDs, deterministic export, manifest hashing, legacy immutability, offline operation, and later dual-source retrieval contract are all represented.
- Placeholder scan: no implementation placeholder is required to execute the public exporter tasks.
- Type consistency: all tasks use the same `CoachProjectionV1`, `LinkedBundleV1`, `build_linked_bundle`, and `write_linked_bundle` boundaries.
- Scope: this plan intentionally excludes the app-repo dual-source retrieval integration so it does not collide with active Claude/Codex work.
