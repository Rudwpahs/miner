# Miner Daily Target + Live Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Miner daily target a single editable config value initially set to 1,659, enforce that quota on a Seoul calendar day, and publish a privacy-safe public dashboard for collected data, distillation backlog, and accepted distillation results.

**Architecture:** `Rudwpahs/miner` remains the public collector and dashboard host; `Rudwpahs/hoopDB` remains the private raw/V3 store. Miner persists only safe numeric collection stats in the public `miner-state` branch. Dashboard generation runs server-side in GitHub Actions, reads numeric collection stats plus the private V3 ledger/concept index, reduces them to an explicit allowlisted JSON schema, and deploys only static assets + aggregate numbers to GitHub Pages.

**Tech Stack:** Python 3.12, Pydantic 2, httpx, pytest, Ruff, GitHub Actions, vanilla HTML/CSS/JavaScript, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-09-19-miner-daily-target-live-dashboard-design.md`

**Supersedes draft:** `docs/superpowers/plans/2026-09-19-miner-daily-target-live-dashboard.md`

## Global Constraints

- Initial `daily_target` is `1659`; normal target changes edit only `config/miner_target.json`.
- Quota timezone is exactly `Asia/Seoul`.
- Scheduled Miner wakes every 20 minutes and performs zero source API calls when quota is already reached.
- A final run cannot overshoot the configured quota or advance a source checkpoint past unexported records.
- Manual `--no-export` runs do not consume quota.
- Existing relevance, DOI identity verification, and global deduplication behavior remain unchanged.
- `distillation_pending` = unique union of ledger candidates with status `PENDING` or `CLAIMED` plus `parked_review_candidate_ids`.
- `distillation_success` = unique concept-index records with `status == "ACCEPTED"`; `PROPOSE_ACCEPT` and Judge `CONFIRM` alone do not count.
- Public payload may contain only aggregate integers, dates, timestamps, target, system status, and 7-day numeric history.
- Candidate titles, URLs/DOIs, authors, summaries, candidate IDs, hashes, queue IDs, and knowledge-unit text never enter public assets or `status.json`.
- Public publication fails closed if private state is missing/malformed or the public schema contains an unexpected key.
- Dashboard reconciles every 5 minutes and also refreshes after Miner/V3 prepare workflows when reusable-workflow validation allows it.

## Review Focus

1. **Quota remainder smaller than chunk size:** request size must be limited before fetching, never by slicing after checkpoint advancement.
2. **Seoul midnight:** daily count resets to zero while cumulative total and yesterday history remain intact.
3. **Private export succeeds but safe-state persistence fails:** retry may duplicate a private raw record, but bootstrap/reconciliation must count candidate IDs uniquely so public totals never inflate from that duplicate.
4. **Malformed V3 ledger/concept index:** no guessed dashboard values; status generation fails before Pages artifact upload.
5. **Schema/privacy drift:** any unexpected public key or candidate-like identifier causes validation failure.

---

## Task 1: Single Target Config + Numeric Collection State

**Files:**
- Create: `config/miner_target.json`
- Create: `src/basketball_miner/collection_stats.py`
- Create: `tests/test_collection_stats.py`

**Interfaces:**
- `MinerTargetConfig(daily_target: int, timezone: str)`
- `CollectionStats(schema_version: int, date: str, today_collected: int, collected_total: int, daily_counts: dict[str, int], last_miner_run_at: str | None)`
- `load_target_config(path: Path) -> MinerTargetConfig`
- `load_collection_stats(path: Path, now: datetime) -> CollectionStats`
- `remaining_target(config: MinerTargetConfig, stats: CollectionStats) -> int`
- `apply_export(stats: CollectionStats, exported: int, run_at: datetime) -> CollectionStats`

- [ ] **Step 1: Write failing tests for config validation, quota math, and Seoul rollover**

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


def test_target_config_loads_operator_value(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text('{"daily_target":1659,"timezone":"Asia/Seoul"}', encoding="utf-8")
    config = load_target_config(path)
    assert config.daily_target == 1659
    assert config.timezone == "Asia/Seoul"


def test_target_config_rejects_non_positive_target(tmp_path: Path):
    path = tmp_path / "miner_target.json"
    path.write_text('{"daily_target":0,"timezone":"Asia/Seoul"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_config(path)


def test_remaining_target_never_goes_negative():
    config = load_target_config(Path("config/miner_target.json"))
    stats = CollectionStats(
        date="2026-09-19",
        today_collected=1700,
        collected_total=3000,
        daily_counts={"2026-09-19": 1700},
    )
    assert remaining_target(config, stats) == 0


def test_seoul_midnight_resets_today_only(tmp_path: Path):
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
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/test_collection_stats.py -v
```

