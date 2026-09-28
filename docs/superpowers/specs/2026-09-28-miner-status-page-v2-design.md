# Miner Status Page v2 Design

Date: 2026-09-28
Repository: `Rudwpahs/miner`
Related private data repository: `Rudwpahs/hoopDB`
Supersedes only the dashboard fault-handling/UX portions of `2026-09-19-miner-daily-target-live-dashboard-design.md`; collection quota rules remain unchanged.

## 1. Goal

Turn the existing Miner Live dashboard into a reliable, status-page-style operational page that can be opened instead of manually checking GitHub commits and workflows.

The page must answer, at a glance:

- Did Miner collect today?
- Did it reach the configured daily target?
- Is distillation working, delayed, or unavailable?
- How much work is waiting for distillation?
- How much durable accepted corpus exists?
- When did collection and distillation last succeed?

The page must remain useful when one subsystem is broken. A malformed or temporarily unavailable distillation state must not hide valid Miner collection state.

## 2. Current State and Problem

The existing implementation already has:

- `dashboard/index.html`, `dashboard/app.js`, `dashboard/styles.css`;
- a five-minute GitHub Pages reconciliation workflow;
- privacy-safe server-side aggregation;
- daily target and collection state on the `miner-state` branch;
- V3 ledger/concept-index readers for distillation metrics.

On 2026-09-28 the safe Miner state reports `today_collected=1659`, `daily_target=1659` through config, and `collected_total=10284`. The V3 `distill.json` in `hoopDB` is currently empty.

The existing dashboard aggregation treats malformed/missing distillation state as a fatal error. That preserves privacy but makes the entire status page unavailable precisely when the user needs to know that distillation is unhealthy.

## 3. Design Principle: Fault Isolation

Keep the privacy boundary, but isolate component failure.

- Miner collection state is one component.
- Distillation queue state is one component.
- Accepted corpus state is one component.
- Page freshness is one component.

Each component is parsed and validated independently.

A component that cannot be verified becomes `UNKNOWN` or `DEGRADED`; it does not fabricate `0` and does not prevent other verified components from publishing.

Candidate-level data remains forbidden in public output.

## 4. Public Status Contract v2

Publish `status.json` with an explicit allowlisted schema.

```json
{
  "schema_version": 2,
  "generated_at": "2026-09-28T17:00:00+09:00",
  "timezone": "Asia/Seoul",
  "summary_status": "DEGRADED",
  "miner": {
    "status": "OPERATIONAL",
    "today_collected": 1659,
    "daily_target": 1659,
    "collected_total": 10284,
    "last_success_at": "2026-09-28T01:56:47.441215+09:00"
  },
  "distillation": {
    "status": "UNAVAILABLE",
    "pending": null,
    "last_success_at": null,
    "reason": "STATE_UNAVAILABLE"
  },
  "corpus": {
    "status": "OPERATIONAL",
    "accepted_total": 0
  },
  "history_7d": [
    {"date": "2026-09-28", "collected": 1659}
  ]
}
```

Allowed component statuses:

- `OPERATIONAL`
- `COLLECTING`
- `DELAYED`
- `DEGRADED`
- `UNAVAILABLE`
- `UNKNOWN`

Allowed summary statuses:

- `OPERATIONAL`
- `DEGRADED`
- `PARTIAL_OUTAGE`
- `STALE`

Allowed safe reason codes:

- `NONE`
- `STATE_UNAVAILABLE`
- `STATE_MALFORMED`
- `NO_RECENT_SUCCESS`
- `PAGE_STALE`

No free-form private error text is published.

## 5. Metric Rules

### 5.1 Miner

Source: `miner-state/state/collection_stats.json` plus `config/miner_target.json`.

- `today_collected`, `collected_total`, `last_success_at` remain authoritative from collection state.
- `daily_target` uses the same effective-target logic as production Miner.
- `status=OPERATIONAL` when today has reached the target.
- `status=COLLECTING` when below target and the latest Miner state is fresh.
- `status=DELAYED` when below target and the last Miner success is older than the configured freshness threshold.

