# Miner Daily Target + Live Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Miner target a configurable 1,659 unique exports per Seoul day and publish a privacy-safe near-live dashboard for collected, pending-distillation, and accepted-distillation counts.

**Architecture:** Keep candidate payloads in private `Rudwpahs/hoopDB`; keep only numeric collection state in the public `miner-state` branch. Scheduled Miner wakes every 20 minutes, reads one target config value, caps exports to the remaining quota, and persists numeric collection stats beside existing safe checkpoints. A separate dashboard workflow reads numeric collection stats plus only the private V3 ledger/concept index server-side, validates an explicit public schema, and deploys static HTML/CSS/JS plus `status.json` to GitHub Pages.

**Tech Stack:** Python 3.12, Pydantic 2, httpx, pytest, GitHub Actions, vanilla HTML/CSS/JavaScript, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-09-19-miner-daily-target-live-dashboard-design.md`

## Global Constraints

- Initial daily target is **1,659** and must be controlled only by `config/miner_target.json`.
- Timezone for quota boundaries is exactly `Asia/Seoul`.
- Raw candidate titles, URLs/DOIs, authors, summaries, candidate IDs, canonical hashes, queue batch IDs, and knowledge-unit text must never enter the public dashboard payload or page assets.
- Existing relevance filtering, DOI identity verification, and global deduplication semantics remain unchanged.
- Scheduled Miner wakes every 20 minutes but must stop before source API calls when the configured target is already reached.
- Manual `--no-export` runs must not consume or modify the daily quota.
- `distillation_pending` is the unique union of ledger candidate states with status `PENDING` or `CLAIMED` plus `parked_review_candidate_ids`.
- `distillation_success` is the count of concept-index records whose status is exactly `ACCEPTED`; Judge `CONFIRM` or `PROPOSE_ACCEPT` alone is not success.
- Public status publication is allowlist-only and fails closed on malformed/unknown data.
- Dashboard refresh target is near-live: after Miner/V3 prepare work plus a 5-minute reconciliation schedule.
- No new runtime dependency beyond the existing Python dependencies is required for the static dashboard.

## Review Focus

1. **State persistence fails after private export:** the next run must re-read the old checkpoint safely; duplicate private exports must not inflate public unique totals once bootstrap/reconciliation deduplicates candidate IDs.
2. **Quota has only a few slots left:** source request size must be bounded by the remaining export quota so a final batch cannot overshoot or advance past unexported source records.
3. **Seoul midnight boundary:** yesterday's `today_collected` must roll into history while `collected_total` stays monotonic and the new day starts at zero.
4. **Malformed/private V3 state:** dashboard generation must retain/fail to publish rather than emit guessed counts or leak raw fields.
5. **Public payload drift:** any unexpected key or candidate-like identifier must make validation fail before Pages upload.

---

## File Structure

### New files

- `config/miner_target.json` — single operator-facing daily target and timezone.
- `src/basketball_miner/collection_stats.py` — target config, numeric collection state, quota math, Seoul rollover, one-time private inbox bootstrap.
- `src/basketball_miner/dashboard_status.py` — private V3 aggregate reduction and strict public status schema.
- `scripts/build_dashboard_status.py` — server-side CLI that reads `miner-state` stats and private V3 state, then writes safe `status.json`.
- `dashboard/index.html` — mobile-first public shell.
- `dashboard/app.js` — polls `status.json` and renders counters/chart.
- `dashboard/styles.css` — responsive presentation.
- `.github/workflows/dashboard.yml` — 5-minute/reusable Pages build + deploy.
- `tests/test_collection_stats.py` — target/state/bootstrap tests.
- `tests/test_dashboard_status.py` — aggregate semantics and privacy-contract tests.
- `tests/test_dashboard_assets.py` — static UI contract/privacy tests.
- `tests/test_workflow_contracts.py` — cron/config/reusable dashboard contract tests.

### Modified files

- `src/basketball_miner/run.py` — add `max_exports` cap without changing relevance/dedup logic.
- `scripts/run_miner.py` — load target + numeric state, bootstrap when absent, pass remaining quota, persist updated stats.
- `.github/workflows/mine.yml` — 20-minute schedule, load/persist `collection_stats.json`, trigger reusable dashboard refresh.
- `.github/workflows/distill_v3_prepare.yml` — trigger reusable dashboard refresh after shadow preparation.
- `README.md` — document target knob and public dashboard.

---

### Task 1: Target Config and Numeric Collection State

**Files:**
- Create: `config/miner_target.json`
- Create: `src/basketball_miner/collection_stats.py`
- Create: `tests/test_collection_stats.py`

**Interfaces:**
- Produces: `MinerTargetConfig`, `DailyCount`, `CollectionStats`, `load_target_config(path)`, `load_collection_stats(path, now)`, `remaining_target(config, stats)`, `apply_export(stats, exported, run_at)`.
- Later tasks consume these exact names from `basketball_miner.collection_stats`.

- [ ] **Step 1: Write failing config/state tests**

```python
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from basketball_miner.collection_stats import (
    CollectionStats,
    apply_export,
    load_collection_stats,
    load_target_config,
    remaining_target,
)