Expected: import failure because `collection_stats.py` does not exist.

- [ ] **Step 3: Create the only operator-facing target file**

```json
{
  "daily_target": 1659,
  "timezone": "Asia/Seoul"
}
```

- [ ] **Step 4: Implement strict Pydantic models and rollover**

```python
class MinerTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_target: int = Field(gt=0, le=100_000)
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"


class CollectionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    date: str
    today_collected: int = Field(ge=0)
    collected_total: int = Field(ge=0)
    daily_counts: dict[str, int] = Field(default_factory=dict)
    last_miner_run_at: str | None = None

    @classmethod
    def empty(cls, date: str) -> "CollectionStats":
        return cls(date=date, today_collected=0, collected_total=0)
```

`load_collection_stats` must derive `today = now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()`. If stored `date != today`, return a copy with `date=today`, `today_collected=0`, preserving `collected_total`, previous `daily_counts`, and `last_miner_run_at`. Prune `daily_counts` to the newest seven ISO dates after each rollover/apply.

`apply_export` rejects `exported < 0`, increments both today and total, writes today's daily count, stamps `last_miner_run_at = run_at.isoformat()`, and returns a copied model rather than mutating its input.

- [ ] **Step 5: Add malformed-state and history-pruning tests**

```python
def test_negative_persisted_count_is_rejected(tmp_path: Path):
    path = tmp_path / "collection_stats.json"
    path.write_text(
        '{"schema_version":1,"date":"2026-09-19","today_collected":-1,'
        '"collected_total":0,"daily_counts":{},"last_miner_run_at":null}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_collection_stats(path, datetime(2026, 9, 19, 12, 0, tzinfo=KST))
```

- [ ] **Step 6: Run GREEN**

```bash
pytest tests/test_collection_stats.py -v
ruff check src/basketball_miner/collection_stats.py tests/test_collection_stats.py
```

- [ ] **Step 7: Commit**

```bash
git add config/miner_target.json src/basketball_miner/collection_stats.py tests/test_collection_stats.py
git commit -m "feat: add configurable daily miner target"
```

---

## Task 2: Exact Export Cap Without Checkpoint Loss

**Files:**
- Modify: `src/basketball_miner/run.py`
- Modify: `tests/test_run.py`

**Interface change:**

```python
run_miner(..., max_exports: int | None = None) -> RunCounters
```

- [ ] **Step 1: Extend the existing `FakeAdapter` so request limits are observable**

Change the test helper to:

```python
class FakeAdapter:
    def __init__(self, name: str, records: list[SourceRecord]) -> None:
        self.name = name
        self.records = records
        self.calls = 0
        self.request_limits: list[int] = []

    def fetch(self, checkpoint: Checkpoint, limit: int) -> AdapterBatch:
        self.calls += 1
        self.request_limits.append(limit)
        start = int(checkpoint.cursor or "0")
        rows = self.records[start : start + limit]
        return AdapterBatch(
            records=rows,
            next_checkpoint=Checkpoint(adapter=self.name, cursor=str(start + len(rows))),
        )
```

- [ ] **Step 2: Add failing cap tests**

```python
def test_zero_export_cap_skips_adapter_calls():
    adapter = FakeAdapter("fake", [make_source(1)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=10, max_exports=0)
    assert adapter.calls == 0
    assert counters.exported == 0


def test_final_request_is_bounded_by_remaining_export_slots():
    adapter = FakeAdapter("fake", [make_source(i) for i in range(10)])
    sink = FakeSink()
    counters = run_miner([adapter], sink, budget=100, chunk_size=50, max_exports=3)
    assert counters.exported == 3
    assert len(sink.candidates) == 3
    assert adapter.request_limits[0] == 3


def test_negative_export_cap_is_rejected():
    with pytest.raises(ValueError, match="max_exports"):
        run_miner([], FakeSink(), max_exports=-1)
```

