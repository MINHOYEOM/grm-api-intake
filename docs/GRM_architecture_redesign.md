# GRM 아키텍처 재설계 — Python-thick / Routine-thin (목표: v16)

> 작성: 2026-06-04 · 작성자: Claude(설계) → Codex(구현) 핸드오프용
> 전제: 2026-06-04 v15.7 1차 라이브 실행에서 단일 컨텍스트 폭주로 출력 포맷 폴백·Status 누락 발생(LV-15.7a).
> 결론: 프롬프트를 더 키우거나 쪼개는 것으로는 안 된다. **일을 재배치**한다.
> 정합: `feature/multi-modality`(v15.8, `compute_modality`)와 직교 — 본 설계는 "어디서 처리하느냐"이고 제형 확장은 "무엇을 분류·서술하느냐"라 충돌하지 않는다.

---

## 1. 문제 정의 (왜 바꾸나)

현재 Routine 은 한 번의 LLM 컨텍스트 안에서 다음을 전부 한다:
핸드오프 파싱 · WebSearch 14회 · 중복제거 · 13카테고리/Tier 판정 · **표·메타·듀얼링크·배지·인용 생성(템플릿팅)** · 8~N개 카드 작성 · Status 갱신 · handoff 마감 · Lint.

1차 라이브(2026-06-04, 49행)에서 컨텍스트 압축이 일어나며 (a) 1,200줄 카드 포맷 규칙과 (b) 행별 page_id 가 동시에 드롭됐다. 결과: 출력이 일반 마크다운+금지 문법으로 폴백, Status 미갱신. page_id 는 핸드오프 JSON 에 분명히 있었다 — 데이터 부재가 아니라 **결정론적 작업을 LLM 컨텍스트에 올린 게 원인**.

핵심 진단: **LLM 에게 판단(산문)뿐 아니라 템플릿 엔진·링크 빌더·상태 관리자 역할까지 시켜서 터졌다.**

## 2. 설계 원칙

> LLM 은 LLM 만 할 수 있는 것(판단이 필요한 산문)만 한다. 그 외 전부 코드.

- **결정론적 = Python**: 표, 메타, 듀얼링크, raw 인용(필드 복사), 배지, 면책, 제형 분류, Tier, 중복제거, 노이즈 필터, Status 갱신, handoff 마감, Lint 의 기계 검사 부분.
- **판단 = LLM(Claude Routine)**: 한 줄 요약(W1), 시사점(W6), 점검사항(W7), 그리고 Intake 에 없는 글로벌/Watch 이벤트의 WebSearch 탐지·요약.
- **컨텍스트 최소화**: LLM 은 "카드 1장 = 1행"을 입력받아 산문 슬롯만 채운다. 전체 포맷 스펙이 컨텍스트에 상주하지 않는다 → 압축 폭주 구조적으로 불가능.
- **검증 가능**: Python 으로 내린 모든 것은 기존 CI(`grm-ci.yml`)+unittest 로 회귀 검증된다.

## 3. 책임 분리표 (핵심)

| 작업 | 현재 | 목표(v16) | 비고 |
|---|---|---|---|
| 수집·소스 8+3 | Python | **Python** | 변화 없음 |
| 노이즈 필터(식품 WL 등) | Python(버그) | **Python(수정)** | §7 |
| 제형 분류(modality) | Python(`compute_modality`) | **Python** | v15.8 유지 |
| Tier·QA relevance·13카테고리 | Python 휴리스틱 + LLM 재판정 | **Python 확정**, LLM 은 경계행만 재판정 | LLM 부하↓ |
| 중복제거(Intake/Search/Fetch) | LLM | **Python(doc_id 키)** + LLM 은 의미 중복만 | |
| 한눈에 표·W2 메타표 | LLM | **Python 조립** | 결정론 |
| 듀얼링크(📰/📎) | LLM | **Python 조립**(official_url 필드) | 가짜 링크 원천 차단 |
| raw 인용(W3) | LLM | **Python 복사**(raw 필드 그대로) | 위조 불가 |
| 배지·면책·범례·메타(M2/M3) | LLM | **Python 조립** | |
| **W1 한 줄 요약** | LLM | **LLM(카드별)** | 판단 |
| **W6 시사점** | LLM | **LLM(카드별)** | 판단 |
| **W7 점검사항** | LLM | **LLM(카드별)** | 판단 |
| WebSearch(Intake 외 이벤트·Watch) | LLM | **LLM** | 탐지 |
| Status 갱신·handoff CONSUMED | LLM | **Python** | page_id 보유 주체가 처리 |
| Publish Lint | LLM | **Python(구조 검사) + LLM(의미 검사)** | 이중 |