### 5.2 Distillation

Source: private V3 ledger and recent semantic run records.

- Valid ledger: compute pending count using existing unique pending/claimed + parked-review logic.
- Missing/empty/malformed ledger: `pending=null`, `status=UNAVAILABLE`, safe reason code only.
- Never convert an unavailable ledger into `pending=0`.
- `last_success_at` is the latest verified completed semantic promotion/audit success.
- If pending work exists but no verified success occurs within the delay threshold, use `DELAYED`.

### 5.3 Corpus

Source: private V3 concept index.

- Parse independently from the ledger.
- `accepted_total` is the count of unique durable knowledge units with `status == ACCEPTED`.
- If the concept index is valid, corpus remains `OPERATIONAL` even when the ledger is unavailable.
- If concept index is missing/malformed, set `accepted_total=null` and `status=UNAVAILABLE`.

## 6. Summary Status Rules

Priority order:

1. Page data older than the stale threshold -> `STALE`.
2. Miner unavailable -> `PARTIAL_OUTAGE`.
3. Distillation or corpus unavailable/delayed while Miner is healthy -> `DEGRADED`.
4. All observable components healthy -> `OPERATIONAL`.

The page headline maps these to simple Korean copy:

- `OPERATIONAL` -> `정상 운영 중`
- `DEGRADED` -> `일부 처리 지연`
- `PARTIAL_OUTAGE` -> `일부 시스템 장애`
- `STALE` -> `상태 갱신 지연`

## 7. Dashboard UX — OpenAI Status-inspired information architecture

Reference interaction pattern: the current OpenAI Status page leads with one prominent overall-state message, then presents a `System status` section with service rows and a separate history path. Miner Status should use the same scanning model without copying OpenAI branding, text, exact spacing, or proprietary assets.

### 7.1 Visual direction

- light, neutral background;
- centered content column around 820–900 px on desktop;
- generous whitespace and thin neutral borders;
- system font stack, restrained typography, no gradients or glass effects;
- green / amber / red / gray used only for state communication;
- rounded cards with subtle radius, not oversized dashboard tiles;
- mobile-first single-column collapse with no horizontal scrolling.

### 7.2 Header

Left:

- `HoopHub Status` wordmark/text only.

Right:

- compact `새로고침` action;
- optional `History` link once history view exists.

Do not add a notification-subscription system in Phase 1.

### 7.3 Overall status banner

Directly below the header, show one large bordered status panel similar in purpose to the top OpenAI Status message.

Operational example:

- green check icon;
- `정상 운영 중`;
- secondary copy: `Miner 및 확인 가능한 처리 시스템이 정상입니다.`

Degraded example:

- amber indicator;
- `일부 처리 지연`;
- secondary copy: `Miner 수집은 정상이며 Distillation 상태를 확인할 수 없습니다.`

The banner must never call the entire system healthy when one required component is unknown/unavailable.

### 7.4 System status panel

Below the overall banner, show a single `System status` card containing three service rows rather than three unrelated large metric cards.

#### Miner row

Primary line:

- green/amber/red state icon;
- `Miner`;
- right-aligned state label such as `Operational` or `Collecting`.

Secondary line:

- `오늘 1,659 / 1,659`;
- `누적 10,284`;
- `마지막 수집 01:56`.

Visual history:

- show seven compact day segments based only on authoritative collection-day data;
- green when target reached;
- amber when positive but below target;
- gray when no verified value exists;
- never infer outage from missing historical detail.

#### Distillation row

Primary line:

- state icon;
- `Distillation`;
- right-aligned `Operational`, `Delayed`, or `Unavailable`.

Secondary line:

- `대기 N개` when verified;
- `대기 확인 불가` when ledger unavailable;
- `마지막 성공 <time>` when known.

