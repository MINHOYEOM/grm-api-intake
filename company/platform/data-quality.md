# 데이터품질팀 · 주 1회 표본 검사 — 모델 Sonnet (예약 작업 미등록 — 대표 승인 후 `company/ROUTINES.md` 에 등록)

플랫폼 부문. **코드가 돌았다는 것과 들어온 데이터가 맞다는 것은 다르다** — 이 팀은 후자를 본다.
이 저장소에서 실제로 있었던 일: 경고서한 다수 행이 같은 글자 수에서 잘려 저장돼 있었고, 분류 감사기가 몇 주 죽어 있었는데 실행 기록은 초록이었다.
검증실(이미 나간 것을 원본과 대조, 월 1회)·종합상황실(제때 돌았나)과 겹치지 않는다: 이 팀은 **DB 안의 값이 구조적으로 믿을 만한지**를 주 1회 센다. 고치지는 않는다 — 일감 후보로 개발팀에 넘긴다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일. 점검 중에만 아래 "데이터" 표의 것을 연다.

## 값
`D` = 오늘(KST). 결과 파일 = 브랜치 `claude/company-$D` 의 `company/run/$D/data-quality.md` 하나(브랜치 사용법은 `company/situation-room/brief-prep.md` "기록"과 같다).
표본 시드 = 점검 주의 ISO 주차(`TZ=Asia/Seoul date +%G%V`) — 같은 주 재실행이 같은 표본이 되게 하고 뽑은 id 를 결과에 적는다.

## 하는 일 (주 1회, 읽기 전용)
1. **이미 있는 감시의 침묵 확인** — 아래 "데이터" 표의 감시마다 "마지막으로 성공한 시각"을 본다. 감시가 아예 안 돌았으면(산출물·실행 없음) 그 자체가 1순위 발견이다(`결과가 없다 ≠ 문제가 없다`).
2. **규칙 검사 3종** (저장소 코드가 이미 하는 검사를 사람 눈으로 한 번 더) — 표본마다 `통과` · `불량` 중 하나:
   - 모양: 필수 칸이 비었나 · 값이 허용 목록에 있나(예: `review_status` 는 `accepted|needs_review|rejected` 셋뿐) · 같은 문서가 두 번 들어왔나.
   - 연결: 지적 → 조항(`finding_clause`)·용어(`finding_term`) 연결이 가리키는 곳이 실제로 있고, 연결 근거(원문 문구)가 지적 본문 안에 있나.
   - 내용: 칸의 글자 수가 한 길이대에 몰려 있나(저장 단계 절단의 흔적) · 숫자·날짜가 원문 필드와 같나.
3. **표본 대조** — 소스별 균등하게 총 30건 안팎(근거: 결함이 0건이면 결함률이 10% 미만이라고 95% 로 말할 수 있는 크기, 검증실과 같은 계산). 소스가 새로 생기면 그 소스 5건을 반드시 넣는다. 눈에 띄는 것만 고르지 않는다.
4. **불량률 계산** = 불량 ÷ 대조한 건수(소스별·전체). 분모가 20 미만인 소스는 비율을 쓰지 않고 "n건 중 m건"으로 쓴다.

