# Phase 2d — GMP 실태조사(실사) 결과 Collector Spec (nedrug HTML 스크래핑)

작성: 2026-05-29 / 상태: v1 구현·검증·enable 완료. **API 키 불필요(공개 웹). Notion 스키마 변경 불필요**(`gmp-inspection` Type · `Official Page Scrape` Source Type 기존 존재).

## Goal
의약품안전나라 "의약품등 GMP 실사 결과공개"(총 624건)에서 **실사 결과 목록 메타**를 수집해 Notion Intake에 `gmp-inspection`으로 적재. 제조/품질 자가참조 가치 높음(우선순위 `gmp-certificate`보다 높음). **1차 = 목록 메타 + 다운로드 docId 저장. 본문(PDF/HWP) 파싱은 Phase 2d-2로 분리.**

## 확인된 구조 (Codex + Chrome 검증)
- 목록 URL: `https://nedrug.mfds.go.kr/pbp/CCBBD03/getList` — **서버렌더 HTML 표**(SPA 아님). 쿠키 없이 일반 GET 200 확인. `limit=100` 동작.
- form GET 파라미터: `page`, `limit`, `sort`, `sortOrder`, `searchYn`. 권장 호출: `?page=1&limit=100`.
- 주의: `sort=registTsChar&sortOrder=DESC`를 붙이면 최신순이 아니라 과거 2022년 row가 나오는 함정 확인됨. v1 collector는 기본 최신 정렬 + `page/limit`만 사용.
- 표 컬럼: 사전/사후, 완제/원료, 국가, 제조소명, 소재지, 실사시작일, 실사종료일, 실사결과(다운로드), 등록일.
- 다운로드 체인: `downFile(docId)` → `/pbp/cmn/isExistFile?docId=...` → `/cmn/edms/down/{docId}` (첫 샘플 PDF, **HWP 섞일 수 있음**).

## 모듈 / 게이트
- 신규 `collect_mfds_gmp_inspection.py` (FDA Warning Letter HTML 스크래핑 패턴 재사용 — `collect_intake`의 `_FDAWLTableParser`/HTMLParser 방식 참고).
- feature flag `ENABLE_MFDS_GMP_INSPECTION`(기본 false), 독립 게이트. `grm_common` HTTP helper 재사용, 브라우저 UA 헤더.

## 매핑
- `document_id = gmpinspect-{docId}` (downFile의 docId — 안정 고유키). dedupe `MFDS::gmpinspect-{docId}`.
- `Headline` = `[GMP실사] {제조소명} ({국가}) — {사전/사후}·{완제/원료}`.
- `Firm` = 제조소명.
- `Body` = 소재지 / 국가 / 사전·사후 / 완제·원료 / 실사시작~종료일 / 등록일 / 다운로드 URL(`https://nedrug.mfds.go.kr/cmn/edms/down/{docId}`).
- `Source URL` = 다운로드 URL(보고서), `Official URL` = 보드 `https://nedrug.mfds.go.kr/pbp/CCBBD03`.
- `Date` = 등록일(수집 윈도우 기준). 실사종료일도 Body 보존.
- 고정값: `Source=MFDS`, `Type or Class=gmp-inspection`, `Source Type=Official Page Scrape`, `Language=KO`, `Region/Jurisdiction=Korea (MFDS)`.
- `Signal Tier` = **Tier 2 기본**(실사 결과 공개 = 품질 신호이나, 지적사항 본문이 첨부라 메타만으론 심각도 불명). 본문 파싱 단계에서 중대/중요 지적 시 Tier 3 승격 검토.
- QA Relevance = Possible(메타 기준). 국가≠대한민국(해외제조소)도 수집하되 `국가`를 Body에 보존(필요 시 국내 필터 옵션).
- Self-Check 미사용(기능 제거됨).

## 수집 범위 / 페이징
- `등록일 DESC` 정렬로 최근분만(수집 윈도우 = 등록일 기준). 전량 624건 매번 적재 금지 — 윈도우 + dedupe.
- 페이지네이션: `page` 증가(GET). 윈도우 벗어나면 중단.

## 검증 → enable
1. probe: `getList?page=1&limit=10` 일반 GET HTML 파싱 → row 수/컬럼/ docId 추출 확인(브라우저 아닌 `requests.get`로).
2. py_compile.
3. dry-run: 등록일 윈도우 건수 + 파싱 정합(제조소명/docId/날짜).
4. 실삽입 1회 + 2회차 dedupe skip.
5. green + Claude 교차검토 후 `ENABLE_MFDS_GMP_INSPECTION=true`.

## 후속 (Phase 2d-2)
- 다운로드 첨부(PDF/HWP) 본문 파싱 → 지적사항 failure mode 자동 분류. **HWP 처리 필요**(PDF 가정 금지). 별도 단계.

### Phase 2d-2 feasibility probe (2026-05-30)
- 다운로드 URL(`/cmn/edms/down/{docId}`)은 쿠키 없이 일반 GET 가능.
- 샘플 페이지 1/2/5/10/20/40/63의 첫 첨부를 확인한 결과 모두 PDF였고, PyMuPDF(`fitz`)로 텍스트 레이어 추출 성공(약 489~1,285자).
- 최근 5건은 모두 1페이지 PDF이며 `지적(보완)사항 없음`까지 텍스트로 추출됨.
- 실제 지적사항 있는 샘플도 확인:
  - 2026-05-18 `(주)엘앤씨바이오`: 품질경영/위탁시험기관 추가 평가자료 등.
  - 2026-04-14 `Orion Corporation`: 허가관리 관련 지적사항.
  - 2026-02-25 `신원산업(주)`: 시설장비 등 GMP 감시 분야 지적사항.
