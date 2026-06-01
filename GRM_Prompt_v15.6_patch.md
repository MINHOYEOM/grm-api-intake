# GRM Routine v15.6 수정안 (v15.5 → v15.6 patch)

**작성:** 2026-06-01 · Claude(Routine/E축) · 우선순위 합의(Codex): **Routine 먼저 확정 → 코드 잔재 제거** · Codex 코드 트랙 반영 후 상태 갱신
**목표:** ① MFDS(8번째 소스) 인지 ② KO 항목 한글 유지(내부 충돌 해소) ③ gmp-inspection 지적사항 본문 LLM 요약 ④ Self-Check 출력 잔재 제거 ⑤ Signal Tier/Region 활용 + **국내/글로벌 2단 섹션**
**적용법:** 아래 P1~P9 패치를 `GRM_Prompt_v15.5.md`에 순서대로 반영하면 `GRM_Prompt_v15.6.md` 완성. 라인 번호는 v15.5 기준.

---

## 변경 요약 (delta 표 — A절 표에 행 추가)

| 영역 | v15.5 | v15.6 |
|---|---|---|
| 수집 소스 | 7개(글로벌) | **8개** (+MFDS: 국내 제조/품질) |
| 섹션 구조 | 단일 글로벌 흐름 | **국내(🇰🇷 MFDS) / 글로벌(🌐) 2단 분리** |
| 언어 | 영문 원문+한국어 번역 병기 | **Language=KO 항목은 한글 원문 유지, 번역·영문병기 없음** |
| GMP 실사 | 해당 없음 | **gmp-inspection 지적사항 본문(attachment_text)→분야/failure mode LLM 요약** |
| Self-Check | 메타에 자가점검 서술 잔존 | **자기검증 서술 제거**(커버리지 사실 카운트는 유지) |
| 버전 라벨 | v15.5 Intake-first daily | **v15.6 Intake-first daily (+MFDS)** |

---

## P1 — [역할] MFDS 제조/품질 포함 (L26-28)

**기존:**
```
한국 규제는 사내 RA가 담당하므로 이 다이제스트는
글로벌 규제 변화 중심. FDA · EMA · TGA · MHRA · PIC/S · ICH 등 주요 규제기관을 균형 있게 모니터링.
```
**교체:**
```
글로벌 규제 변화(FDA · EMA · TGA · MHRA · PIC/S · ICH)를 균형 있게 모니터링하되,
식약처(MFDS) 제조/품질 신호(GMP 실태조사·행정처분·회수·고시/지침·입법예고·안전성서한)도
QA 직접 관련 항목으로 포함한다. 일반 인허가 정책은 사내 RA 영역이나, 제조소 GMP·품질 결함·
제재·회수는 QA 다이제스트 범위다.
```

---

## P2 — [핵심원칙] 8개 소스 + KO 충돌 1차 해소 (L31, L39-43)

**P2-a · L31 [핵심원칙]1 교체:**
```
1. 원문 인용 우선: 사실은 원문 + (영문 원문일 때) 한국어 번역 병기 (Evidence Level A에 한정).
   ⚠️ Language=KO 항목(MFDS 등 한국어 원문 소스)은 한글이 이미 원본이므로 번역·영문 병기를
   하지 않는다(P5 참조). quote 는 한글 원문을 그대로 사용한다.
```

**P2-b · L39-43 [핵심원칙]6 교체:**
```
6. 운영 모델 (v15.6 — Intake-first cloud routine, daily collection):
   외부 GitHub Actions 수집기가 매일 20:17 UTC (익일 05:17 KST) 에
   8개 소스를 수집해 결과를 Notion "GRM API Intake" 데이터베이스에 raw 필드로 적재한다.
   수집 소스: Federal Register API · OpenFDA Recall API · EMA RSS · MHRA RSS ·
   PIC/S RSS · ECA RSS · FDA Warning Letters scrape · MFDS(식약처: RSS 다보드 +
   data.go.kr 회수·행정처분 API + nedrug GMP 실사 스크래핑).
```
(이하 L44-49 동문, "7개"→"8개" 일괄 치환)

---

## P3 — [0단계 Intake 읽기] Source 필터·MFDS Type 인지 (L152-168)