- [ ] **Step 3: Run RED**

```bash
pytest tests/test_run.py -k "export_cap or remaining_export or zero_export" -v
```

- [ ] **Step 4: Implement cap by reducing `request_limit` before fetch**

Add parameter and validation:

```python
if max_exports is not None and max_exports < 0:
    raise ValueError("max_exports must be >= 0")
```

Before each adapter fetch:

```python
remaining_exports = None if max_exports is None else max_exports - exported
if remaining_exports == 0:
    break
request_limit = min(chunk_size, budget - inspected)
if remaining_exports is not None:
    request_limit = min(request_limit, remaining_exports)
```

Also stop the outer `while` when the cap is reached. Do not fetch 50 records and slice candidate output to three, because that would advance the source checkpoint past unexported records.

- [ ] **Step 5: Run focused + regression tests**

```bash
pytest tests/test_run.py -v
pytest tests/test_end_to_end_dry_run.py tests/test_source_identity.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/basketball_miner/run.py tests/test_run.py
git commit -m "feat: cap miner exports without overshoot"
```

---

## Task 3: Bootstrap Historical Totals + Persist Daily Quota State

**Files:**
- Modify: `src/basketball_miner/collection_stats.py`
- Modify: `scripts/run_miner.py`
- Modify: `.github/workflows/mine.yml`
- Modify: `tests/test_collection_stats.py`
- Create: `tests/test_run_miner_cli.py`

**New interface:**

```python
bootstrap_collection_stats(store: GitHubV3Store, now: datetime) -> CollectionStats
```

- [ ] **Step 1: Add failing bootstrap test**

Create a fake store whose `list_dir()` exposes `YYYY/MM/DD/*.jsonl` and `read_file()` returns bytes. Test global dedupe and first-seen-day accounting:

```python
def test_bootstrap_counts_candidate_ids_once_across_days():
    store = FakeStore.from_files({
        "ml/coach/miner-data/inbox/2026/09/18/a.jsonl": (
            b'{"candidate_id":"CAND-aaaaaaaaaaaaaaaa"}\n'
            b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb"}\n'
        ),
        "ml/coach/miner-data/inbox/2026/09/19/b.jsonl": (
            b'{"candidate_id":"CAND-bbbbbbbbbbbbbbbb"}\n'
            b'{"candidate_id":"CAND-cccccccccccccccc"}\n'
        ),
    })
    stats = bootstrap_collection_stats(store, datetime(2026, 9, 19, 12, 0, tzinfo=KST))
    assert stats.collected_total == 3
    assert stats.today_collected == 1
    assert stats.daily_counts == {"2026-09-18": 2, "2026-09-19": 1}
```

Also test malformed JSON and malformed candidate IDs raise instead of being ignored.

- [ ] **Step 2: Run bootstrap tests RED**

```bash
pytest tests/test_collection_stats.py -k bootstrap -v
```

- [ ] **Step 3: Implement one-time private inbox traversal**

Use only:

```python
INBOX_ROOT = "ml/coach/miner-data/inbox"
CANDIDATE_RE = re.compile(r"^CAND-[0-9a-f]{16}$")
```

Traverse year/month/day directories in lexical order, parse each nonblank JSONL object, require a valid `candidate_id`, and count each candidate only on its first chronological occurrence. Do not retain candidate content after counting. Return a `CollectionStats` object with the current Seoul date and last seven daily counts.

- [ ] **Step 4: Add CLI tests for remaining quota and zero-work path**

In `tests/test_run_miner_cli.py`, monkeypatch `run_miner`, `ensure_private_repo`, and `GitHubV3Store`:

```python
def test_export_run_passes_only_remaining_quota(...):
    # configured 100, persisted today 91
    # assert run_miner(... max_exports=9)
    ...


def test_target_reached_does_not_call_run_miner(...):
    # configured 100, persisted today 100
    # assert run_miner spy not called
    ...


def test_no_export_dry_run_does_not_write_collection_stats(...):
    # manual dry-run remains observational
    ...
```

