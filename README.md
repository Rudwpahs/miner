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

## Daily target

production Miner는 매시간 `07`, `27`, `47`분에 실행 기회를 가집니다. 각 실행은 `config/miner_target.json`의 `daily_target`을 읽고, 서울 날짜 기준 오늘 누적 수집량이 목표에 도달하면 source API 호출 전에 즉시 종료합니다.

목표량을 바꾸려면 Python 코드나 workflow를 수정하지 않고 아래 파일의 `daily_target` 숫자 하나만 변경합니다.

```text
config/miner_target.json
```

마지막 실행에서는 남은 quota보다 더 많은 source record를 fetch하지 않기 때문에 목표량을 넘기기 위해 checkpoint를 앞당기지 않습니다. GitHub cron 지연, 외부 API rate limit, 실제 관련 후보 공급량에 따라 특정 날짜의 실제 수집량은 목표보다 적을 수 있습니다.

## Miner Live

공개 dashboard는 후보 원문이 아니라 다음 **집계값만** 보여줍니다.

- 현재 총 수집 데이터
- 증류 대기
- 증류 성공
- 오늘 수집량 / 오늘 목표량
- 최근 7일 일별 수집량
- 마지막 Miner 실행 시각
- 마지막 증류 성공 시각
- 현재 시스템 상태

브라우저는 같은 origin의 `status.json`만 읽으며 candidate title, URL/DOI, author, summary, candidate ID, canonical hash, queue ID, knowledge-unit text는 공개 asset에 포함하지 않습니다. 상태 JSON은 GitHub Actions가 private `Rudwpahs/hoopDB`를 서버측에서 읽어 숫자로 축약한 뒤 GitHub Pages에 배포합니다.

dashboard는 5분 주기로 reconcile되고, 브라우저는 60초마다 최신 `status.json`을 다시 확인합니다. 새 상태를 읽지 못하면 화면에 `STALE` 상태를 표시합니다.

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
- 실제 canonical promotion은 Auditor integration만 수행

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