**L162 교체** (기존 `(Source 필터 없음 — 7개 소스 전체 조회)`):
```
(Source 필터 없음 — 8개 소스 전체 조회. MFDS row 는 Source=MFDS 로 식별)
⚠️ Intake 조회 도구 분기 (2026-06-01 Claude·Codex 양측 실측 반영):
   STEP 0 — 필터드 쿼리 단발 검증(필수): query_data_sources 계열 도구를 1회 시험 호출한다.
     · 성공 → (A) 경로로 진행.
     · 실패(예: "Tool notion-query-data-sources not found" — Claude·Codex 환경 양쪽에서 확인됨)
       → 즉시 (B) MFDS fallback 조회를 반드시 수행한다. (도구 부재를 "가용하면 우선"으로
         흘려보내지 말 것 — 검증 없이 (A)를 가정하면 MFDS 0건 브리프가 재발한다.)
   (A) 필터드 쿼리 가용 시: Status=New + Run Date window 속성 필터로 정확 조회. (정상 경로)
   (B) 필터드 쿼리 부재 시(현 Claude Routine 실행환경 = 부재 확인됨):
       아래 'MFDS fallback 조회'를 **생략 불가**로 수행한다:
       · data_source_url = collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288 로 스코프 고정
       · MFDS 전용 분할 쿼리를 별도 1회 이상 실행(query 예: "MFDS 행정처분 회수 GMP 실태조사")
         — 글로벌 항목에 밀려 MFDS 가 25건 컷오프에서 누락되는 것을 방지.
       · created_date_range.start = 실행일-9 (createdTime 기준 — Run Date 속성 필터가 아님에 유의)
       · 반환은 최대 25건/페이지·커서 없음 → 소스별 분할로 보완. 25건 도달 시 M3에 "절단 가능" 기록.
   ⚠️ (B) 경로는 Status·Run Date 속성을 못 거른다 → 흡수 후 각 row 페이지를 fetch 해
       Status=New 인지, Run Date 가 window 내인지 본문에서 직접 확인한 뒤 카드화한다.
```

**L182-184 흡수 속성 목록에 추가:**
```
   · MFDS row 는 추가로 Type or Class · Language · Region/Jurisdiction · Body(지적사항/사유 본문) 흡수.
```

---

## P4 — [신규 섹션] MFDS Type→테마 매핑 + gmp-inspection 본문 요약

> 삽입 위치: [필터 — 포함 (13개 카테고리)] (L562) 바로 앞에 신규 블록으로.

```
[MFDS 항목 처리 — v15.6 신규]
Source=MFDS 항목은 Type or Class 값으로 국내 섹션 내 테마를 부여한다(매핑에 없는 Type는
'Type 원문' 제목으로 별도 그룹 — 절대 누락 금지):
  admin-action        → 행정처분
  recall-quality      → 회수·판매중지
  gmp-inspection      → GMP 실태조사
  guidance-industry   → 지침·안내서
  guidance-internal   → 공무원지침서
  gmp-guideline       → 지침·안내서   (legacy/기존 옵션 — 누락 방지)
  regulation-final    → 개정 법령
  notice-final        → 고시
  legislative-notice  → 입법예고
  safety-letter       → 안전성서한
  gmp-certificate     → GMP 적합판정

[gmp-inspection 지적사항 요약 — v15.6 신규]
gmp-inspection 카드는 Body / Raw payload 의 attachment_text(실사구분·소재지·대상제형·
주요 지적사항·결론)를 직접 읽어 다음을 산출한다:
1. 제조소 소재국(Body `국가:` 또는 address) · 실사 구분(사전/사후 — 보드 컬럼 값) · 제형.
2. 지적사항이 있으면 GMP 분야로 분류해 1~2줄 요약:
   무균/멸균 · 공정·세척 밸리데이션 · 데이터 인테그리티(CSV/Part11) · 환경모니터링 ·
   품질관리(QC)시험 · 안정성 · 문서/SOP · 시설·설비 · 위탁시험/공급업체.
   각 지적을 "분야 — failure mode(무엇이 왜)" 형태로 적는다.
3. 결론(적합/보완요구/부적합)을 명시. 수집기 keyword flag(attachment_deficiency_assessment:
   none/present/unknown)는 보조 신호로만 사용하고, unknown·none 이어도 본문을 직접 읽어 판단한다.
   ("지적(보완)사항 없음" 명시 시 '지적사항 없음'으로 적고 분야 분류 생략.)
※ 이 규칙이 nedrug 본문 구조화(2d-2b)를 LLM 요약으로 대체한다. quote 는 한글 원문만.
```

---