Use real temporary JSON files for config/state so the test exercises serialization, not a hand-built object shortcut.

- [ ] **Step 5: Modify `scripts/run_miner.py` flow**

Scheduled/export path order must be:

```python
config = load_target_config(args.target_config)
now = datetime.now(ZoneInfo(config.timezone))
collection_path = args.state_dir / "collection_stats.json"
if collection_path.exists():
    collection_stats = load_collection_stats(collection_path, now)
else:
    collection_stats = bootstrap_collection_stats(
        GitHubV3Store(target_repo, target_branch, token), now
    )
remaining = remaining_target(config, collection_stats)
```

If `remaining == 0`, save the safe next state and exit before adapters/identity verification are instantiated or called. Otherwise call:

```python
counters = run_miner(..., max_exports=remaining)
collection_stats = apply_export(collection_stats, counters.exported, now)
```

Persist `collection_stats.json` in `_state_next` beside the existing four state files. Dry-run path does not write it.

- [ ] **Step 6: Change Miner schedule and safe-state persistence**

Change cron to:

```yaml
- cron: "7,27,47 * * * *"
```

Load `state/collection_stats.json` conditionally from `miner-state` so first deployment can bootstrap it. Persist exactly five safe state files after an export-capable run:

```bash
test -f _state_next/collection_stats.json
test "$(find _state_next -type f | wc -l)" -eq 5
cp _state_next/collection_stats.json state/collection_stats.json
```

Include `state/collection_stats.json` in `git add`.

- [ ] **Step 7: Run GREEN**

```bash
pytest tests/test_collection_stats.py tests/test_run_miner_cli.py -v
pytest tests/test_end_to_end_dry_run.py tests/test_export.py -v
```

- [ ] **Step 8: Commit**

```bash
git add src/basketball_miner/collection_stats.py scripts/run_miner.py \
  .github/workflows/mine.yml tests/test_collection_stats.py tests/test_run_miner_cli.py
git commit -m "feat: enforce persistent daily miner quota"
```

---

## Task 4: Privacy-Safe Public Status Aggregator

**Files:**
- Create: `src/basketball_miner/dashboard_status.py`
- Create: `scripts/build_dashboard_status.py`
- Create: `tests/test_dashboard_status.py`

**Interfaces:**
- `HistoryPoint(date: str, collected: int)`
- `PublicStatus(...)`
- `parse_concept_index(content: bytes) -> list[ConceptIndexRecord]`
- `build_public_status(config, collection_stats, ledger, concept_rows, generated_at, last_success_at) -> PublicStatus`
- `validate_public_payload(payload: dict) -> PublicStatus`

- [ ] **Step 1: Write metric-semantics tests**

```python
def test_pending_is_active_union_parked_review():
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
    status = build_public_status(...)
    assert status.distillation_pending == 2


def test_success_counts_unique_accepted_knowledge_units_only():
    rows = [
        ConceptIndexRecord(knowledge_unit_id="KU-1", status="ACCEPTED"),
        ConceptIndexRecord(knowledge_unit_id="KU-1", status="ACCEPTED"),
        ConceptIndexRecord(knowledge_unit_id="KU-2", status="REVIEW"),
    ]
    status = build_public_status(..., concept_rows=rows)
    assert status.distillation_success == 1
```

Use complete real constructor arguments instead of ellipses in the actual test file: target config, zeroed collection stats, fixed generated timestamp, and `last_success_at=None`.

- [ ] **Step 2: Write privacy/schema tests**

```python
def test_unknown_public_key_is_rejected():
    payload = valid_public_payload()
    payload["candidate_id"] = "CAND-aaaaaaaaaaaaaaaa"
    with pytest.raises(ValueError):
        validate_public_payload(payload)


def test_serialized_public_status_has_no_candidate_or_url_fields():
    rendered = json.dumps(valid_public_payload(), sort_keys=True)
    assert "CAND-" not in rendered
    assert "canonical_hash" not in rendered
    assert "https://" not in rendered
```

- [ ] **Step 3: Run RED**

```bash
pytest tests/test_dashboard_status.py -v
```

- [ ] **Step 4: Implement allowlisted models**

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