요지: **카드의 "뼈대"는 Python 이 완성하고, LLM 은 "살(산문 3슬롯)"만 붙인다.**

## 4. 데이터 계약 (collector → routine)

수집기가 만드는 핸드오프를 "New-only 큐"에서 **"조립된 브리프 초안(scaffold)"**으로 격상한다.

핸드오프 `rows[]` 각 행은 page_id·official_url·tier·modality(조건부)를 보유한다(Codex 검증).
⚠️ **정정**: v1 rows 에 `raw` 는 없다 — raw 는 원 row 본문 code block 에만 있다. 따라서 v2 생성 시
**page_id 로 원 row children 을 fetch 해 raw JSON 을 각 row 에 붙이는 단계가 선행**돼야 한다(raw 의존 칸의 전제).
그 위에서 수집기가 추가로 산출:
- `card_scaffold`: 카드별로 Python 이 완성한 W2 메타표·듀얼링크·raw 인용(W3)·배지·제형 라벨 마크다운 + 비워둔 산문 슬롯 토큰 `{{W1}}`·`{{W6}}`·`{{W7}}`.
- `prose_input`: LLM 이 그 슬롯을 채우는 데 필요한 최소 컨텍스트(제품/사유/제형/조항/결론 핵심 필드만, raw 전체 아님).
- `section`: `global` / `domestic(MFDS)` / `watch` / `recall_table` 사전 분류.
- `card_id`: 안정적 식별자(= source::document_id).

스키마 버전 `grm-routine-handoff/v2`. v1 과 하위호환(없는 필드는 LLM 폴백).

## 5. 새 데이터 흐름

```
[Python 수집기 — 매일 + 월요일 조립]
 수집 → 노이즈필터 → 제형/Tier/카테고리 확정 → 중복제거 →
 카드 scaffold 조립(표·링크·인용·배지) → handoff v2(scaffold + prose 슬롯 비움)

[Claude Routine — 월요일, 얇음]
 0. handoff v2 읽기 (작은 prose_input 만)
 1. (선택) Intake 외 글로벌/Watch WebSearch 탐지
 2. 카드별 루프: prose_input 1건 → {{W1}}/{{W6}}/{{W7}} 채움  ← 컨텍스트 카드 1장 크기
 3. 채운 산문을 scaffold 슬롯에 삽입(치환만)
 4. 페이지 발행

[Python(또는 Routine 말미) — 마감]
 5. Lint(구조 자동검사) → 통과 시 Status=Processed + handoff CONSUMED
```

핵심: 2단계가 카드별 독립 호출이라 컨텍스트가 절대 누적되지 않는다. 50장이든 5장이든 각 호출 크기는 동일.

## 6. 왜 "기능적으로 더 훌륭"해지나 (단지 안정성이 아니라)

- **포맷 100% 일관**: 표·링크·배지가 코드 산출이라 깨질 수 없다. 가짜 링크·위조 인용 원천 차단.
- **산문 품질↑**: LLM 이 카드 1장에 온전히 집중(압축에 쫓기지 않음) → 시사점·점검사항이 더 깊고 정확. 제형(modality)·조항·결론을 정확히 반영.
- **제형 맞춤**: `compute_modality` 결과가 prose_input 에 들어가, 무균/바이오/경구액상별로 다른 관점의 시사점·점검을 LLM 이 작성.
- **누락 0**: Tier 3·page_id 가 코드 관리라 카드 누락·Status 누락이 사라진다.
- **테스트 가능**: scaffold·링크·필터가 unittest 대상 → 회귀 안전. 사람이 매주 눈으로 안 잡아도 됨.
- **확장 쉬움**: 새 소스/제형은 Python 분류만 추가하면 scaffold 가 자동 흡수.

## 7. 노이즈 필터 — Python 수정 명세 (Codex 핸드오프)

증상(2026-06-04): FDA WL Intake 11건 중 다수가 식품 WL(베이커리·수산·보바)인데 차단 안 되고 Tier 3/1 로 적재.
원인: v1.7 필터가 본문 키워드만 보고 **WL 발행 부서(center/office)**를 안 봄.
근거(Codex): FDA WL 파서가 실제 `issuing_office` 를 파싱·raw 저장한다(collect_intake.py ~1787/1825).
현재 필터는 부서 전용 게이트가 아니라 keyword list 이고 `Human Foods Program·CFSAN·CVM` 만 있고
`Office of Inspections and Investigations(OII)·CTP·CDRH` 는 빠져 있다(~256).
수정: `issuing_office` 기반 게이트로 보강.
- **무조건 제외**: `CVM`, `CTP`, `CDRH`, `CFSAN`, `Human Foods Program`.
- **맥락 제외(주의)**: `Office of Inspections and Investigations` 는 **식품/수산/HACCP/FSVP 맥락일 때만** 제외.
  OII + finished pharmaceuticals/cGMP 는 유지(또는 Needs Review) — 약품 CGMP WL 오삭제 방지.