- 결론: **2d-2a는 PDF 텍스트 추출부터 구현 가능**. PDF 텍스트 추출 성공 시 Body에 `실태조사 결과/지적(보완)사항` 원문을 추가하고, 지적사항 있음/없음과 주요 분야 키워드로 Tier 2/3 및 QA relevance 조정. HWP/HWPX가 나오면 v1에서는 다운로드 링크만 보존하고 `attachment_parse_status`성 로그로 graceful fallback.

### Phase 2d-2 멀티포맷 본문 추출 설계 (HWPX 포함)
포맷별 분기 — **PDF·HWPX 둘 다 쉬움**, 레거시 바이너리 HWP만 fallback:
- **PDF(텍스트레이어)**: PyMuPDF(`fitz`) 또는 pdfplumber. probe상 GMP 실사 첨부는 현재 **전부 이 케이스**.
- **HWPX(정부 표준·권장 포맷)**: ZIP+XML 패키지 → `zipfile`로 열어 `Contents/section*.xml`의 텍스트 노드 `{http://www.hancom.co.kr/hwpml/2011/paragraph}t`(`hp:t`)를 모아 문단 결합. **stdlib(zipfile+ElementTree)로 충분**, 또는 python-hwpx류 lib. 독점 바이너리 파싱 불필요 → 쉬움. (정부가 HWP 금지·HWPX 의무화 추세라 향후 주력 포맷)
- **레거시 바이너리 .hwp**: olefile/pyhwp best-effort, 실패 시 다운로드 링크만 보존(graceful). 비중 감소 중.
- **스캔 PDF(텍스트레이어 0)**: 추출 0자 감지 → 링크 보존, OCR은 후순위 옵션.
- 공통: 추출 텍스트를 Body에 `지적(보완)사항` 섹션으로 저장('지적사항 없음'도 그대로). 지적 유무·분야 키워드로 Tier 2/3·QA relevance 조정. 추출 실패는 `attachment_parse_status`로 표면화(조용한 실패 금지).
- 의존성: PyMuPDF는 GitHub Actions에 추가 설치 필요(HWPX는 stdlib로 가능). 요청 간 지연 유지.

## 주의
- 공개 정보지만 스크래핑이므로 robots/이용약관 확인, 요청 간 지연(rate limit) 두기.
- 표 구조 변경 시 graceful(파싱 0건이면 경고로 표면화, 조용한 성공 금지).

## Codex 작업 지시 — Phase 2d-2a (확정 2026-05-30, gmp-certificate보다 우선)
기존 `collect_mfds_gmp_inspection.py`(이미 라이브)에 **본문 텍스트 추출**을 추가. 구조화 태깅(2d-2b)은 아직 안 함.

1. **수집 윈도우 내 row만** docId로 첨부 다운로드(GET `/cmn/edms/down/{docId}`, 브라우저 UA, raw bytes helper, 요청 간 지연). 전량 624 다운로드 금지.
2. **포맷 감지(매직바이트)**: `%PDF`→PDF, `PK\x03\x04`→HWPX(ZIP), `\xd0\xcf\x11\xe0`→레거시 HWP(OLE).
3. **추출**:
   - PDF: PyMuPDF(`fitz`) 텍스트. 0자면 스캔 간주 → fallback.
   - HWPX: `zipfile` → `Contents/section*.xml` → `{http://www.hancom.co.kr/hwpml/2011/paragraph}t` 텍스트 결합(stdlib).
   - 레거시 HWP/스캔/실패: 추출 생략.
4. **Body 갱신**: 기존 메타 + `실사 결과/지적(보완)사항` 원문 추가(truncate/chunk 적용). `attachment_parse_status`(pdf-ok/hwpx-ok/hwp-skip/scan-no-text/download-fail)를 Body 또는 raw_payload에 기록.
5. **지적 유무 판별 + Tier**: 본문에 `지적(보완)사항 없음`/`이상없음` 류 → 지적 없음 → **Tier 2, QA Possible**. 그 외(지적 있음) → **Tier 3, QA Likely**.
6. **graceful 절대원칙**: 다운로드/추출 실패는 **row를 떨어뜨리거나 run을 깨지 않음** — 메타 + 다운로드 링크 + parse_status로 insert. 실패는 로그로 표면화.
7. dedupe 불변(`gmpinspect-{docId}`). 기존 메타-only 5건은 dedupe로 갱신 안 됨(신규분부터 본문 포함) — 필요 시 1회 backfill은 선택.
8. 의존성: PyMuPDF를 requirements/워크플로에 추가(HWPX는 stdlib).
9. 검증: py_compile → dry-run(추출 성공률·지적 유무 분포·Tier 분포) → 실삽입 1회+2회차 dedupe → Claude 교차검토. (flag는 이미 true이나 동작 변경이라 재검증)

## Phase 2d-2b (후속)
- 지적사항 구조화 분류(중대/중요/기타 · failure mode 태깅). 2d-2a 안정화 후.
