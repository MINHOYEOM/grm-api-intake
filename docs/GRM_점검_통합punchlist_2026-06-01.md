# GRM 전방위 점검 — 통합 Punch-list (점검 후 반영용)

**작성:** 2026-06-01 · Claude(A 커버리지·C 태깅·E Routine) + Codex(B 수집정확성·D 운영) 1차 결과 병합
**원칙:** 점검 후 반영. 아래 punch-list 확정 → 단일 패스로 코드+Routine 동시 수정.
**검증 출처:** GRM_Prompt_v15.5.md · GRM_session_decisions.md(D1~D9) · 수집기 6종 · GRM API Intake DB 표본 · 05-31 브리프. Codex 라인 참조 4건 전부 파일에서 교차확인 완료.

**진행 상태(2026-06-01 갱신):**

- ✅ **반영 완료(9건 중 7건):** PL-1·3·6(Claude/v15.6) · PL-2·4·5·9(Codex/코드+라이브 스키마). 코드 py_compile·diff 통과, 30/90일 dry-run 정상. **commit `de7af3b` push 완료**(branch `codex/mfds-gmp-inspection`), 수집 run 26731893630 green.
- 🔲 **잔여:** PL-7(수집기 부재 소스 — 품목허가 변경·DMF 등, A축 확장) · PL-8(보조출처 403). 둘 다 별도 트랙, 배포와 무관.
- ✅ **배포 게이트 ①②③ 닫힘:** ①MFDS 조회 실측(프로덕션 데이터 (B)경로 ✓) ②Self-Check 코드+출력 동시 커밋(`de7af3b` ✓) ③국내 섹션 실렌더(검증 렌더 `GRM_게이트3_국내섹션_검증렌더_2026-06-01.md`, 로직 전부 pass ✓ — 프로덕션 페이지 미발행).
- ✅ **게이트④ Routine 연결 완료:** 운영자(MINO)가 주간 Routine에 v15.6.1 반영. 2026-06-01 라이브 실행 2회로 🇰🇷 국내 섹션·소재국 fallback·지적사항 요약·듀얼링크(Q4) 모두 정상 렌더 확인.
- ✅ **PL-10 해결·검증완료(2026-06-01):** (B) fallback 중복 발행 결함 → 수집기 New-only handoff 경로(v15.6.3, commit `4ab32a5`). **Claude 라이브 검증 통과** — 1회차 풀브리프+handoff CONSUMED 전환 / 2회차 빈브리프+broad fallback 미실행 / 원본 5건 Processed. **멱등성 확보, 운영 정기발행 차단 해제.** 테스트본 2건 `[TEST]` 라벨 완료.
- 🟢 **현 상태: 배포 차단 전부 해제.** 9개 PL 중 PL-1~6·9·10 반영완료(7+PL-10). 잔여 PL-7(수집기 부재 소스 A축 확장)·PL-8(403)은 정기발행과 무관한 별도 트랙. Q4(원본링크) 해결, Q5(카드포맷) 별도 세션 미결.
- **테스트본 정리:** 06-01 중복 페이지 2건에 `[TEST]` 라벨 부착 완료(삭제 안 함, 기록 보존). 정식 05-31 브리프와 구분됨.
- 🟡 **Q4(원본 링크):** v15.6.1로 해결 — 라이브 06-01 브리프 국내 카드에 📎 MFDS 행정처분 상세/실사 PDF 직링크 정상 출력 확인.
- ⏸ **Q5(카드 포맷):** 별도 세션(`GRM_Q5_카드포맷_논의_2026-06-01.md`). 미결.

---

## 0. 합의된 사실관계 (초판 정정 포함)

- 항목 데이터는 **GRM API Intake**(`collection d5b9634a…`), 발행물은 **GRM Weekly Brief** DB — 별개. (초판이 후자에서 집계해 "태그 전부 공란"이라 한 것은 오류, 정정함.)
- Intake 표본 태그(Signal Tier·Region·QA/OSD Relevance·Language)는 **채워져 있음.** C축 진짜 문제는 "결측"이 아니라 **Region 단일값 + 지적사항 비구조화 + 집계불가**.
- session_decisions가 모든 축을 이미 결정해 둠(D1~D9). 이번 점검은 "결정 vs 구현"의 갭 확인 = 대부분 **미반영(pending)** 상태 확정.

