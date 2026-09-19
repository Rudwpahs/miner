# Miner Daily Target + Live Dashboard Design

Date: 2026-09-19
Repository: `Rudwpahs/miner`
Related private data repository: `Rudwpahs/hoopDB`

## 1. Goal

Build two connected capabilities without exposing private candidate data:

1. Raise Miner collection capacity so the system targets **1,659 newly exported candidates per Seoul calendar day**.
2. Provide a public, mobile-friendly live dashboard showing aggregate counts for collected data, distillation backlog, and successfully distilled data.

The value `1,659` must not be hard-coded into workflow logic. It must live in one configuration file so it can be changed quickly in response to a plain-language request such as “마이너 하루 2000개로 바꿔”.

The target is a controlled daily quota/cap, not a promise that upstream sources will always yield exactly that many eligible unique candidates.

## 2. Existing System

Current Miner behavior:

- Public collector repository: `Rudwpahs/miner`.
- Private candidate store: `Rudwpahs/hoopDB`.
- Scheduled collection workflow: `.github/workflows/mine.yml`.
- Current collection schedule: every 3 hours.
- Miner currently uses Crossref plus YouTube RSS adapters.
- Crossref candidates are relevance-checked, DOI identity-checked, deduplicated, then exported to private `hoopDB`.
- Current V3 preparation workflow runs hourly and creates/advances deterministic V3 shadow state.
- V3 state is tracked in `hoopDB` under `ml/coach/miner-data/v3/`, including the distillation ledger and concept index.

Private candidate payloads must remain private. The public repository must never receive candidate titles, URLs, authors, summaries, candidate IDs, fingerprints, raw queue payloads, or knowledge-unit text merely to power the dashboard.

## 3. Daily Target Control

### 3.1 Configuration

Create one public configuration file:

`config/miner_target.json`

Initial content:

```json
{
  "daily_target": 1659,
  "timezone": "Asia/Seoul"
}
```

`daily_target` is the single operator-facing knob. The implementation must not duplicate `1659` in workflow shell, Python defaults, dashboard JavaScript, or production state. A test fixture may assert that the configured initial value is loaded.

### 3.2 Durable quota state

Extend the existing safe `miner-state` branch with an aggregate-only metrics file, for example:

`state/miner_metrics.json`

It stores only non-sensitive counters and timestamps:

```json
{
  "schema_version": 1,
  "timezone": "Asia/Seoul",
  "collected_total": 0,
  "daily_exports": {
    "2026-09-19": 0
  },
  "last_miner_run_at": null
}
```

This file is the authoritative source for Miner quota accounting after bootstrap. It is persisted atomically with the existing checkpoint/dedup state so a workflow retry cannot silently double-count exports.

A one-time bootstrap job computes the historical `collected_total` and recent daily export counts from the private inbox, validates them, and initializes `miner_metrics.json`. Normal scheduled runs then update counters incrementally; they do not rescan the entire private inbox.

### 3.3 Schedule

Change the Miner schedule from every 3 hours to every 20 minutes.

The higher wake-up frequency is for recovery and quota control, not to force a full mining run every 20 minutes.

### 3.4 Quota behavior

At the start of a scheduled run:

1. Resolve the current date in `Asia/Seoul`.
2. Read `daily_target` from `config/miner_target.json`.
3. Read today’s exported count from `state/miner_metrics.json` on `miner-state`.
4. Calculate:

   `remaining = max(0, daily_target - exported_today)`

5. If `remaining == 0`, exit before source API calls.
6. Otherwise mine with a bounded inspection budget and an explicit `max_exports=remaining` cap.
7. Persist checkpoints, dedup state, and aggregate counters together after a successful export cycle.

A run must never intentionally overshoot the configured target because of batch size. If a fetched batch contains more eligible candidates than the remaining quota, export exactly the deterministic first `remaining` candidates and do not count or mark unexported candidates as exported.

### 3.5 Recovery behavior

- A source rate limit or transient failure must not reset the day’s count.
- The next scheduled run resumes against the same daily target.
- At Seoul midnight, the daily key changes and a new quota starts.
- Existing global deduplication and DOI identity verification remain unchanged.
- Manual `--no-export` dry-runs do not consume the daily quota.
- A failed export must not advance the aggregate export counter.
- Workflow retries must be idempotent with respect to exported candidate IDs and quota counters.

### 3.6 Capacity expectation

The dashboard distinguishes:

- target reached,
- still collecting,
- source-limited / degraded,
- blocked/error.