## P5 — [Evidence Level / 한국어 번역] KO 항목 구조 충돌 해소 (L416-482)

**P5-a · L437-439 Evidence A quote 규칙에 추가:**
```
   ⚠️ Language=KO 항목(MFDS): 원문이 한글이므로 한글 원문 필드값에서 핵심 원문만 quote 한다.
   영문 quote·영문 병기를 만들지 않는다.
   ⚠️ quote 분량 제한: gmp-inspection attachment_text·행정처분 EXPOSE_CONT 등은 길 수 있으므로
   quote 는 **핵심 원문 1~3줄만** 인용한다(주요 지적사항/결론/사유 핵심). 전문은 블록 W7
   Raw payload toggle 에 보존하고 quote 로 펼치지 않는다.
```

**P5-b · L479-482 [한국어 번역 callout] 교체:**
```
[한국어 출력 — Evidence Level 연동 (v15.6)]
- Evidence A · 영문 원문 소스(FR·Recall·EMA·MHRA·PIC/S·ECA·FDA WL):
  "**한국어 번역**" + 원문 quote 에 대응하는 번역 quote(>) 블록.
- Evidence A · Language=KO 소스(MFDS): 번역 블록(W4)을 생성하지 않는다.
  한글 원문을 그대로 quote 하고, 핵심 사실·시사점·점검 사항도 한글로 작성한다.
  영문 라벨 병기 금지(분류용 영문 키는 Notion Type or Class 필드에만 존재).
- Evidence B/C: "**한국어 요약**" + paraphrase. quote 블록 금지.
```

**P5-c · 블록 W4(L740-745) 단서 추가:**
```
번역 대상이 없으면(또는 Language=KO 항목이면) 이 callout 자체를 생략.
```

---

## P6 — [페이지 출력] 국내/글로벌 2단 섹션 (블록 6~10, L698~)

**블록 6 앞에 분기 추가:**
```
블록 6 구성 (v15.6 — 국내/글로벌 2단):
다이제스트 본문은 글로벌과 국내(MFDS)를 분리한다.
- "## 🌐  글로벌 한눈에 ({N}건)" → 글로벌 한눈에 표 + 글로벌 사례 카드(블록 7~10)
- "## 🇰🇷  국내(MFDS) 한눈에 ({N}건)" → 국내 한눈에 표 + 국내 사례 카드
국내 섹션 카드는 P4 매핑 테마로 그룹핑하고, gmp-inspection 은 P4 지적사항 요약을 따른다.
국내 항목이 0건이면 국내 섹션 전체를 생략(빈 H2 금지)하고 M3에 "MFDS Intake 0건"으로 기록.
한눈에 표 컬럼에 **소재국**(예: 대한민국·프랑스·일본) 추가 —
gmp-inspection 해외 제조소를 식별하기 위함.
⚠️ 소재국 출처: Notion `Region/Jurisdiction` 속성은 **관할기관**(`Korea (MFDS)` 단일값)이므로
제조소 소재국으로 쓰지 않는다. 소재국은 `Site Country` 필드를 우선 사용하고, 비어 있으면
Body `국가:` 필드(또는 address)에서 파싱한다.
정렬: 각 섹션 내 Signal Tier 3→2→1, 동급은 발행일 desc.
```

**블록 7 한눈에 표(L700-706) — Region 컬럼 추가:**
```
<tr><td>**#**</td><td>**카테고리**</td><td>**기관**</td><td>**Region**</td><td>**사안**</td><td>**발행일**</td><td>**Evidence**</td><td>**Signal**</td></tr>
```

---

## P7 — [커버리지 콜아웃] MFDS 카운트 추가 (블록 5, L684)

**교체:**
```
	🔍  커버리지: Intake row {N}건 (FR {N} · Recall {N} · EMA {N} · MHRA {N} · PIC/S {N} · ECA {N} · FDA WL {N} · MFDS {N}) · 공식 API 직접호출 0 (외부 수집기 위임) · WebSearch {N}/9 (Core {N} + Deep Dive {N}) · WebFetch 접근 {N}/5 · 실패 {5-N}건 · 유효항목 {M}건 (글로벌 {G} · 국내 {K}) · Evidence A {N} / B {N} / C {N} · 미확인 {기관·카테고리}
```

---

## P8 — [메타데이터] Self-Check 서술 제거 + 8소스 (L811-819, L840-843)