---

## 1. 통합 Punch-list (우선순위 정렬)

| ID | 축 | 우선 | 항목 | 근거(결정/코드) | 담당 | 조치 | 선행의존 |
|---|---|---|---|---|---|---|---|
| **PL-1** | E | P1 | Routine이 MFDS·신규 Type 모름 | v15.5 §Step1 L46 "7 sources"에 MFDS 없음 / D7 "NEEDS UPDATE" | Claude | **반영됨(v15.6)**: 8개 소스·MFDS Type 매핑(gmp-guideline 포함)·국내/글로벌 2단 섹션 | 없음(즉시) |
| **PL-2** | E·D | P1 | Self-Check 잔존(2레이어) | 코드: recall.py L141-142, intake.py L1664-1665 매핑 / 출력: 05-31 브리프 접이식 자가점검 / D6 "pending removal" | Codex(코드)+Claude(출력) | **반영됨(2026-06-01)**: ⓐ수집기 자동 산정·매핑 중단, Notion 필드는 휴면 유지 ⓑv15.6 메타데이터 자기검증 서술 삭제 | 없음(즉시) |
| **PL-3** | C·E | P1 | gmp-inspection 지적사항 = 키워드 플래그뿐 | gmp_inspection.py L4-20 `assess_deficiency` 4키워드 / 90일 present47·none19·**unknown9** / D8 미구현 | Claude(Routine 요약) | **반영됨(v15.6)**: [gmp-inspection 지적사항 요약] 규칙 — 본문 읽어 GMP분야+failure mode, keyword flag는 보조(unknown·none도 본문 판단) | 없음(즉시) |
| **PL-4** | B | P2 | MFDS RSS 필터 과락 | rss.py L20-30 제목 only 하드 게이트 `QA_KEYWORDS` 10개 / 30일 raw32→수집7 | Codex | **반영됨(2026-06-01)**: 키워드 보강(생균치료제·혈장분획제제·세포/유전자·비교동등성·비임상 등). 90일 dry-run MFDS RSS 38건, 기존 과락 4개 회수 확인 | 없음 |
| **PL-5** | C·E | P2 | Region=관할, 소재국 부재 | `Region/Jurisdiction`=`Korea (MFDS)`(관할기관)이라 프랑스·일본·중국 제조소도 동일값 / 소재국은 Body `국가:`만 | Codex(필드신설)+Claude(Routine 파싱) | **반영됨(2026-06-01)**: 기존 Region(관할) 유지, 신규 `Site Country` 필드 Notion 라이브 스키마+수집기 매핑 추가. Routine은 `Site Country` 우선, 비어 있으면 Body `국가:` fallback | **PL-6 이전 필수** |
| **PL-6** | E | P2 | Tier/Region 표 미노출 | v15.5 §Step3 한눈에 표에 컬럼 없음 / D9 TODO | Claude | **반영됨(v15.6)**: 국내 한눈에 표에 Signal·소재국(Site Country) 컬럼 + 섹션 내 Tier3→1 정렬 | PL-5 충족됨 |
| **PL-7** | A | P2 | 수집기 부재 소스 | 스키마 Type 옵션에도 없음 | Codex(수집기)+Claude(Type신설) | 상: 품목허가 변경(제조방법/규격)·DMF / 중: 허가/취하·수입해외제조소·위수탁 / 하: DUR | 없음 |
| **PL-8** | D | P3 | 보조출처 403 상시 | 05-31 브리프 WebFetch 5/5 403 (PIC/S·MHRA·gmp-compliance·RAPS·EPR) | Codex | UA/캐시/대체경로 또는 Evidence C 강등 명시 | 없음 |
| **PL-9** | D·B | **P1근저** | Claude 실행환경에 query_data_sources 부재 → MFDS 누락 | **2026-06-01 실측**: Claude 환경에 query_data_sources 없음. Codex 환경도 `_notion_query_data_sources` 호출 시 `Tool notion-query-data-sources not found` 실패. `search`+data_source_url+created_date_range fallback은 작동(MFDS row 반환)하나 25건컷·커서없음·createdTime필터(Status/RunDate 아님). v15.5 프롬프트(L152-178)는 정상 — **실행능력 문제** | Codex(환경)+Claude(fallback) | **반영됨(2026-06-01)**: v15.6 P3에 필터드 쿼리 단발검증 + 실패 시 fallback(B경로) 생략 금지 + 배포게이트① 추가 | **PL-1 실효의 전제 · 배포 게이트** |
| **PL-10** | D·B | ~~P1~~ **해결·검증완료** | (B) fallback이 Status=New를 못 걸러 중복 발행 | **2026-06-01 라이브 재현 후 해결.** 근본=PL-9(속성필터 도구 부재). | Codex(수집기)+Claude(라이브검증) | **반영(v15.6.3, commit `4ab32a5` / branch `codex/pl10-handoff-idempotency`):** `collect_intake.py`가 Notion API 속성필터로 `Status=New`+window 조회 → `OPEN GRM Routine Handoff` JSON 큐 생성(workflow non-dry시 `--emit-routine-handoff`). Routine handoff-first, broad fallback 코드 차단, 1회차 후 handoff→`CONSUMED`/Processed. | **✅ Claude 라이브 검증(2026-06-01):** STEP1 handoff row_count=5 / 1회차 5건 풀브리프+직링크+handoff CONSUMED 전환 / 2회차 빈브리프+`duplicate run suppressed`+broad fallback 미실행+재카드화 0 / 원본 5건 Processed 확정. **운영 차단 해제.** |