KST = ZoneInfo("Asia/Seoul")


def test_target_config_loads_single_operator_value(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text('{"daily_target":1659,"timezone":"Asia/Seoul"}', encoding="utf-8")
    config = load_target_config(path)
    assert config.daily_target == 1659
    assert config.timezone == "Asia/Seoul"


def test_non_positive_target_is_rejected(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text('{"daily_target":0,"timezone":"Asia/Seoul"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_config(path)


def test_seoul_midnight_resets_today_but_preserves_total(tmp_path: Path):
    path = tmp_path / "collection_stats.json"
    path.write_text(
        '{"schema_version":1,"date":"2026-09-19","today_collected":1659,'
        '"collected_total":2400,"daily_counts":{"2026-09-19":1659},'
        '"last_miner_run_at":"2026-09-19T23:58:00+09:00"}',
        encoding="utf-8",
    )
    stats = load_collection_stats(path, datetime(2026, 9, 20, 0, 1, tzinfo=KST))
    assert stats.date == "2026-09-20"
    assert stats.today_collected == 0
    assert stats.collected_total == 2400
    assert stats.daily_counts["2026-09-19"] == 1659


def test_apply_export_updates_daily_and_total_counts():
    stats = CollectionStats.empty("2026-09-19")
    updated = apply_export(stats, 41, datetime(2026, 9, 19, 18, 0, tzinfo=KST))
    assert updated.today_collected == 41
    assert updated.collected_total == 41
    assert updated.daily_counts == {"2026-09-19": 41}
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
pytest tests/test_collection_stats.py -v
```

Expected: collection-stats imports fail because the module does not exist.

- [ ] **Step 3: Add the single config value**

Create `config/miner_target.json` exactly as:

```json
{
  "daily_target": 1659,
  "timezone": "Asia/Seoul"
}
```

- [ ] **Step 4: Implement strict target/state models and quota math**

Implement the following public surface in `collection_stats.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MinerTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_target: int = Field(gt=0, le=100_000)
    timezone: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if value != "Asia/Seoul":
            raise ValueError("timezone must be Asia/Seoul")
        ZoneInfo(value)
        return value


class CollectionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    date: str
    today_collected: int = Field(ge=0)
    collected_total: int = Field(ge=0)
    daily_counts: dict[str, int]
    last_miner_run_at: str | None = None

    @classmethod
    def empty(cls, date: str) -> "CollectionStats":
        return cls(date=date, today_collected=0, collected_total=0, daily_counts={})


def load_target_config(path: Path) -> MinerTargetConfig:
    return MinerTargetConfig.model_validate_json(path.read_text(encoding="utf-8"))


def remaining_target(config: MinerTargetConfig, stats: CollectionStats) -> int:
    return max(0, config.daily_target - stats.today_collected)
```

Implement `load_collection_stats` so missing files return `CollectionStats.empty(today)`, and a date change keeps `collected_total`/history but sets `today_collected=0`. Keep only the most recent 7 calendar-day entries in `daily_counts` after rollover/apply.

Implement `apply_export` as an immutable copy update; reject negative `exported`.

- [ ] **Step 5: Add Review Focus test for history pruning and invalid persisted counts**

```python
def test_collection_state_rejects_negative_counts(tmp_path: Path):
    path = tmp_path / "collection_stats.json"
    path.write_text(
        '{"schema_version":1,"date":"2026-09-19","today_collected":-1,'
        '"collected_total":0,"daily_counts":{},"last_miner_run_at":null}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_collection_stats(path, datetime(2026, 9, 19, 12, 0, tzinfo=KST))
```

- [ ] **Step 6: Run tests GREEN**

```bash
pytest tests/test_collection_stats.py -v
ruff check src/basketball_miner/collection_stats.py tests/test_collection_stats.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add config/miner_target.json src/basketball_miner/collection_stats.py tests/test_collection_stats.py
git commit -m "feat: add configurable daily miner target"
```

---

### Task 2: Hard Export Cap in the Miner Core

**Files:**
- Modify: `src/basketball_miner/run.py`
- Modify: `tests/test_run.py`

**Interfaces:**
- Consumes: existing `run_miner(...)` behavior.
- Produces: `run_miner(..., max_exports: int | None = None) -> RunCounters`.
- `max_exports=0` performs zero adapter fetches; `None` preserves current unlimited-within-budget behavior.

- [ ] **Step 1: Add failing cap tests**

Use the existing fake adapter/sink patterns in `tests/test_run.py` and add:

```python
def test_zero_export_cap_never_calls_adapter():
    adapter = CountingAdapter(records=[source_record("1"), source_record("2")])
    sink = RecordingSink()
    counters = run_miner([adapter], sink, budget=100, max_exports=0)
    assert adapter.fetch_calls == 0
    assert counters.exported == 0


def test_final_source_request_is_bounded_by_remaining_exports():
    adapter = CountingAdapter(records=[source_record(str(i)) for i in range(20)])
    sink = RecordingSink()
    counters = run_miner([adapter], sink, budget=100, chunk_size=50, max_exports=3)
    assert counters.exported == 3
    assert sum(len(batch) for batch in sink.batches) == 3
    assert adapter.request_limits[0] == 3
```

The second test specifically prevents the dangerous implementation where 50 source records are fetched/checkpointed and only 3 are exported.

- [ ] **Step 2: Verify RED**

```bash
pytest tests/test_run.py -k "export_cap or remaining_exports" -v
```

Expected: FAIL because `max_exports` is not accepted.

- [ ] **Step 3: Implement minimal cap without slicing a fetched batch**

Change the signature:

```python
def run_miner(
    adapters: list[SourceAdapter],
    sink: CandidateSink,
    budget: int = 500,
    *,
    checkpoints: dict[str, Checkpoint] | None = None,
    seen_hashes: set[str] | None = None,
    seen_crossref_dois: set[str] | None = None,
    chunk_size: int = 50,
    run_id: str | None = None,
    identity_verifier: SourceIdentityVerifier | None = None,
    max_exports: int | None = None,
) -> RunCounters:
```

Validation:

```python
if max_exports is not None and max_exports < 0:
    raise ValueError("max_exports must be >= 0")
```

At the outer/inner loop guards, stop when `exported >= max_exports` when a cap exists. Before `adapter.fetch`, calculate:

```python
remaining_exports = None if max_exports is None else max_exports - exported
if remaining_exports == 0:
    break
request_limit = min(chunk_size, budget - inspected)
if remaining_exports is not None:
    request_limit = min(request_limit, remaining_exports)
```

Do **not** fetch more source records and then slice `candidates`; bounding `request_limit` preserves checkpoint correctness.

- [ ] **Step 4: Add negative-cap validation test**

```python
def test_negative_export_cap_is_rejected():
    with pytest.raises(ValueError, match="max_exports"):
        run_miner([], RecordingSink(), max_exports=-1)
```

- [ ] **Step 5: Run focused and full miner tests GREEN**

```bash
pytest tests/test_run.py -v
pytest tests/test_end_to_end_dry_run.py tests/test_source_identity.py -v
```

Expected: PASS with existing behavior unchanged when `max_exports=None`.

- [ ] **Step 6: Commit**

```bash
git add src/basketball_miner/run.py tests/test_run.py
git commit -m "feat: cap miner exports without batch overshoot"
```

---

### Task 3: Bootstrap Historical Counts and Persist Quota State

**Files:**
- Modify: `src/basketball_miner/collection_stats.py`
- Modify: `scripts/run_miner.py`
- Modify: `.github/workflows/mine.yml`
- Modify: `tests/test_collection_stats.py`
- Create: `tests/test_run_miner_cli.py`

**Interfaces:**
- Produces: `bootstrap_collection_stats(store, now) -> CollectionStats` where `store` supports `list_dir(path)` and `read_file(path)` like `GitHubV3Store`.
- Scheduled `run_miner.py` writes `_state_next/collection_stats.json` alongside the existing four safe state files.

- [ ] **Step 1: Write failing bootstrap test with private-data fixtures**

```python
def test_bootstrap_counts_unique_candidate_ids_and_first_seen_day():
    store = FakeStore(
        {
            "ml/coach/miner-data/inbox/2026/09/18/a.jsonl": (
                b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa"}\n'
                b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb"}\n'
            ),
            "ml/coach/miner-data/inbox/2026/09/19/b.jsonl": (
                b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb"}\n'
                b'{"candidate_id":"CAND-cccccccccccccccc"}\n'
            ),
        }
    )
    stats = bootstrap_collection_stats(store, datetime(2026, 9, 19, 12, 0, tzinfo=KST))
    assert stats.collected_total == 3
    assert stats.today_collected == 1
    assert stats.daily_counts == {"2026-09-18": 2, "2026-09-19": 1}
```

Include malformed JSON and missing `candidate_id` tests; bootstrap must raise instead of guessing.

- [ ] **Step 2: Verify bootstrap tests RED**

```bash
pytest tests/test_collection_stats.py -k bootstrap -v
```

Expected: FAIL because bootstrap is absent.

- [ ] **Step 3: Implement recursive private inbox bootstrap**

Implement traversal only under:

```python
INBOX_ROOT = "ml/coach/miner-data/inbox"
```

Rules:

```python
# Traverse YYYY/MM/DD in lexical order.
# Parse each nonblank JSONL line as dict.
# candidate_id must match ^CAND-[0-9a-f]{16}$.
# Count each candidate ID only on its first chronological occurrence.
# Return numeric CollectionStats only; never return/store row payloads.
```

This path is used only when `collection_stats.json` is missing/incompatible; normal 20-minute runs do not rescan history.

- [ ] **Step 4: Write failing CLI quota tests**

Monkeypatch `run_miner`, `ensure_private_repo`, and the store/bootstrap functions:

```python
def test_scheduled_export_passes_only_remaining_quota(monkeypatch, tmp_path):
    # target 100; persisted today count 91 -> core receives max_exports=9
    ...
    assert captured["max_exports"] == 9


def test_target_reached_skips_semantic_source_work(monkeypatch, tmp_path):
    # target 100; today count 100 -> run_miner not called
    ...
    assert captured["called"] is False


def test_no_export_dry_run_does_not_write_collection_stats(monkeypatch, tmp_path):
    ...
    assert not (tmp_path / "next" / "collection_stats.json").exists()
```

- [ ] **Step 5: Modify CLI to load/bootstrap quota before source work**

Add CLI arguments:

```python
parser.add_argument("--target-config", type=Path, default=ROOT / "config" / "miner_target.json")
```

Scheduled export flow:

```python
config = load_target_config(args.target_config)
now = datetime.now(ZoneInfo(config.timezone))
collection_path = args.state_dir / "collection_stats.json"
if collection_path.exists():
    collection_stats = load_collection_stats(collection_path, now)
else:
    store = GitHubV3Store(target_repo, target_branch, token)
    collection_stats = bootstrap_collection_stats(store, now)
remaining = remaining_target(config, collection_stats)
if remaining == 0:
    _save_state(..., collection_stats=collection_stats)
    print(_safe_summary(...))
    return 0
counters = run_miner(..., max_exports=remaining)
collection_stats = apply_export(collection_stats, counters.exported, now)
_save_state(..., collection_stats=collection_stats)
```

The dry-run branch continues to call `run_miner` without `max_exports` and never writes next collection state.

- [ ] **Step 6: Update state persistence in `mine.yml` and schedule**

Change schedule to:

```yaml
on:
  schedule:
    - cron: "7,27,47 * * * *"
```

Persist five safe files instead of four:

```bash
test -f _state_next/collection_stats.json
test "$(find _state_next -type f | wc -l)" -eq 5
cp _state_next/collection_stats.json state/collection_stats.json
git add state/crossref.json state/youtube_rss.json state/seen_hashes.jsonl \
  state/seen_crossref_dois.jsonl state/collection_stats.json
```

Load `state/collection_stats.json` conditionally from `miner-state` because the first deployment will bootstrap it when absent.

- [ ] **Step 7: Run Task 3 tests GREEN**

```bash
pytest tests/test_collection_stats.py tests/test_run_miner_cli.py -v
pytest tests/test_end_to_end_dry_run.py tests/test_export.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/basketball_miner/collection_stats.py scripts/run_miner.py .github/workflows/mine.yml \
  tests/test_collection_stats.py tests/test_run_miner_cli.py
git commit -m "feat: enforce daily miner quota with persistent stats"
```

---

### Task 4: Strict Public Dashboard Status Aggregator

**Files:**
- Create: `src/basketball_miner/dashboard_status.py`
- Create: `scripts/build_dashboard_status.py`
- Create: `tests/test_dashboard_status.py`

**Interfaces:**
- Consumes: `CollectionStats`, private `DistillLedger`, private concept-index JSONL.
- Produces: `PublicStatus`, `build_public_status(...)`, `parse_concept_index(content)`, `validate_public_payload(payload)`.

- [ ] **Step 1: Write failing metric-semantics tests**

```python
from basketball_miner.dashboard_status import build_public_status
from basketball_miner.distill_v3.ledger import DistillLedger
from basketball_miner.distill_v3.models import CandidateStageState


def test_pending_is_union_of_active_and_parked_candidates():
    ledger = DistillLedger(
        candidate_states={
            "CAND-1111111111111111": CandidateStageState(
                candidate_id="CAND-1111111111111111",
                source_fingerprint="1" * 64,
                source_type="academic",
                stage="TRIAGE",
                status="PENDING",
                updated_at="2026-09-19T00:00:00Z",
            ),
            "CAND-2222222222222222": CandidateStageState(
                candidate_id="CAND-2222222222222222",
                source_fingerprint="2" * 64,
                source_type="academic",
                stage="REVIEW",
                status="COMPLETE",
                updated_at="2026-09-19T00:00:00Z",
            ),
        },
        parked_review_candidate_ids={"CAND-2222222222222222"},
    )
    status = build_public_status(..., ledger=ledger, concept_rows=[])
    assert status.distillation_pending == 2


def test_success_counts_only_accepted_concept_index_records():
    rows = [
        {"knowledge_unit_id":"KU-1","status":"ACCEPTED"},
        {"knowledge_unit_id":"KU-2","status":"REVIEW"},
    ]
    status = build_public_status(..., concept_rows=rows)
    assert status.distillation_success == 1
```

- [ ] **Step 2: Write failing privacy/allowlist tests**

```python
def test_public_payload_rejects_unknown_private_field():
    payload = valid_public_payload()
    payload["candidate_id"] = "CAND-aaaaaaaaaaaaaaaa"
    with pytest.raises(ValueError):
        validate_public_payload(payload)


def test_public_payload_contains_no_candidate_identifier_pattern():
    payload = valid_public_payload()
    rendered = json.dumps(payload, sort_keys=True)
    assert "CAND-" not in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered
```

- [ ] **Step 3: Verify RED**

```bash
pytest tests/test_dashboard_status.py -v
```

Expected: FAIL because module is absent.

- [ ] **Step 4: Implement the public schema as `extra="forbid"` models**

Use:

```python
class HistoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    collected: int = Field(ge=0)


class PublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    generated_at: str
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    daily_target: int = Field(gt=0)
    collected_total: int = Field(ge=0)
    distillation_pending: int = Field(ge=0)
    distillation_success: int = Field(ge=0)
    today_collected: int = Field(ge=0)
    last_miner_run_at: str | None
    last_distillation_success_at: str | None
    system_status: Literal["COLLECTING", "TARGET_REACHED", "DISTILLING", "BLOCKED", "DEGRADED"]
    history_7d: list[HistoryPoint]
```

Pending implementation:

```python
active = {
    candidate_id
    for candidate_id, state in ledger.candidate_states.items()
    if state.status in {"PENDING", "CLAIMED"}
}
pending = active | set(ledger.parked_review_candidate_ids)
```

Success implementation:

```python
accepted_ku_ids = {
    str(row["knowledge_unit_id"])
    for row in concept_rows
    if row.get("status") == "ACCEPTED"
}
success = len(accepted_ku_ids)
```

Reject duplicate accepted KU IDs only by set-deduping; reject rows without string `knowledge_unit_id` or unsupported status.

- [ ] **Step 5: Implement server-side builder CLI**

`scripts/build_dashboard_status.py` must:

1. load `config/miner_target.json`;
2. load `state/collection_stats.json` from a supplied local path (the workflow checks out `miner-state` separately);
3. construct `GitHubV3Store("Rudwpahs/hoopDB", "main", HOOPHUB_MINER_TOKEN)`;
4. read `ledger_path()` and `concept_index_path()` only;
5. parse them into `DistillLedger` + concept rows;
6. derive the latest successful audit timestamp by checking the newest available V3 audit run metadata only; if no successful audit exists, use `null` rather than guessing;
7. call `PublicStatus.model_dump(mode="json")` and write only validated JSON to the requested output path.

CLI surface:

```bash
python scripts/build_dashboard_status.py \
  --collection-state _miner_state/state/collection_stats.json \
  --output _site/status.json
```

- [ ] **Step 6: Add malformed-private-state fail-closed tests**

```python
def test_missing_ledger_fails_closed(fake_store):
    fake_store.files.pop("ml/coach/miner-data/v3/ledgers/distill.json", None)
    with pytest.raises(RuntimeError, match="ledger"):
        build_status_from_store(...)


def test_malformed_concept_index_fails_closed():
    with pytest.raises(ValueError):
        parse_concept_index(b'{"status":"ACCEPTED","candidate_id":"CAND-aaaaaaaaaaaaaaaa"}\n')
```

- [ ] **Step 7: Run aggregator tests GREEN**

```bash
pytest tests/test_dashboard_status.py -v
ruff check src/basketball_miner/dashboard_status.py scripts/build_dashboard_status.py tests/test_dashboard_status.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/basketball_miner/dashboard_status.py scripts/build_dashboard_status.py tests/test_dashboard_status.py
git commit -m "feat: build privacy-safe live miner metrics"
```

---

### Task 5: Mobile-First Static Dashboard

**Files:**
- Create: `dashboard/index.html`
- Create: `dashboard/app.js`
- Create: `dashboard/styles.css`
- Create: `tests/test_dashboard_assets.py`

**Interfaces:**
- Consumes: same-origin `status.json` matching `PublicStatus`.
- Browser performs no GitHub API calls and contains no credentials.

- [ ] **Step 1: Write failing asset-contract tests**

```python
from pathlib import Path


def test_dashboard_has_three_primary_metric_slots():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="collected-total"' in html
    assert 'id="distillation-pending"' in html
    assert 'id="distillation-success"' in html


def test_dashboard_fetches_only_same_origin_status_json():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert 'fetch(`status.json?' in js
    assert "api.github.com" not in js
    assert "HOOPHUB_MINER_TOKEN" not in js


def test_target_value_is_not_hard_coded_in_dashboard_assets():
    assets = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ["dashboard/index.html", "dashboard/app.js", "dashboard/styles.css"]
    )
    assert "1659" not in assets
```

- [ ] **Step 2: Verify RED**

```bash
pytest tests/test_dashboard_assets.py -v
```

Expected: FAIL because dashboard files do not exist.

- [ ] **Step 3: Build semantic HTML shell**

Required IDs:

```html
<main class="dashboard-shell">
  <header>
    <p class="eyebrow">HOOPHUB DATA PIPELINE</p>
    <h1>Miner Live</h1>
    <p id="system-status" aria-live="polite">Loading…</p>
  </header>
  <section class="metric-grid" aria-label="핵심 지표">
    <article><span>수집 데이터</span><strong id="collected-total">—</strong></article>
    <article><span>증류 대기</span><strong id="distillation-pending">—</strong></article>
    <article><span>증류 성공</span><strong id="distillation-success">—</strong></article>
  </section>
  <section aria-label="오늘 수집 진행률">
    <div><span id="today-progress-label">—</span></div>
    <progress id="today-progress" value="0" max="1"></progress>
  </section>
  <section aria-label="최근 7일 수집량">
    <div id="history-chart"></div>
  </section>
  <footer>
    <time id="last-miner-run">—</time>
    <time id="last-distillation-success">—</time>
    <time id="generated-at">—</time>
  </footer>
</main>
```

- [ ] **Step 4: Implement polling/rendering in vanilla JS**

Core behavior:

```javascript
const number = new Intl.NumberFormat("ko-KR");

async function refresh() {
  const response = await fetch(`status.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`status ${response.status}`);
  const status = await response.json();
  document.querySelector("#collected-total").textContent = number.format(status.collected_total);
  document.querySelector("#distillation-pending").textContent = number.format(status.distillation_pending);
  document.querySelector("#distillation-success").textContent = number.format(status.distillation_success);
  document.querySelector("#today-progress-label").textContent =
    `${number.format(status.today_collected)} / ${number.format(status.daily_target)}`;
  const progress = document.querySelector("#today-progress");
  progress.max = status.daily_target;
  progress.value = Math.min(status.today_collected, status.daily_target);
  renderHistory(status.history_7d);
  renderTimes(status);
}

refresh().catch(renderError);
setInterval(() => refresh().catch(renderError), 60_000);
```

`renderHistory` creates simple accessible CSS bars using date/count text; no chart dependency.

- [ ] **Step 5: Implement responsive CSS**

Requirements:

```css
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
.metric-grid strong { font-variant-numeric: tabular-nums; }
@media (max-width: 720px) {
  .metric-grid { grid-template-columns: 1fr; }
}
```

Do not encode private identifiers in classes/data attributes/tooltips.

- [ ] **Step 6: Add stale/error behavior test**

Assert `app.js` contains a visible error path and generated-time rendering; the UI must not silently retain a green/healthy status when fetch fails.

- [ ] **Step 7: Run asset tests GREEN**

```bash
pytest tests/test_dashboard_assets.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dashboard tests/test_dashboard_assets.py
git commit -m "feat: add public miner live dashboard"
```

---

### Task 6: GitHub Pages Reconciliation and Event Refresh

**Files:**
- Create: `.github/workflows/dashboard.yml`
- Modify: `.github/workflows/mine.yml`
- Modify: `.github/workflows/distill_v3_prepare.yml`
- Create: `tests/test_workflow_contracts.py`

**Interfaces:**
- `dashboard.yml` supports `schedule`, `workflow_dispatch`, and `workflow_call`.
- Callers pass `HOOPHUB_MINER_TOKEN` with `secrets: inherit`.

- [ ] **Step 1: Write failing workflow-contract tests**

```python
import re
from pathlib import Path


def test_miner_wakes_every_twenty_minutes_and_no_literal_target():
    text = Path(".github/workflows/mine.yml").read_text(encoding="utf-8")
    assert 'cron: "7,27,47 * * * *"' in text
    assert "1659" not in text


def test_dashboard_reconciles_every_five_minutes():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert 'cron: "*/5 * * * *"' in text
    assert "workflow_call:" in text


def test_dashboard_deploy_happens_only_after_safe_status_build():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert "build_dashboard_status.py" in text
    assert text.index("build_dashboard_status.py") < text.index("upload-pages-artifact")
```

- [ ] **Step 2: Verify RED**

```bash
pytest tests/test_workflow_contracts.py -v
```

Expected: FAIL because dashboard workflow/caller hooks are absent.

- [ ] **Step 3: Create reusable/scheduled dashboard workflow**

Required trigger shape:

```yaml
name: Miner Live Dashboard

on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:
  workflow_call:
    secrets:
      HOOPHUB_MINER_TOKEN:
        required: true

concurrency:
  group: miner-live-dashboard
  cancel-in-progress: true
```

Job permissions:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

Build steps, in this order:

```yaml
- checkout main
- checkout miner-state into _miner_state
- setup Python 3.12
- install package
- mkdir -p _site
- cp dashboard/index.html dashboard/app.js dashboard/styles.css _site/
- run python scripts/build_dashboard_status.py --collection-state _miner_state/state/collection_stats.json --output _site/status.json
- actions/configure-pages
- actions/upload-pages-artifact with path _site
- actions/deploy-pages
```

The private token appears only in the environment of the status-builder step.

- [ ] **Step 4: Trigger dashboard after Miner state persistence**

Add to `mine.yml`:

```yaml
  refresh-dashboard:
    needs: persist-state
    if: github.event_name == 'schedule' || inputs.export == true
    uses: ./.github/workflows/dashboard.yml
    secrets: inherit
```

- [ ] **Step 5: Trigger dashboard after V3 prepare**

Add to `distill_v3_prepare.yml`:

```yaml
  refresh-dashboard:
    needs: prepare-shadow
    if: github.event_name == 'schedule' || inputs.write_shadow == true
    uses: ./.github/workflows/dashboard.yml
    secrets: inherit
```

If GitHub rejects caller/callee permission inheritance in validation, keep the 5-minute dashboard schedule as the authoritative refresh and replace these two reusable calls with same-repo `workflow_dispatch` REST calls using `actions: write`; do not duplicate dashboard build logic in three workflows.

- [ ] **Step 6: Add privacy workflow checks**

Tests assert dashboard workflow never copies `ml/coach/miner-data/inbox` or whole `hoopDB` checkout into `_site`, and no token-like environment variable is referenced in dashboard JS/assets.

- [ ] **Step 7: Run workflow tests GREEN**

```bash
pytest tests/test_workflow_contracts.py tests/test_dashboard_assets.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/dashboard.yml .github/workflows/mine.yml \
  .github/workflows/distill_v3_prepare.yml tests/test_workflow_contracts.py
git commit -m "ci: publish near-live miner dashboard"
```

---

### Task 7: Documentation, Full Verification, Bootstrap, and Main Integration

**Files:**
- Modify: `README.md`
- Verify all files from Tasks 1–6.

**Interfaces:**
- Operator changes only `config/miner_target.json` for ordinary daily-target changes.
- Expected public URL after Pages is enabled: `https://rudwpahs.github.io/miner/`.

- [ ] **Step 1: Document the operator workflow**

Add a concise README section:

```markdown
## Daily target

The scheduled collector reads `config/miner_target.json`. To change the daily collection target, edit only `daily_target`; no workflow or Python constant needs to change.

## Miner Live

The public dashboard exposes aggregate counts only: collected total, distillation pending, distillation success, today/target, and seven-day collection history. Candidate-level data remains in private `Rudwpahs/hoopDB`.
```

- [ ] **Step 2: Run the complete test suite**

```bash
pytest -q
ruff check src scripts tests
git diff --check
```

Expected: all tests PASS, Ruff clean, no whitespace errors.

- [ ] **Step 3: Run privacy grep over public assets/config**

```bash
! grep -R -E 'CAND-[0-9a-f]{16}|canonical_hash|source_url|source_title|authors|summary|abstract' dashboard
! grep -R '1659' .github/workflows dashboard src/basketball_miner/run.py scripts/run_miner.py
```

Allowed `1659` occurrences are limited to `config/miner_target.json`, spec/plan prose, and explicit config-loading test fixtures.

- [ ] **Step 4: Verify branch CI before main**

Push the implementation branch and require the existing test workflow plus new contract tests to pass. Inspect failed logs rather than bypassing checks.

- [ ] **Step 5: Fast-forward main only if it has not diverged**

```bash
git fetch origin main
BASE=$(git merge-base HEAD origin/main)
test "$BASE" = "$(git rev-parse origin/main)"
git push origin HEAD:main
```

If main advanced, stop and rebase/merge the new main into the implementation branch, rerun all verification, then use a non-force fast-forward update. Never force-push main.

- [ ] **Step 6: Let the first scheduled/export run bootstrap numeric history**

Because `miner-state` initially lacks `state/collection_stats.json`, the first export-capable run executes `bootstrap_collection_stats` against the private inbox, computes unique historical totals, applies any new exports, and persists `collection_stats.json`. Confirm the file contains only numeric/date/timestamp fields.

- [ ] **Step 7: Verify the first dashboard build**

Check the Actions run for `Miner Live Dashboard`:

- status-builder succeeds;
- generated `_site/status.json` passes schema validation;
- Pages upload contains only `index.html`, `app.js`, `styles.css`, and `status.json`;
- no private candidate payload is present;
- deployed page displays all three headline metrics and the configured target.

If Pages is not enabled for Actions, enable GitHub Pages with **Source: GitHub Actions** in repository settings, rerun `Miner Live Dashboard`, and re-check the public URL. This is repository configuration, not an application-code workaround.

- [ ] **Step 8: Verify target reached behavior in a manual test fixture, not by consuming real quota**

Use unit/CLI fixture state where `today_collected == daily_target`; confirm logs show quota reached and no source adapter/API invocation. Do not inflate the production counter for testing.

- [ ] **Step 9: Commit docs if not already included and record final SHAs**

```bash
git add README.md
git commit -m "docs: document miner target and live metrics"
```

Record implementation branch head, final main SHA, full test count, dashboard workflow run ID, and deployed Pages URL in the completion report.

---

## Self-Review Results

- **Spec coverage:** All acceptance criteria map to Tasks 1–7: single config, 20-minute quota control, exact no-overshoot request sizing, numeric public payload, three live counters, today/target, seven-day history, timestamps, 5-minute reconciliation, event refresh, privacy boundary, and target-only operator edits.
- **Placeholder scan:** No implementation step relies on `TBD`, generic “handle errors”, or unspecified tests. Every failure behavior is tied to an explicit test or command.
- **Type consistency:** `CollectionStats`, `MinerTargetConfig`, `PublicStatus`, `build_public_status`, and `bootstrap_collection_stats` names are defined once and referenced consistently.
- **Review Focus coverage:** state failure/duplicate bootstrap is Task 3; final-slot quota behavior is Task 2; midnight rollover is Task 1; malformed private state is Task 4; public schema drift is Tasks 4 and 6.
- **Scope:** The plan remains one cohesive pipeline change: collection quota state feeds the same public observability surface. No semantic-policy or app-product changes are included.
