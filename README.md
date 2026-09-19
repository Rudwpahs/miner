<div align="center">

# ⛏️ Basketball Knowledge Miner

### Collect broadly. Promote carefully.

농구 자료를 많이 모으되, **아무 자료나 바로 FormPath 지식으로 승격시키지 않는 공개 수집·검증 파이프라인**입니다.

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-Collector-3776AB?logo=python&logoColor=white">
  <img alt="GitHub Actions" src="https://img.shields.io/badge/GitHub_Actions-Scheduled-2088FF?logo=githubactions&logoColor=white">
  <img alt="Pipeline" src="https://img.shields.io/badge/Pipeline-Deterministic-6f42c1">
  <img alt="Boundary" src="https://img.shields.io/badge/Data-Public→Private-2ea44f">
</p>

[Collection](#collection-pipeline) · [Distillation V3](#distillation-v3) · [Safety Boundary](#public--private-boundary) · [Run](#development)

</div>

---

## What this repo does

이 저장소는 **수집과 1차 정리**를 담당합니다. 실제 candidate payload는 비공개 `Rudwpahs/hoopDB`로 넘기고, 여기서는 공개 가능한 코드·상태·fixture만 관리합니다.

### V1 sources

- Crossref 학술 메타데이터
- 명시적으로 허용한 YouTube 코칭 / 전문가 채널
- 같은 provenance·rate-limit·fixture 검증을 통과한 추가 공식/API 소스

### Explicitly out of scope

Reddit 전체 스크래핑, paywall 우회, anti-bot 우회, 원본 영상, 전체 transcript, 전체 저작권 기사, private FormPath 연구 내용은 수집 대상이 아닙니다.

## Collection pipeline

```mermaid
flowchart LR
    A[Source adapter] --> B[Normalize candidate]
    B --> C[Basketball relevance]
    C --> D[Trim fields]
    D --> E[Exact dedup]
    E --> F[Export candidate]
    F --> G{Target repo private?}
    G -->|Yes| H[hoopDB inbox]
    G -->|No| I[STOP]
```

목표는 많이 긁어오는 것이 아니라 **출처가 남고, 중복이 줄어들고, 공개/비공개 경계를 넘지 않는 후보 데이터**를 만드는 것입니다.

## Schedule & limits

| Item | Current setting |
|---|---|
| Production cron | `17 */3 * * *` UTC |
| Runs / day | 8 |
| Max source records / run | 20,000 |
| Theoretical inspected / day | 160,000 |

실제 candidate 수는 관련성 검사와 중복 제거 때문에 더 적습니다. GitHub cron은 정확한 시각보다 늦게 시작될 수 있고 외부 API 정책·quota도 바뀔 수 있습니다.

## Distillation V3

V3 core는 의미 판단을 직접 하지 않고 **각 단계가 할 수 있는 행동을 제한**합니다.

```mermaid
flowchart TD
    A[RAW INBOX] --> B[Deterministic validation]
    B --> C[VALIDATED]
    C --> D[Exact dedup + lease]
    D --> E[TRIAGE]
    E -->|Reject| X[REJECT]
    E -->|No direct accept| F[DEEP REVIEW]
    F -->|Reject| X
    F -->|Propose accept| G[JUDGE]
    G -->|Reject| X
    G -->|Confirm| H[IMMUTABLE STAGING]
    H --> I[Future AUDITOR / promotion]
    I --> J[CANONICAL KNOWLEDGE]
```

### Hard rules

- raw inbox → canonical 직접 이동 금지
- Triage는 `ACCEPT` 불가
- Deep의 `PROPOSE_ACCEPT`만으로 승인 불가
- Judge의 `CONFIRM` 필요
- staging은 동일 byte 재시도만 허용, 내용이 바뀐 overwrite 거부
- 실제 canonical promotion은 future Auditor integration만 수행

## Public ↔ Private boundary

```mermaid
flowchart LR
    A[Public miner code] --> B[Candidate metadata]
    B --> C{Private preflight}
    C -->|Pass| D[Private hoopDB]
    C -->|Fail| E[No export]
```

`miner`는 public, 실제 후보 저장소인 `hoopDB`는 private이라는 경계를 유지합니다.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
```

수집만 확인하려면:

```bash
python scripts/run_miner.py --budget 50 --no-export
```

V3 dry-run:

```bash
python scripts/run_distill_v3.py \
  --inbox-jsonl tests/fixtures/v3/inbox.jsonl \
  --state-dir _v3_state \
  --batch-size 100 \
  --dry-run
```

현재 dry-run은 semantic ACCEPT, canonical write, private repository write, GPU 실행을 하지 않습니다.

운영 설정과 secret 권한, live-export gate는 `docs/OPERATIONS.md`에 정리되어 있습니다.