---

## 2. E축 — GRM_Prompt v15.6 §단위 수정안 (원문 확보 후 확정본)

> v15.5 실제 구조 기준. PL-1·2·3·6 반영. **PL-5는 2026-06-01 반영됨**(`Site Country` 신설) — E5의 소재국 표시는 `Site Country` 우선, 비어 있으면 Body `국가:` fallback.

**E1 — MFDS 인지 (PL-1) — 실제 수정 위치 3곳**
1. **L26-28 [역할]**: "FDA·EMA·TGA·MHRA·PIC/S·ICH" + "한국 규제는 사내 RA가 담당" → MFDS 제조/품질 신호는 본 다이제스트 범위에 포함으로 수정(국내 RA가 보는 글로벌과 별개로, 제조소 GMP실사·행정처분·회수는 QA 직접 관련).
2. **L40-43 [핵심원칙]6 + L401-403 [열거형 공식 출처]**: "7개 소스"→"8개 소스(+MFDS)". MFDS Type or Class 전 값 동적 인지 문구 추가.
3. **블록5 커버리지 콜아웃 L684**: 하드코딩된 `FR · Recall · EMA · MHRA · PIC/S · ECA · FDA WL`에 **MFDS {N}** 추가.
+ MFDS Type→테마 매핑 신설(실DB 값 기준):
`admin-action→행정처분 · recall-quality→회수·판매중지 · gmp-inspection→GMP 실태조사 · guidance-industry/-internal→지침·안내서 · regulation-final→개정법령 · notice-final→고시 · legislative-notice→입법예고 · safety-letter→안전성서한 · gmp-certificate→GMP 적합판정`
+ catch-all: `매핑에 없는 Type는 'Type 원문' 제목 별도 섹션(누락 금지).`
+ 국내/글로벌 섹션 분리(E축 review_prompt L45 요구) — H2 "🇰🇷 국내(MFDS)" / "🌐 글로벌" 2단 구성 검토.

**E2 — KO 항목 = 프롬프트 내부 충돌 해소 (D2 미반영, ⚠️단순 추가 아님)**
충돌 지점 3곳: ⓐ L31 `[핵심 원칙]1` "사실은 영문 원문 + 한국어 번역 병기" / ⓑ L135-139 `[한국어 번역]` 섹션이 영문→한글 번역 전제 / ⓒ L416-482 Evidence A 구조가 "영문 quote + 한국어 번역 quote" 쌍을 강제.
→ MFDS(Language=KO) 항목 예외 신설: `Language=KO 항목은 원문 한글이 이미 원본이므로 번역 callout(W4) 생략, 영문 병기 금지(D2). Evidence A의 영문 quote 규칙은 영문 원문 소스에만 적용. KO 항목은 원문 한글을 그대로 quote(>)하고 W4 번역 블록을 만들지 않는다.`