**P8-a · L811-819 Intake 결과 목록에 MFDS 추가, 자가점검 표현 정리:**
```
		Intake 처리: Intake DB 조회 {N}회 + WebSearch {N}회 + WebFetch {N}개
		Intake 결과 (8개 소스):
		- Federal Register: {N}건  · OpenFDA Recall: {N}건  · EMA RSS: {N}건
		- MHRA RSS: {N}건  · PIC/S RSS: {N}건  · ECA RSS: {N}건  · FDA WL: {N}건
		- MFDS: {N}건 (admin {N}·recall {N}·gmp-inspection {N}·guidance {N}·기타 {N})
```
⚠️ "점검 완료:", "passed/verified", "이상 없음 확인" 류 **자기검증(Self-Check) 서술만 삭제**.
   커버리지·건수·실패 URL 같은 **사실 카운트는 유지**(Self-Check 기능 폐지 반영).

**P8-b · L840-843 [Intake-WebSearch 불일치 점검] — 삭제 아님, 사실 카운트로 축소(Codex 정정):**
자가검증 표현("불일치: 없음 → 일치 확인")은 제거하되, 운영 진단용 **source별 사실 대조 카운트**는 남긴다:
```
Intake vs Search 대조 (사실 카운트, 판정 서술 없음):
   · source별 Intake 적재 {N}건 / 동일 영역 WebSearch 발견 {M}건
   · Intake=0 인데 WebSearch 발견된 source: {목록 또는 "없음"}
   (위는 수집 누락 진단용 카운트일 뿐, "검증 통과/일치" 같은 자기판정 문구는 쓰지 않는다.)
```

---

## P9 — 버전 라벨 일괄 (전역)

`v15.5` → `v15.6`, `Intake-first daily mode` → `Intake-first daily mode (+MFDS)`.
해당 위치: L18(B절 라벨)·L658(블록1 헤더)·L800(AI 면책)·L844(생성 라벨) 등 전부.

---

## ⛔ 배포 게이트 (운영 반영 전 필수 — Codex 합의)

v15.6 을 프로덕션 Routine 에 넣기 전 아래 3개를 모두 충족해야 한다. 미충족 시 **배포 보류**(MFDS 0건 브리프 재발 방지):
1. **MFDS Intake 조회 성공 검증** — (A) 필터드 쿼리 가용 또는 (B) fallback 조회로 직전 7~9일 MFDS row 가 실제로 반환됨을 1회 실측. (2026-06-01 Claude 실측: (B) 경로로 gmp-inspection·admin row 반환 확인 — fallback 자체는 작동함. 단 프로덕션 실행계정에서 재확인 필요.)
2. **Self-Check 동시성** — 수집기 자동 산정/매핑 중단 코드 변경과 v15.6 출력 변경이 같은 배포에 묶임. 출력만 지우고 코드가 남으면 필드 계속 기입됨.
3. **국내 섹션 실렌더 확인** — 다음 수동 Routine 실행에서 🇰🇷 국내(MFDS) 섹션 + gmp-inspection 지적사항 분야 요약이 실제로 렌더되는지 육안 확인(05-31 브리프는 MFDS 0건이었음).

## 적용 후 검증 체크 (Codex 인계)

- [x] **PL-9 실측 결론(2026-06-01):** Claude Routine 실행환경에 `query_data_sources` **부재 확정**. Codex 환경에서도 `_notion_query_data_sources` 호출은 `Tool notion-query-data-sources not found`로 실패 확인. `search`+`data_source_url`+`created_date_range` fallback 은 작동(MFDS row 반환)하나 ⓐ 최대 25건·커서 없음 ⓑ `created_date_range`=createdTime 필터(Run Date 속성/Status 필터 아님). → P3 (B)경로 유지.
- [x] Region: Codex 권고대로 기존 `Korea (MFDS)`(관할) **유지**, 소재국은 별도 `Site Country` 필드 신설. 2026-06-01 Notion 라이브 스키마와 수집기 매핑 반영. Routine 은 `Site Country` 우선, 비어 있으면 Body `국가:` fallback.
- [x] Self-Check: Codex 권고대로 **Notion 필드는 유지**, 신규 자동 산정/브리프 노출만 중단. 수집기 자동 산정/매핑 제거 및 v15.6 출력 노출 제거 정합.

> 이 patch가 확정되면 전체 본문을 반영한 `GRM_Prompt_v15.6.md`를 생성할 수 있습니다. 단 위 **배포 게이트 3건**은 본문 생성과 무관하게 운영 반영의 선결조건입니다.