Pending set:

```python
active = {
    candidate_id
    for candidate_id, state in ledger.candidate_states.items()
    if state.status in {"PENDING", "CLAIMED"}
}
pending_ids = active | set(ledger.parked_review_candidate_ids)
```

Success set:

```python
accepted_ku_ids = {
    row.knowledge_unit_id
    for row in concept_rows
    if row.status == "ACCEPTED"
}
```

System status decision order:

```python
if collection_stats.today_collected >= config.daily_target:
    system_status = "TARGET_REACHED"
elif pending_ids:
    system_status = "DISTILLING"
else:
    system_status = "COLLECTING"
```

`BLOCKED`/`DEGRADED` are reserved for the builder CLI failure/staleness path; the normal reducer does not invent them.

- [ ] **Step 5: Implement server-side CLI**

The CLI must:

1. load `config/miner_target.json`;
2. load local `collection_stats.json` from the separately checked-out `miner-state` branch;
3. use `GitHubV3Store("Rudwpahs/hoopDB", "main", HOOPHUB_MINER_TOKEN)`;
4. read exactly `ledger_path()` and `concept_index_path()` for primary metrics;
5. parse `DistillLedger.model_validate_json(...)` and concept-index JSONL;
6. determine `last_distillation_success_at` by scanning only the newest dated V3 run directory backward until it finds a completed `AUDIT` run with at least one `CREATE|SUPPORT|REFINE|CONTRADICT` decision; if none exists, use `None`;
7. validate `PublicStatus` and write JSON to `--output`.

Exact CLI:

```bash
python scripts/build_dashboard_status.py \
  --collection-state _miner_state/state/collection_stats.json \
  --output _site/status.json
```

If ledger/concept-index is missing or malformed, exit nonzero and do not write a replacement status file.

- [ ] **Step 6: Add fail-closed tests**

```python
def test_missing_ledger_fails_closed(...):
    with pytest.raises(RuntimeError, match="ledger"):
        build_status_from_store(...)


def test_malformed_concept_index_fails_closed():
    with pytest.raises(ValueError):
        parse_concept_index(b'{"knowledge_unit_id":"KU-1","status":"UNKNOWN"}\n')
```

Use explicit fake store fixtures in the actual test file.

- [ ] **Step 7: Run GREEN**

```bash
pytest tests/test_dashboard_status.py -v
ruff check src/basketball_miner/dashboard_status.py scripts/build_dashboard_status.py tests/test_dashboard_status.py
```

- [ ] **Step 8: Commit**

```bash
git add src/basketball_miner/dashboard_status.py scripts/build_dashboard_status.py tests/test_dashboard_status.py
git commit -m "feat: build privacy-safe miner live status"
```

---

## Task 5: Mobile-First Public Dashboard

**Files:**
- Create: `dashboard/index.html`
- Create: `dashboard/app.js`
- Create: `dashboard/styles.css`
- Create: `tests/test_dashboard_assets.py`

- [ ] **Step 1: Write failing static-contract tests**

```python
def test_dashboard_has_three_headline_metrics():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="collected-total"' in html
    assert 'id="distillation-pending"' in html
    assert 'id="distillation-success"' in html


def test_dashboard_uses_same_origin_status_only():
    js = Path("dashboard/app.js").read_text(encoding="utf-8")
    assert 'fetch(`status.json?ts=${Date.now()}`' in js
    assert "api.github.com" not in js
    assert "HOOPHUB_MINER_TOKEN" not in js


def test_target_is_not_hardcoded_in_assets():
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ["dashboard/index.html", "dashboard/app.js", "dashboard/styles.css"]
    )
    assert "1659" not in text
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/test_dashboard_assets.py -v
```

- [ ] **Step 3: Create semantic HTML**

Primary structure:

```html
<section class="metric-grid" aria-label="핵심 지표">
  <article><span>수집 데이터</span><strong id="collected-total">—</strong></article>
  <article><span>증류 대기</span><strong id="distillation-pending">—</strong></article>
  <article><span>증류 성공</span><strong id="distillation-success">—</strong></article>
</section>
<progress id="today-progress" value="0" max="1"></progress>
<p id="today-progress-label">—</p>
<div id="history-chart" aria-label="최근 7일 수집량"></div>
<time id="last-miner-run">—</time>
<time id="last-distillation-success">—</time>
<time id="generated-at">—</time>
```

