# Miner Status Page v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Miner Live page a reliable, status-page-style operational dashboard that continues publishing verified Miner data even when Distillation state is missing or malformed.

**Architecture:** Keep GitHub Pages and the current server-side aggregation boundary. Replace the monolithic v1 public status object with a v2 component model (`miner`, `distillation`, `corpus`) so each private subsystem can degrade independently; keep privacy validation fail-closed for unexpected/private fields. Rework the static HTML/CSS/JS into a compact status-page layout with one overall status banner, a `System status` service list, recent collection-history segments, and incident-style degraded messaging.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, Ruff, vanilla HTML/CSS/JavaScript, GitHub Actions, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-09-28-miner-status-page-v2-design.md`

## Global Constraints

- `Rudwpahs/miner` remains public and `Rudwpahs/hoopDB` remains private.
- Browser reads only same-origin `status.json`; no browser token and no direct `hoopDB` API access.
- Candidate titles, URLs/DOIs, authors, summaries, candidate IDs, hashes, queue IDs, knowledge-unit text, private paths, and free-form private errors never enter public assets or `status.json`.
- Miner state, Distillation state, Corpus state, and page freshness are independently observable components.
- Missing/empty/malformed Distillation state must produce `pending=null`, never `0`, and must not suppress valid Miner/Corpus state.
- Missing/malformed concept index must affect only Corpus.
- Public schema version is `2` and rejects unknown keys.
- Summary statuses are `OPERATIONAL`, `DEGRADED`, `PARTIAL_OUTAGE`, `STALE`.
- Component statuses are `OPERATIONAL`, `COLLECTING`, `DELAYED`, `DEGRADED`, `UNAVAILABLE`, `UNKNOWN`.
- Safe reason codes are `NONE`, `STATE_UNAVAILABLE`, `STATE_MALFORMED`, `NO_RECENT_SUCCESS`, `PAGE_STALE`.
- Keep existing five-minute GitHub Pages reconciliation schedule.
- UI follows the status-page scanning model: overall banner → service rows → recent history/incident context; do not copy OpenAI branding or proprietary assets.

## Review Focus

1. Empty `distill.json`: page still publishes Miner and Corpus, Distillation becomes `UNAVAILABLE` with `pending=null`.
2. Valid ledger + malformed concept index: Distillation remains usable while Corpus alone becomes `UNAVAILABLE`.
3. Unknown/private output field: publication fails closed instead of silently stripping it.
4. Nullable values in browser: render `확인 불가`, not `0`, `NaN`, or a broken timestamp.
5. Stale `status.json` / failed refresh: browser visibly reports stale state without erasing the last successfully rendered metrics.

---

### Task 1: Introduce Public Status v2 Component Models

**Files:**
- Modify: `src/basketball_miner/dashboard_status.py`
- Modify: `tests/test_dashboard_status.py`

**Interfaces:**
- Produces: `MinerComponentStatus`, `DistillationComponentStatus`, `CorpusComponentStatus`, `PublicStatus(schema_version=2, ...)`.
- Produces: `build_public_status(config, collection_stats, ledger_result, concept_result, *, generated_at, last_success_at) -> PublicStatus` or equivalent narrowly-typed helper composition.
- Consumes: existing `MinerTargetConfig`, `CollectionStats`, `DistillLedger`, `ConceptIndexRecord`.

- [ ] **Step 1: Write failing schema tests**

Add tests asserting:

```python
def test_public_status_v2_has_component_objects():
    status = build_public_status(...)
    assert status.schema_version == 2
    assert status.miner.today_collected == 100
    assert status.distillation.pending == 0
    assert status.corpus.accepted_total == 0
    assert status.summary_status in {"OPERATIONAL", "DEGRADED"}