- **유지**: `CDER`. `CBER` 는 유지(또는 제형/카테고리 2차판단).
- 부서 결측 시 기존 본문 keyword(FSVP/HACCP→제외, finished pharmaceuticals/cGMP→유지) 폴백.
- alias normalize 필요: `CDER` ↔ `Center for Drug Evaluation and Research (CDER)` 등 약어·괄호·대소문자.
- **unittest(Codex 제안)**: HFP/CFSAN+HACCP→제외 · CVM/CTP/CDRH→제외 · CDER+finished/cGMP→유지 ·
  CBER+biologics/aseptic→유지 · OII+seafood/HACCP→제외 · OII+finished/cGMP→유지(또는 Needs Review) ·
  office 결측+FSVP→제외 · office 결측+finished/cGMP→유지.
- 효과: Intake 오염·오티어링·handoff 노이즈가 입구에서 제거 → Routine 이 매주 LLM 으로 거를 필요 없음.

## 8. 마이그레이션 (비파괴 단계)

기존을 멈추지 않고 점진 이행. 각 단계는 독립 배포·롤백 가능.

- **M0 (즉시·작음)**: §7 노이즈 필터 부서 게이트 + unittest. (현 시스템에 바로 이득)
- **M1**: 핸드오프에 page_id 가 이미 있으니, Routine 프롬프트(v15.8)에 "0단계에서 page_id 목록 먼저 추출·고정 → Status 는 독립 최종 단계" + "카드 포맷 출력계약을 프롬프트 말미 재배치"를 반영(임시 방어선, 구조 전환 전까지 폴백 방지).
- **M2 (핵심)**: 수집기에 `build_card_scaffold()` 추가 → handoff v2(scaffold + prose 슬롯). Python 단위테스트로 scaffold 검증.
- **M3**: Routine 을 얇게 교체 — scaffold 읽고 카드별 산문만 채워 발행. 1,200줄 포맷 스펙을 프롬프트에서 제거(스펙은 Python 으로 이동).
- **M4**: Lint 구조 검사를 Python 으로(발행 전 자동), LLM Lint 는 의미 검사만. Status·CONSUMED 를 Python 마감으로.
- **(선택) M5 — 최대 자동화**: 산문 슬롯도 수집기 내 Haiku API 로 채워 수집기가 완성 브리프를 직접 발행. Routine 은 Watch 보강·예외 처리만. 트레이드오프: 산문 품질이 인터랙티브 Claude 대비 낮을 수 있어 M3 안정화 후 A/B 비교로 결정.

권장 정지점: **M0~M4 까지가 "더 훌륭 + 더 안정"의 본체**. M5 는 품질 비교 후 결정.

## 9. 결정 필요 / 미해결

- M5(수집기 내 Haiku 발행) 채택 여부 — 산문 품질 vs 완전 자동화. M3 후 A/B.
- ~~scaffold 를 핸드오프 본문(JSON)에 넣을지, 별도 "draft brief" Notion 페이지로~~ → **결정(2026-06-05): 핸드오프 JSON 본문 `rows[]` 에 `card_scaffold` 필드로 포함(additive v2).** 구조 단순·Routine 단일 입력·v1 하위호환 용이. (별도 draft 페이지는 페이지 1개 증가 + Routine 이 두 곳을 봐야 함.)
- 멀티 사용자 배포 시 발행 주체(수집기 vs Routine) 일원화.

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-04 | 최초 작성. v15.7 라이브 폭주(LV-15.7a)·노이즈필터 갭(LV-15.7b) 대응 목표 아키텍처. Python-thick/Routine-thin 책임분리·handoff v2 계약·카드별 산문 루프·마이그레이션 M0~M5 |
| 2026-06-05 | §9 미해결 1건 결정: scaffold 저장 위치 = **핸드오프 JSON 본문 `rows[]` 포함(additive v2)**. K2 착수(별도 채팅): M0 부서게이트 → K2-prep raw fetch → `build_card_scaffold()` 순수함수 + golden → handoff v2 단계 게이트. 지시문 `archive/point-in-time/GRM_Keystone_K2_ClaudeCode_지시.md` |