- [ ] **Step 4: Implement 60-second status polling**

```javascript
const nf = new Intl.NumberFormat("ko-KR");

async function refresh() {
  const response = await fetch(`status.json?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`status ${response.status}`);
  const status = await response.json();
  document.querySelector("#collected-total").textContent = nf.format(status.collected_total);
  document.querySelector("#distillation-pending").textContent = nf.format(status.distillation_pending);
  document.querySelector("#distillation-success").textContent = nf.format(status.distillation_success);
  document.querySelector("#today-progress-label").textContent =
    `${nf.format(status.today_collected)} / ${nf.format(status.daily_target)}`;
  const progress = document.querySelector("#today-progress");
  progress.max = status.daily_target;
  progress.value = Math.min(status.today_collected, status.daily_target);
  renderHistory(status.history_7d);
  renderTimes(status);
  renderStatus(status.system_status);
}

refresh().catch(renderError);
setInterval(() => refresh().catch(renderError), 60_000);
```

`renderError` must visibly mark the page stale/degraded instead of silently keeping a healthy label.

- [ ] **Step 5: Implement dependency-free history bars and responsive CSS**

```css
.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1rem;
}
.metric-grid strong { font-variant-numeric: tabular-nums; }
@media (max-width: 720px) {
  .metric-grid { grid-template-columns: 1fr; }
}
```

History chart uses DOM/CSS bars with text labels, not an external chart library.

- [ ] **Step 6: Add privacy/stale tests**

Tests assert dashboard assets contain no `candidate_id`, `canonical_hash`, `source_url`, token variable, or GitHub API host, and that `renderError`/`generated-at` rendering exists.

- [ ] **Step 7: Run GREEN**

```bash
pytest tests/test_dashboard_assets.py -v
```

- [ ] **Step 8: Commit**

```bash
git add dashboard tests/test_dashboard_assets.py
git commit -m "feat: add Miner Live dashboard"
```

---

## Task 6: 5-Minute Reconciliation + GitHub Pages Deployment

**Files:**
- Create: `.github/workflows/dashboard.yml`
- Modify: `.github/workflows/mine.yml`
- Modify: `.github/workflows/distill_v3_prepare.yml`
- Create: `tests/test_workflow_contracts.py`

- [ ] **Step 1: Write failing workflow tests**

```python
def test_miner_schedule_is_every_twenty_minutes():
    text = Path(".github/workflows/mine.yml").read_text(encoding="utf-8")
    assert 'cron: "7,27,47 * * * *"' in text
    assert "1659" not in text


def test_dashboard_schedule_is_every_five_minutes():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert 'cron: "*/5 * * * *"' in text
    assert "workflow_call:" in text


