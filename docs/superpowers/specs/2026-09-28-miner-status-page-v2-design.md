# Miner Status Page v2 Design

Date: 2026-09-28
Repository: `Rudwpahs/miner`
Related private data repository: `Rudwpahs/hoopDB`
Supersedes only the dashboard fault-handling/UX portions of `2026-09-19-miner-daily-target-live-dashboard-design.md`; collection quota rules remain unchanged.

## 1. Goal

Turn the existing Miner Live dashboard into a reliable, status.openai.com-style operational page that can be opened instead of manually checking GitHub commits and workflows.

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

## 7. Dashboard UX

Keep the existing static GitHub Pages implementation, but change the layout to component-oriented status.

Top:

- large overall status line and dot;
- last refreshed time.

Components:

1. **Miner**
   - status badge;
   - `오늘 수집 1,659 / 1,659`;
   - cumulative collected total;
   - last successful collection.

2. **Distillation**
   - status badge;
   - pending count or `확인 불가`;
   - last verified distillation success;
   - safe reason label when unavailable/delayed.

3. **Corpus**
   - status badge;
   - accepted durable knowledge-unit count or `확인 불가`.

Below components:

- 7-day collection history;
- no candidate titles, IDs, URLs, DOI, authors, summaries, queue IDs, or private paths.

Mobile view must show the three components without horizontal scrolling.

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
- dashboard remains same-origin only;
- existing quota/dedup/collection tests remain green.

## 11. Non-Goals

- No candidate-level browser API.
- No direct browser access to `hoopDB`.
- No WebSocket service.
- No new database.
- No authentication layer for the public aggregate page.
- No automatic repair or restart of the local distiller from the public page.