**E3 — §3 카드 포맷에 블록 추가 (PL-3)**
`gmp-inspection 카드: Body의 attachment_text(실사구분·소재지·주요 지적사항·결론)를 읽어 GMP 분야(무균/멸균·밸리데이션·데이터 인테그리티·환경모니터링·품질관리시험·안정성·문서/SOP·설비)와 failure mode로 1~2줄 요약. '지적(보완)사항 없음'이면 '지적사항 없음'으로. 수집기 keyword flag(none/present/unknown)는 보조 신호로만, unknown이어도 본문을 직접 읽어 판단.`

**E4 — Self-Check 출력 제거 (PL-2 출력측)**
v15.5 본문에는 Self-Check 명시 블록은 없으나(이미 D6 일부 반영), **05-31 실제 브리프 하단 접이식 `🔖 검색 메타데이터`에 자가점검 서술이 렌더됨**("점검 완료: …", "Intake-WebSearch 불일치: 없음" 등). → 메타데이터 토글에서 자기검증 서술(점검완료/일치확인/passed류) 삭제. 커버리지 콜아웃(소스·건수·실패 카운트)은 사실이므로 유지. + 코드측 PL-2ⓐ는 2026-06-01 수집기 자동 산정·매핑 중단으로 반영됨.

**E5 — §Step3 item3 한눈에 표 (PL-6, Region은 PL-5 의존)**
`컬럼에 Tier·Region/소재국 추가. 정렬 Tier3→2→1, 동급 Date desc. Region/Jurisdiction은 관할(`Korea (MFDS)`)로 유지하고, 국내/해외 제조소 구분은 Site Country를 우선 사용. Site Country가 비어 있으면 Body 국가: 파싱 fallback.`

---

## 3. 반영 순서 (단일 패스 권장 그룹)

1. **즉시(서로 독립):** PL-1, PL-2(코드+출력), PL-3, PL-4 — 의존 없음.
2. **PL-9** 해결을 PL-1과 함께: 도구 부재 우회 없으면 MFDS를 넣어도 라우틴이 못 읽음.
3. **PL-5 → PL-6** 순서 고정: PL-5는 반영됨. PL-6은 v15.6 표 렌더 실측으로 확인.
4. **PL-7**(신규 수집기)·**PL-8**(403)은 별도 트랙, 위 완료와 무관.

---

## 4. Codex 회신용 메모

- 라인 참조 **전부 확인**: v15.5 "7개 소스" L40-43·L401-403 / gmp_inspection.py L47-56 `_NO_DEFICIENCY_RE`·`_DEFICIENCY_PRESENT_RE`(키워드 정규식 = D8 근거). Self-Check 코드 잔존으로 확인했던 recall.py `_self_check_required` + intake.py `PROP_SELF_CHECK` 매핑은 2026-06-01 Codex 변경으로 자동 산정·기입 제거됨.
- 정정 2건(내 초판 대비):
  - **PL-9는 프롬프트 결함 아님.** v15.5 L170-178에 페이지네이션 처리 명시, L162에 "Source 필터 없음 = MFDS 포함 의도"까지 이미 있음. 실패는 **런타임에 필터드 쿼리 도구(query_data_sources) 부재**로 §0단계가 semantic search로 강등된 것 → D축(Codex) 우선.
  - **E2(KO)는 한 줄 추가가 아니라 프롬프트 내부 충돌 해소**(L31·L135-139·L416-482 Evidence A 구조가 영문 원문 전제). MFDS KO 예외를 Evidence/번역 규칙에 박아야 함.
- 제 추가 발견(Codex 4건에 없음): **PL-5 Region 단일옵션**(C축) → 2026-06-01 `Site Country` 신설로 1차 해소.
- 분담: 코드/런타임(PL-2ⓐ·4·5·7·8·9)=Codex / Routine·태깅·Type신설(PL-1·2ⓑ·3·6·5파싱)=Claude. 이 중 PL-2ⓐ·4·5·9은 2026-06-01 1차 반영/검증 완료.
- session_decisions와 정합: D(언어 한글유지)·Self-Check 제거 결정(L237-244)·Region 단일옵션(L131)·gmp-inspection 본문추출 라이브(L269-273) 전부 확인됨. 이번 갭은 "결정됐으나 Routine 미반영"이 핵심.