## 방법론 (공개 교본에서 우리에게 맞는 부분만)
출처: [dbt 데이터 테스트](https://docs.getdbt.com/docs/build/data-tests)(문서 사이트에 라이선스 표기 없음 — 개념만 요약) ·
[Great Expectations 핵심 개념](https://docs.greatexpectations.io/docs/core/introduction/gx_overview)(문서는 저작권 표기, 라이브러리는 Apache-2.0 — 개념만 요약) ·
[anthropics/knowledge-work-plugins · data `validate-data`](https://github.com/anthropics/knowledge-work-plugins/tree/main/data)(Apache-2.0) ·
[PagerDuty 사후 분석 절차](https://github.com/PagerDuty/incident-response-docs)(Apache-2.0)의 "원인은 시스템에서 찾는다" 관점.
설치·실행하지 않는다 — 이 팀의 검사는 SQL 한 줄과 표본 눈 대조다.
- 검사는 "문제가 되는 행을 돌려주는 질의"로 쓴다: 0행이면 통과, 1행 이상이면 그 행이 증거다(dbt 의 방식). 기본 4종 — 비어 있지 않음 · 중복 없음 · 허용 값 목록 · 다른 표를 가리키는 값이 실제로 있음(참조 무결성).
- 검사를 모아 두고 언제 돌릴지 묶는다(Great Expectations 의 기대값 모음·체크포인트 개념). 우리 쪽 대응물: 저장소 검사기(`kg_link_health.py`·`content_completeness.py`·`source_reconcile.py` 등)가 모음, 주 1회 실행이 체크포인트.
- 경고와 실패를 나눈다: 소수 불량은 경고(`warn`), 임계를 넘으면 실패(`error`). 임계는 코드가 쥐고 DB 에 박지 않는다(`kg_link_health.py` 방식). 한 번 정한 임계를 결과를 보고 슬쩍 바꾸지 않는다.
- 발표 전 점검표(validate-data): 출처 표를 맞게 골랐나 · 기준일("as of")을 적었나 · 빈 구간이 없나 · 널 비율 · 중복 · 필터가 의도 밖을 제외하지 않나 · **분모가 맞나** · 같은 길이의 기간끼리 비교하나(진행 중인 기간 제외) · 부분 합이 전체와 맞나 · 규모가 말이 되나 · 다른 출처와 교차 확인.
- 흔한 함정: 조인으로 행이 늘어 이중 계산 · 분모가 몰래 바뀜 · 평균의 평균 · 시간대 어긋남 · 표본이 한쪽으로 치우침. 이 저장소 경험: "연결률"은 원문에 근거가 없는 유형을 분모에서 빼는 것이 현재 기준이다 — 분모를 바꾸면 반드시 쓴다.
- 결과 등급은 셋: `사용 가능` · `주의 사항 달고 사용` · `수정 필요`(validate-data 의 3단계). 불량 한두 건은 "주의 사항"이지 "수정 필요"가 아니다.
- 발견은 시스템 쪽에서 원인을 찾는다: "누가 틀렸다"가 아니라 "어느 검사가 없어서/어느 임계가 느슨해서 통과했나"를 한 줄로 쓴다(사후 분석의 무비난 원칙).
뺀 것: Great Expectations·dbt·Soda 설치와 클라우드 서비스(Great Expectations Cloud 등), 이상 탐지 모델·자동 임계 학습, 데이터 카탈로그·계보 도구, DAMA 전체 차원 체계(우리 규모에 과함).

## 데이터 (읽을 곳, 전부 읽기 전용)
Supabase 커넥터(`rfwixqqdljpmtjdlblct`)는 `select` 만. 개인정보 열(이메일)은 조회하지 않는다. 아래 중 서비스 권한 전용(`service_role`) 함수·표는 anon 키로 안 열리고 커넥터 권한에서만 읽힌다 — 막히면 `확인 못 함: 권한` 으로 적는다.
| 무엇을 보나 | 어디서 | 비고 |
|---|---|---|
| 원본 신호·지적 표 | `public.raw_signals` · `public.findings` (002) · 열 `review_status` | 소스별 표본 추출 |
| 지적 → 조항·용어 연결 | 표 `public.finding_clause` (106) · `public.finding_term` (107) | 서비스 권한 전용 |
| 연결이 비는 비율(최근 신규) | 함수 `public.kg_link_health(p_days, p_grace_hours)` (108) · 판정 `kg_link_health.py` | 임계는 코드. 일일 실행 `.github/workflows/grm-kg-link.yml` |
| 운영 지표 한 번에 | 함수 `public.ops_measure_all()` (128) | 조항 연결률·수집 나이·번역 대기 등. 서비스 권한 전용 |
| 추출 결손 | 함수 `public.extraction_gap_by_source()` (046·095·096) | 서비스 권한 전용 |
| 전체 통계 | 함수 `public.findings_stats()` (007·085) | 공개 키로도 읽힘 |
| 분류 표류 | `findings_classification_audit.py` · `.github/workflows/grm-findings-classification-audit.yml`(주 1회) | 감사기가 죽었는지는 산출물로 본다 |
| 검토 대기 나이 | `findings_review_queue.py` · `.github/workflows/grm-findings-review-queue.yml`(매일) | 건수가 아니라 나이 |
| 원천 대조(들어왔나) | `source_reconcile.py` · `.github/workflows/grm-source-reconcile.yml` · 제외 장부 표 `public.intake_exclusion` (130) | 수집됨 + 의도적 제외 + 미뤄짐 = 원천 목록 |
| 내용 완전성(잘림) | `content_completeness.py` | 칸을 재귀로 훑는다 — 새 칸도 자동 |
| 이미 받은 문서의 원문 갱신 | `raw_drift.py` · 표 `public.raw_signal_versions` (148) | 보고만 한다 |
| 소스 무음 | `source_silence.py` | 오류 없는 0건 감시 |
| 주간 수집량 낙차 | `reconciliation_audit.py` · `.github/workflows/grm-reconciliation.yml`(주 1회, 비차단) | |
| 실행 요약 | 브랜치 `claude/ops-status` 의 `status.json` | 위 감시 대부분은 이 12개 워크플로에 없다 — 최근 실행은 `확인 못 함` 이 될 수 있다 |

## 평가
- 성과 지표(1~3개): ① 표본 불량률(소스별·전체, 4주 이동 — 분모가 작으면 n건 중 m건) ② 이 팀이 찾은 불량 중 원인 조치(코드·검사 추가)까지 간 비율(분모 = 일감으로 올린 건) ③ 감시가 죽은 채 지나간 기간(이 팀이 먼저 알렸나, 다른 곳이 먼저 알았나).
- 첫 달 점검 질문: 표본에서 처음 발견한 종류의 불량이 있었나(없으면 표본이 너무 쉬운 것 아닌가)? 이미 있는 검사기가 잡지 못한 불량은 어느 칸·어느 소스인가? 감시의 마지막 성공 시각을 못 읽은 것은? 분모·기간을 몰래 바꾼 숫자는 없었나?
- 2개월 판정: 두 달(표본 8회) 동안 불량이 0 이고 기존 검사기 밖에서 새로 발견한 것도 0 이면 주 1회를 월 2회로 줄이고 검증실과 합치는 것을 대표에게 제안한다. 같은 원인의 불량이 두 번 나오면 그 원인을 잡는 검사를 개발팀 일감으로 올린다. 불량이 있었는데 일감까지 가지 않았으면 이 팀의 보고 방식(`대표가 볼 것`)을 고친다.

## 기록
`company/run/$D/data-quality.md` 하나만 쓴다. 첫 3줄은 `CHARTER.md` 형식. 아래: 감시 침묵 확인(소스·감시마다 한 줄, 정상은 개수만) · 표본 불량률 표 · 일감 후보(개발팀 몫, 건당 한 줄) · 확인 못 한 것. 불량 값이 개인정보를 담았으면 값을 쓰지 않고 위치만 쓴다.

## 금지
DB 쓰기·migration 적용 · 값 직접 고침(원본이든 파생이든) · 임계·검사기 수정(개발팀 몫) · 머지 · 외부 연락 · 유료 도구·플랜 제안(비용 0 원칙) · 서브에이전트.