```

and:

```python
def test_unknown_nested_public_key_is_rejected():
    payload = valid_v2_payload()
    payload["distillation"]["candidate_id"] = "CAND-aaaaaaaaaaaaaaaa"
    with pytest.raises(ValueError):
        validate_public_payload(payload)
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_dashboard_status.py -k "v2 or nested_public" -v`
Expected: FAIL because v1 flat fields are still present.

- [ ] **Step 3: Implement strict Pydantic v2 component models**

Use `ConfigDict(extra="forbid")` on every public model. Component numeric fields that may be unavailable are `int | None`; status/reason fields use exact `Literal[...]` values from the spec. `PublicStatus` owns `generated_at`, `timezone`, `summary_status`, `miner`, `distillation`, `corpus`, and `history_7d` only.

- [ ] **Step 4: Preserve privacy validation across nested output**

Keep serialized forbidden-fragment checks and ensure unknown nested keys fail model validation.

- [ ] **Step 5: Run GREEN + regression**

Run: `pytest tests/test_dashboard_status.py -v`
Expected: PASS.

Run: `python -m ruff check src/basketball_miner/dashboard_status.py tests/test_dashboard_status.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/basketball_miner/dashboard_status.py tests/test_dashboard_status.py
git commit -m "feat: add component status schema v2"
```

---

### Task 2: Isolate Distillation and Corpus Failures

**Files:**
- Modify: `src/basketball_miner/dashboard_status.py`
- Modify: `tests/test_dashboard_status.py`

**Interfaces:**
- Consumes: Task 1 component models.
- Produces: independent readers/helpers for ledger state, concept-index state, and last distillation success.
- Produces: `build_status_from_store(...) -> PublicStatus` that returns a degraded but valid status when one private subsystem cannot be verified.

- [ ] **Step 1: Write failing empty-ledger test**

Replace the current fatal-ledger expectation with:

```python
def test_empty_ledger_degrades_distillation_without_hiding_miner():
    status = build_status_from_store(... store_with_empty_ledger ...)
    assert status.miner.today_collected == 100
    assert status.distillation.status == "UNAVAILABLE"
    assert status.distillation.pending is None
    assert status.distillation.reason == "STATE_UNAVAILABLE"
    assert status.summary_status == "DEGRADED"
```

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_dashboard_status.py -k "empty_ledger" -v`
Expected: FAIL because current code raises on malformed/empty ledger.

- [ ] **Step 3: Implement independent Distillation load**

Treat missing or zero-byte ledger as `STATE_UNAVAILABLE`; syntactically invalid/nonconforming ledger as `STATE_MALFORMED`. Never publish raw exception text. Valid ledger keeps existing unique `PENDING|CLAIMED + parked_review - terminal` semantics.

- [ ] **Step 4: Write failing Corpus-isolation tests**

Add:

```python
def test_valid_concept_index_survives_invalid_ledger(): ...
def test_malformed_concept_index_only_degrades_corpus(): ...
```

Assertions: valid accepted KU count remains visible when ledger fails; malformed concept index sets `corpus.accepted_total is None` without invalidating Miner or valid Distillation state.

- [ ] **Step 5: Run RED**

Run: `pytest tests/test_dashboard_status.py -k "concept_index_survives or only_degrades_corpus" -v`
Expected: FAIL until concept loading is isolated.

- [ ] **Step 6: Implement independent Corpus load and summary-state reducer**

Add a small reducer that sets:

- `PARTIAL_OUTAGE` if Miner cannot be verified;
- `DEGRADED` when Miner is healthy but Distillation or Corpus is `UNAVAILABLE|DELAYED|DEGRADED|UNKNOWN`;
- `OPERATIONAL` when all required observable components are healthy;
- preserve `STALE` for explicit page-freshness classification.

- [ ] **Step 7: Run GREEN + full dashboard tests**

Run: `pytest tests/test_dashboard_status.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/basketball_miner/dashboard_status.py tests/test_dashboard_status.py
git commit -m "fix: isolate dashboard subsystem failures"
```

---

### Task 3: Update Status Builder CLI for Degraded Publication

**Files:**
- Modify: `scripts/build_dashboard_status.py`
- Modify: `tests/test_build_dashboard_status_cli.py`

**Interfaces:**
- Consumes: Task 2 `build_status_from_store(...) -> PublicStatus`.
- Produces: atomic v2 `status.json` even when one private component is degraded.

- [ ] **Step 1: Rewrite CLI fixture to v2 and add degraded-output test**

Assert exact top-level key set equals `PublicStatus.model_fields`; assert `distillation.pending is None` can be serialized and written.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_build_dashboard_status_cli.py -v`
Expected: FAIL while fixtures still construct v1 flat `PublicStatus`.

- [ ] **Step 3: Update CLI test doubles and keep atomic write behavior**

Do not add fallback logic in the CLI that guesses metrics. The CLI trusts the v2 builder’s safe degraded object, validates it, writes a temporary file, then atomically replaces output.

- [ ] **Step 4: Preserve fail-closed publication for privacy/schema violations**

Keep the existing `last_good` preservation test, but trigger failure with `validate_public_payload`/unexpected-key privacy failure rather than ordinary missing Distillation state.

- [ ] **Step 5: Run GREEN**

Run: `pytest tests/test_build_dashboard_status_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_dashboard_status.py tests/test_build_dashboard_status_cli.py
git commit -m "fix: publish degraded status snapshots safely"
```

---

### Task 4: Replace Metric-Card Dashboard with Status-Page UI

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard_assets.py`