If unavailable/delayed, row can expand or show a compact incident-style message beneath it using only safe reason text, for example `상태 파일을 확인할 수 없습니다.`

Do not fabricate historical uptime bars for Distillation until historical component status is actually persisted.

#### Corpus row

Primary line:

- state icon;
- `Corpus`;
- right-aligned status.

Secondary line:

- `승인된 KU N개` when verified;
- `확인 불가` otherwise.

Do not fabricate historical uptime bars for Corpus until historical component status is actually persisted.

### 7.5 History area

Below `System status`, provide a restrained history section inspired by the separate history affordance on OpenAI Status.

Phase 1:

- `최근 7일 수집 기록`;
- one compact row per day or simple segmented bar;
- date + collected count + target result;
- no chart library.

Later Phase 2 may expand to 30/90-day component history once trustworthy component-history data exists.

### 7.6 Incident presentation

When `summary_status != OPERATIONAL`, show a small incident-style card between the overall banner and system rows.

Examples:

- `Distillation 상태 확인 불가`
- `마지막 확인 가능한 증류 성공: 9월 22일`
- state badge such as `Investigating`, `Delayed`, or `Unavailable` mapped from safe internal enums.

No stack trace, exception text, repository path, candidate ID, URL, DOI, or private file name is exposed.

### 7.7 Footer

Use a short muted note:

- `집계 상태만 공개됩니다. 후보 원문과 식별 정보는 비공개입니다.`
- `마지막 갱신 <time>`.

### 7.8 Accessibility / interaction

- status must never rely on color alone; pair color with icon + text;
- semantic headings and `<section>` labels;
- buttons at least 44 px touch target on mobile;
- `aria-live=polite` for refreshed summary state;
- 60-second browser refresh remains, with a visible manual refresh action;
- if `status.json` refresh fails, keep the last successfully rendered values and mark the page `STALE` instead of clearing numbers.

## 8. Runtime / Runner Health

Direct `Runner` / `Ollama` liveness is useful but is not required to make the first reliable status page work.

Phase 1 derives operational state from authoritative repository state and freshness, avoiding new high-frequency heartbeat commits.

A later Phase 2 may add a privacy-safe runtime heartbeat if needed. It must contain only booleans/timestamps such as runner seen, Ollama reachable, GPU visible, and model ready; no hostname, IP, usernames, paths, tokens, or hardware serials.

## 9. Publication and Refresh

Keep GitHub Pages and the existing five-minute reconciliation schedule.

The build must:

1. load public collection state;
2. attempt each private distillation/corpus source independently;
3. validate each result;
4. reduce failures to nullable aggregate fields + safe status/reason enums;
5. validate the full public schema against an allowlist;
6. publish static assets and `status.json`.

A single malformed private subsystem must not abort publication unless privacy validation itself fails.

Privacy validation remains fail-closed: any forbidden candidate-like content or unexpected public key aborts publication.

## 10. Testing Requirements

Add tests proving:

- valid Miner state still publishes when V3 ledger is empty;
- empty ledger produces `distillation.pending == null`, never `0`;
- valid concept index remains visible when ledger is invalid;
- malformed concept index affects only Corpus;
- public payload rejects unexpected/private fields;
- summary status becomes `DEGRADED` for distillation failure with healthy Miner;
- dashboard renders `확인 불가` for nullable values;
- dashboard keeps last known values and marks `STALE` when refresh fails;
- dashboard uses component rows and overall status banner;
- dashboard remains same-origin only;
- existing quota/dedup/collection tests remain green.

## 11. Non-Goals

- No candidate-level browser API.
- No direct browser access to `hoopDB`.
- No WebSocket service.
- No new database.
- No authentication layer for the public aggregate page.
- No automatic repair or restart of the local distiller from the public page.
- No pixel-for-pixel clone of OpenAI Status; use the same operational-status information hierarchy with HoopHub-specific content and styling.