If upstream sources cannot supply 1,659 new relevant unique candidates on a given day, the system reports the shortfall rather than fabricating or duplicating records.

## 4. Public Aggregate Status Contract

### 4.1 Public-only aggregate payload

Publish a small aggregate file with the dashboard deployment, named `status.json`.

Allowed fields:

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-19T18:00:00+09:00",
  "timezone": "Asia/Seoul",
  "daily_target": 1659,
  "collected_total": 0,
  "distillation_pending": 0,
  "distillation_success": 0,
  "today_collected": 0,
  "last_miner_run_at": null,
  "last_distillation_success_at": null,
  "system_status": "COLLECTING",
  "history_7d": [
    {"date": "2026-09-19", "collected": 0}
  ]
}
```

Forbidden public fields include:

- candidate title
- URL or DOI
- author
- abstract/summary
- candidate ID
- canonical hash/fingerprint
- queue batch IDs
- knowledge-unit text
- private repository paths that reveal candidate identity

### 4.2 Exact metric definitions

`collected_total`
: The cumulative count of unique candidate records successfully exported by Miner into the private inbox. After one-time bootstrap, the source of truth is the durable aggregate counter in `miner-state`.

`today_collected`
: The successfully exported count for the current `Asia/Seoul` calendar date from `miner-state`.

`distillation_pending`
: The number of distinct V3 candidate IDs that still require semantic action. Compute this from the authoritative `DistillLedger` as the union of (a) candidate states whose status is `PENDING` or `CLAIMED` and (b) `parked_review_candidate_ids`, excluding IDs in `terminal_candidate_ids`. Count distinct IDs once. A parked `REVIEW`/`BLOCKED` item therefore remains visible as pending work even though its current stage state may be marked `COMPLETE`.

`distillation_success`
: The number of distinct durable knowledge units whose `ConceptIndexRecord.status == "ACCEPTED"` in the authoritative V3 concept index. Intermediate `PROPOSE_ACCEPT`, `JUDGE/CONFIRM`, or `AUDIT` staging decisions are not counted until the accepted knowledge unit is durably represented in the concept index. This avoids reporting semantic proposals as successful distilled knowledge.

`last_distillation_success_at`
: The timestamp of the most recent repository state change that increased the accepted concept-index count. If historical data cannot provide an exact timestamp, bootstrap leaves this field `null` until the next observed increase.

The dashboard subtitle for `distillation_success` should clarify that the number represents **승인된 지식 단위(KU)**, while `collected_total` and `distillation_pending` are candidate counts.

## 5. Aggregation Architecture

Use a one-way data boundary:

`hoopDB (private)` → server-side aggregate job → allowlisted numeric status → public dashboard

The browser must never call the private repository API and must never need a GitHub token.

The aggregate job runs in GitHub Actions with the existing private-repository access credential. It obtains the required private files server-side, computes only the approved metrics, validates the payload against an explicit schema/allowlist, and publishes only the aggregate payload.

For efficiency:

- Miner collection totals come from `miner-state`, not repeated private inbox scans.
- Distillation pending comes from the V3 ledger.
- Distillation success comes from the V3 concept index.
- The private repository checkout/fetch should be sparse/minimal and read-only.

Before publishing, the job must fail closed if unknown fields appear or authoritative state cannot be parsed.

## 6. Refresh and Publication Strategy

Near-live is sufficient; strict WebSocket real-time is unnecessary.

Refresh triggers:

1. After a Miner export/persist cycle.
2. After V3 preparation/materialization changes when an event hook is available.
3. After semantic distillation changes when an event hook is available.
4. A periodic 5-minute reconciliation job to repair missed event-driven updates.

Publication uses a public static GitHub Pages site. Static HTML/CSS/JS changes are deployed only when the dashboard code changes. Aggregate `status.json` is updated independently by the reconciliation/event workflow, so the page itself does not need a full rebuild for every metric refresh.

To avoid pointless status-history churn, the publisher compares semantic fields before writing. If counters/status have not changed, it may skip the write; a heartbeat timestamp should still be refreshed at least every 30 minutes so stale monitoring is detectable.

The dashboard polls `status.json` periodically with cache-busting and displays `generated_at` so stale data is obvious.

## 7. Dashboard UX

Host a public static page from `Rudwpahs/miner` using GitHub Pages.

Mobile-first layout:

Primary cards:

- **수집 데이터** — candidate count
- **증류 대기** — candidate count
- **증류 성공** — accepted KU count

Secondary information:

- 오늘 수집: `x / daily_target`
- daily progress bar
- current system status
- last Miner run
- last successful distillation
- 7-day daily collection chart
- status payload update time
- small unit labels so candidate counts and KU counts are not confused

Supported status labels:

- `COLLECTING`
- `TARGET_REACHED`
- `DISTILLING`
- `BLOCKED`
- `DEGRADED`

The UI must not expose private object identifiers in tooltips, URLs, DOM data attributes, logs, or source maps.

## 8. Data Integrity and Privacy

The public aggregation code must use an explicit allowlist, not blacklist filtering.

Validation requirements:

- all numeric counters are integers >= 0
- `today_collected <= daily_target` under normal quota-controlled operation
- history contains dates + numeric counts only
- generated timestamps are ISO-8601 or `null` where explicitly allowed
- no unexpected JSON keys
- no candidate-like identifier patterns in public payload
- no DOI/title/author/summary fields
- no private-repository token or private checkout metadata in build output

If aggregation cannot confidently read authoritative private state, do not publish guessed counts. Preserve the last known good counters and set the public system state to `DEGRADED` or `BLOCKED` through the safe publisher path.

## 9. Testing Strategy

Implementation follows TDD.

### Miner target tests

- config loads `daily_target`
- invalid/non-positive target is rejected
- quota remaining calculation
- zero remaining skips adapters/API calls
- partial final batch exports exactly the remaining count
- unexported overflow candidates are not marked exported/deduplicated incorrectly
- manual dry-run does not consume quota
- Seoul date boundary resets daily quota
- failed export does not advance quota state
- workflow retry is idempotent
- global deduplication and DOI identity behavior remain unchanged

### Aggregate tests

- fixture Miner metrics + V3 ledger + concept index produce expected totals
- pending includes `PENDING`, `CLAIMED`, and parked-review IDs exactly once
- rejected/duplicate terminal candidates are excluded from pending
- intermediate `PROPOSE_ACCEPT` and `CONFIRM` are not counted as success
- only distinct concept-index `ACCEPTED` knowledge units count as success
- duplicate knowledge-unit IDs are not double-counted
- allowlist rejects all private fields/unknown keys
- missing or malformed authoritative state fails closed

### Dashboard tests

- renders the three headline counters
- renders candidate/KU units accurately
- renders daily target from status payload, not hard-coded value
- handles stale/error payload visibly
- no private identifiers or raw candidate fields appear in built assets

### Workflow contract tests

- Miner scheduled every 20 minutes
- status reconciliation scheduled every 5 minutes
- target read from config file
- production workflow logic contains no duplicated literal target
- public status publication depends on successful aggregate validation
- private checkout is read-only and credentials are not persisted into deploy output

## 10. Operational Change Procedure

To change the daily Miner target later:

1. Edit only `config/miner_target.json` → `daily_target`.
2. Run target/config contract tests.
3. Commit the validated one-value change to `main`.
4. The next scheduled Miner run automatically uses the new target.
5. The dashboard automatically displays the same new target from generated status.

No code or workflow schedule change is necessary for ordinary target changes. This is the path ChatGPT should use when the user says, for example, “마이너 하루 3000개로 바꿔”.

## 11. Out of Scope

- Exposing candidate-level search publicly.
- Publishing private `hoopDB` files.
- Changing V3 semantic decision policy.
- Increasing semantic distillation spend automatically.
- Replacing DOI identity verification or relevance logic.
- Guaranteeing the configured target when upstream sources cannot supply enough eligible unique candidates.

## 12. Acceptance Criteria

The feature is complete when:

1. Daily target is controlled from a single config value initially set to 1,659.
2. Scheduled Miner wakes every 20 minutes and stops source API work once the daily target is met.
3. Miner can finish a day at exactly the target without batch overshoot or dedup corruption.
4. Durable quota counters survive retries and day boundaries.
5. Public `status.json` contains only approved aggregate fields.
6. Public dashboard shows total collected candidates, pending candidates, accepted-KU success count, today/target, 7-day history, and timestamps.
7. Pending and success counts follow the exact ledger/concept-index definitions in this design.
8. Dashboard updates near-live through event-driven refresh plus 5-minute reconciliation.
9. Raw private candidate data never enters the public repository/page/deployment artifact.
10. Tests verify quota, privacy boundary, metrics semantics, idempotency, and workflow contracts.
11. Changing the target later requires changing only one config value.
