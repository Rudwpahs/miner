# Basketball Knowledge Miner

농구 관련 자료를 많이 모으되, 아무 자료나 바로 FormPath 지식으로 넣지 않기 위해 만든 공개 수집기입니다. 이 저장소는 **수집과 1차 정리**를 담당하고, 실제 후보 데이터는 비공개 `Rudwpahs/hoopDB`로 넘깁니다.

## V1에서 보는 소스

- Crossref 학술 메타데이터
- 명시적으로 허용한 YouTube 코칭 / 전문가 채널
- 같은 provenance·rate-limit·fixture 검증을 통과한 추가 공식/API 소스

Reddit 전체 스크래핑, paywall 우회, anti-bot 우회, 원본 영상, 전체 transcript, 전체 저작권 기사, private FormPath 연구 내용은 이 저장소의 수집 대상이 아닙니다.

## 수집 알고리즘

```text
source adapter에서 메타데이터 읽기
        ↓
공통 candidate 형식으로 normalize
        ↓
농구 관련성 검사
        ↓
불필요한 필드 제거
        ↓
이미 본 항목과 exact dedup
        ↓
통과한 candidate만 export 대상으로 구성
        ↓
대상 GitHub 저장소가 private인지 preflight
        ↓
private이면 hoopDB inbox로 export
private이 아니면 중단
```

즉, 많이 긁어오는 것이 목표가 아니라 **출처가 남고, 중복이 줄어들고, 공개/비공개 경계를 넘지 않는 후보 데이터**를 만드는 것이 목표입니다.

## 스케줄과 한도

production workflow는 `17 */3 * * *` UTC로 하루 8회 실행되도록 구성되어 있고, 한 번에 최대 20,000개 source record를 검사합니다. 이론상 하루 최대 160,000개를 볼 수 있지만 실제 후보 수는 관련성 검사와 중복 제거 때문에 더 적습니다.

GitHub cron은 정확한 시각보다 늦게 시작될 수 있고 외부 API 정책·quota도 바뀔 수 있습니다.

## Distillation V3 알고리즘

V3 core는 의미 판단을 직접 하지 않고, **어떤 단계가 무엇을 할 수 있는지**를 강제로 제한합니다.

```text
RAW INBOX
   ↓ deterministic validation
VALIDATED
   ↓ exact dedup + lease
TRIAGE
   ├─ REJECT 가능
   └─ ACCEPT 불가
        ↓
DEEP REVIEW
   ├─ REJECT
   └─ PROPOSE_ACCEPT
        ↓
JUDGE
   ├─ REJECT
   └─ CONFIRM
        ↓
IMMUTABLE STAGING
        ↓
future AUDITOR / promotion layer
        ↓
CANONICAL KNOWLEDGE
```

핵심 규칙은 다음과 같습니다.

- raw inbox에서 canonical로 바로 갈 수 없음
- Triage는 ACCEPT할 수 없음
- Deep의 `PROPOSE_ACCEPT`만으로 승인되지 않음
- Judge의 `CONFIRM`이 필요함
- staging은 같은 byte의 재시도는 허용하지만 내용이 바뀐 overwrite는 거부
- 실제 canonical promotion은 future Auditor integration만 수행

## V3 dry-run

```bash
python scripts/run_distill_v3.py \
  --inbox-jsonl tests/fixtures/v3/inbox.jsonl \
  --state-dir _v3_state \
  --batch-size 100 \
  --dry-run
```

현재 dry-run은 semantic ACCEPT, canonical write, private repository write, GPU 실행을 하지 않습니다.

## 개발

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
```

수집만 확인하려면:

```bash
python scripts/run_miner.py --budget 50 --no-export
```

이 모드는 aggregate counter만 출력하고 candidate payload를 저장하지 않습니다.

운영 설정과 secret 권한, live-export gate는 `docs/OPERATIONS.md`에 정리되어 있습니다.