**Interfaces:**
- Consumes: v2 same-origin `status.json` from Tasks 1–3.
- Produces: DOM targets for overall banner, Miner row, Distillation row, Corpus row, history segments, generated-at timestamp, refresh action, and incident/degraded message.

- [ ] **Step 1: Write failing structural asset tests**

Add assertions for IDs:

```python
for target in (
    'overall-status', 'overall-message', 'miner-status', 'miner-today',
    'miner-total', 'distillation-status', 'distillation-pending',
    'corpus-status', 'corpus-total', 'history-strip', 'generated-at',
    'refresh-status'
):
    assert f'id="{target}"' in html
```

Also assert the old three-large-card class/markup is absent.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_dashboard_assets.py -v`
Expected: FAIL because current HTML is metric-card based.

- [ ] **Step 3: Implement new semantic HTML**

Layout order:

1. compact header (`HoopHub Status`, refresh button);
2. overall status banner;
3. `System status` bordered panel with Miner / Distillation / Corpus rows;
4. degraded/incident message area hidden when fully operational;
5. recent history strip/list;
6. generated-at footer.

- [ ] **Step 4: Write failing JS behavior tests as static contracts**

Assert `app.js` contains safe-null formatting and mappings for `OPERATIONAL`, `DEGRADED`, `PARTIAL_OUTAGE`, `STALE`, plus `확인 불가`; assert same-origin fetch remains the only fetch target.

- [ ] **Step 5: Run RED**

Run: `pytest tests/test_dashboard_assets.py -k "null or status or origin" -v`
Expected: FAIL until v2 renderer is implemented.

- [ ] **Step 6: Implement v2 renderer**

Use helper functions for:

- nullable integer → localized number or `확인 불가`;
- nullable timestamp → formatted local time or `—`;
- component status → visible label + `data-state`;
- summary status → Korean headline/message;
- refresh button → call `refresh()` without navigation;
- fetch failure → retain existing rendered metrics, mark overall state `STALE`, update generated-at label to `갱신 실패`.

- [ ] **Step 7: Implement restrained status-page CSS**

Use neutral background, centered max-width around 860px, thin borders, moderate radius, service-row dividers, compact typography, and state colors only for indicators/badges. Mobile breakpoint collapses row metadata without horizontal scrolling.

- [ ] **Step 8: Run GREEN**

Run: `pytest tests/test_dashboard_assets.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard_assets.py
git commit -m "feat: redesign Miner Live as status page"
```

---

### Task 5: Verify Workflow Contract and End-to-End Regression

**Files:**
- Modify only if required: `.github/workflows/dashboard.yml`
- Modify only if required: `tests/test_workflow_contracts.py`
- Modify: `README.md` only if displayed schema/status descriptions are now stale.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: deployable `_site` with static assets + v2 `status.json`, still refreshed every five minutes.

- [ ] **Step 1: Add/adjust workflow contract test only if v2 needs a new explicit assertion**

Required invariants remain:

```python
assert 'cron: "*/5 * * * *"' in dashboard_workflow
assert "_site/status.json" in dashboard_workflow
assert "ml/coach/miner-data/inbox" not in dashboard_workflow
```

- [ ] **Step 2: Run workflow contract tests**

Run: `pytest tests/test_workflow_contracts.py -v`
Expected: PASS without workflow change unless the existing workflow cannot publish the v2 builder output.

- [ ] **Step 3: Run complete test suite**

Run: `python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 4: Run lint**

Run: `python -m ruff check src tests scripts`
Expected: PASS.

- [ ] **Step 5: Review privacy boundary manually**

Search changed public assets/output schema for forbidden strings: `candidate_id`, `canonical_hash`, `CAND-`, `http://`, `https://`, `HOOPHUB_MINER_TOKEN`, private repository paths. Expected: no forbidden candidate material in public assets/schema.

- [ ] **Step 6: Update README only if needed**

Change Miner Live documentation from flat v1 metric names to component-oriented v2 status semantics; keep private/public boundary wording intact.

- [ ] **Step 7: Commit final integration changes**

```bash
git add .github/workflows/dashboard.yml tests/test_workflow_contracts.py README.md
git commit -m "docs: align Miner Live status v2 contracts"
```

- [ ] **Step 8: Final branch review**

Compare `feat/miner-status-page-v2` against `main`; verify only status-page/spec/plan-related changes are present and no production Miner collection semantics changed.