def test_status_build_precedes_pages_upload():
    text = Path(".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert text.index("build_dashboard_status.py") < text.index("upload-pages-artifact")
```

- [ ] **Step 2: Run RED**

```bash
pytest tests/test_workflow_contracts.py -v
```

- [ ] **Step 3: Create reusable/scheduled dashboard workflow**

Trigger:

```yaml
on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:
  workflow_call:
    secrets:
      HOOPHUB_MINER_TOKEN:
        required: true
```

Permissions:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

Build order:

```text
checkout main
checkout miner-state into _miner_state
setup Python 3.12
pip install .
copy dashboard/* into _site
build_dashboard_status.py -> _site/status.json
configure-pages
upload-pages-artifact(path=_site)
deploy-pages
```

Only the status-builder step receives `HOOPHUB_MINER_TOKEN` in its environment.

- [ ] **Step 4: Refresh after Miner and V3 prepare**

Add reusable workflow jobs after successful state persistence/preparation:

```yaml
refresh-dashboard:
  needs: persist-state
  uses: ./.github/workflows/dashboard.yml
  secrets: inherit
```

and:

```yaml
refresh-dashboard:
  needs: prepare-shadow
  uses: ./.github/workflows/dashboard.yml
  secrets: inherit
```

If GitHub validation rejects a reusable workflow because of caller permissions, replace these two event calls with same-repository `workflow_dispatch` calls and keep the 5-minute schedule unchanged. Do not copy the dashboard build steps into multiple workflows.

- [ ] **Step 5: Add privacy workflow tests**

Tests reject any workflow line that copies `ml/coach/miner-data/inbox` into `_site`, checks out full private candidate data into the Pages artifact path, or writes token values to files.

- [ ] **Step 6: Run GREEN**

```bash
pytest tests/test_workflow_contracts.py tests/test_dashboard_assets.py -v
```

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/dashboard.yml .github/workflows/mine.yml \
  .github/workflows/distill_v3_prepare.yml tests/test_workflow_contracts.py
git commit -m "ci: publish near-live Miner Live dashboard"
```

---

## Task 7: Documentation, Verification, First Bootstrap, Main Integration

**Files:**
- Modify: `README.md`
- Verify all files from Tasks 1–6.

- [ ] **Step 1: Document the one-line target change path**

Add:

```markdown
## Daily target

Scheduled collection reads `config/miner_target.json`. To change the target, edit only `daily_target`; Python and workflow schedules do not contain a duplicate target constant.

## Miner Live

The public dashboard exposes aggregate counts only: collected total, distillation pending, distillation success, today/target, timestamps, and seven-day collection history. Candidate-level data remains private in `Rudwpahs/hoopDB`.
```

- [ ] **Step 2: Run full local verification**

```bash
pytest -q
ruff check src scripts tests
git diff --check
```

- [ ] **Step 3: Run public-asset privacy checks**

```bash
! grep -R -E 'CAND-[0-9a-f]{16}|canonical_hash|source_url|source_title|authors|summary|abstract' dashboard
! grep -R '1659' .github/workflows dashboard src/basketball_miner/run.py scripts/run_miner.py
```

Allowed literal `1659` locations: `config/miner_target.json`, design/plan prose, and explicit tests proving config loading.

- [ ] **Step 4: Push implementation branch and require CI PASS**

Inspect any failing job logs and fix causes; do not bypass checks.

- [ ] **Step 5: Integrate main without force**

```bash
git fetch origin main
BASE=$(git merge-base HEAD origin/main)
test "$BASE" = "$(git rev-parse origin/main)"
git push origin HEAD:main
```

If main advanced, update the implementation branch from current main, rerun full verification, then fast-forward. Never force-push main.

- [ ] **Step 6: Verify first quota-capable run bootstraps historical totals**

The first run finds no `state/collection_stats.json`, scans private inbox once, counts unique candidate IDs, applies new exports, and persists only numeric/date/timestamp state to `miner-state`. Inspect the resulting state file and confirm it contains no candidate IDs or source metadata.

- [ ] **Step 7: Verify first dashboard deployment**

Confirm `_site` contains exactly:

```text
index.html
app.js
styles.css
status.json
```

Check the page at the repository Pages URL and verify the three headline counters, today/target, timestamps, and 7-day history render.

- [ ] **Step 8: Verify quota-reached behavior without consuming production quota**

Use a test fixture where `today_collected == daily_target`; verify the CLI exits before source adapter calls. Do not modify production counters for this check.

- [ ] **Step 9: Record completion evidence**

Completion report must include implementation branch head, final main SHA, full pytest count, Ruff result, dashboard workflow run ID, and deployed Pages URL.

---

## Self-Review Results

- **Spec coverage:** single target config, 20-minute quota control, exact no-overshoot request sizing, historical bootstrap, public aggregate metrics, 5-minute reconciliation, mobile dashboard, privacy boundary, and one-value operator changes all have explicit tasks.
- **Placeholder scan:** no `TBD`, `TODO`, or unspecified generic “add error handling” steps remain.
- **Type consistency:** only `MinerTargetConfig`, `CollectionStats`, `HistoryPoint`, and `PublicStatus` are named public models; all later tasks use those exact names.
- **Review Focus coverage:** remainder cap → Task 2; midnight → Task 1; state failure/unique bootstrap → Task 3; malformed private state → Task 4; public schema drift → Tasks 4 and 6.
- **Scope:** no semantic-policy change, model-spend change, or product-app code is included.
