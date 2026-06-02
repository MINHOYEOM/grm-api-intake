# GRM 세션 결정 메모 — Codex 점검 반영 + Phase 2b 방향

작성: 2026-05-29 / 산출물 형태: **결정·방향만** (코드 작성·push는 Claude Code/Codex 담당)
상태: **확정** (사용자 + Codex 합의)

## 확정 사항 (TL;DR — Codex/Claude Code 핸드오프)

원칙: **Phase 2b 전에 P1을 먼저 닫는다.** 지금 MFDS를 붙이면 장애 시 기존 수집기 문제인지 신규 소스 문제인지 섞여 디버깅이 지저분해짐.

**실행 순서 (확정):**
1. **P1 두 개 수정** — Notion insert 실패 시 exit 1(+Issue), Atom `Element or Element` 파서 수정. MHRA 365일 회귀 테스트.
2. **P2 동반** — `total_fail` 전체 소스 합산, 429 Retry-After(Notion dedupe query 포함), 스키마 문서 갱신.
3. **`collect_mfds.py` 추가** — RSS fallback + ogLmPp parser 포함, `ENABLE_MFDS=false` 기본값 유지.
4. **Phase 2b-1 ship** — ① MFDS RSS 지침/가이드라인 + ② MFDS RSS 입법예고(primary)까지만. ogLmPp는 인가/IP 충족 환경용 optional path.
5. **Phase 2b-2 (후속)** — GMP 실태조사 결과는 별도 설계(스크래핑/Brave).

**공통 util 분리(신규 합의):** HTTP/429 helper와 Notion insert 정책을 `collect_intake.py`에 묶어두지 말고 **얇은 공통 util 모듈**(예: `grm_common.py`)로 빼기 시작 → `collect_intake.py`/`collect_mfds.py`가 공통 import. Phase 2c+ 확장 대비.

**언어 정책(확정):** Weekly Brief 한국 항목 = **한글 원문 용어 그대로, 영문 병기 없음**. 후속 검색·분류용 영문 라벨은 사람이 읽는 본문이 아니라 Notion `Type or Class` 구조화 필드의 영문 키(`legislative-notice`, `gmp-guideline` 등)로만 둠.

---

---

## Part A — Codex 점검 결과 disposition

전반 평가에 동의. 핵심은 "데이터는 유실/실패했는데 워크플로는 green" 계열이며, **Phase 2b 착수 전에 P1을 먼저 닫는다.**

### P1 — Phase 2b 이전 선결

| # | Finding | 결정 | 방향 |
|---|---------|------|------|
| 1 | Notion insert 실패가 exit code에 미반영 | **채택 (최우선)** | `stats.has_insert_failures()`가 true면 비-제로 exit + GitHub Issue 생성. 단 **dedupe skip은 실패 아님**(정상)으로 구분. 최종 재시도 후에도 남은 insert 실패만 카운트. |
| 2 | Atom `Element or Element` 데이터 손실 (571, 990, 1076, 1080) | **채택 (최우선)** | 전부 `first if first is not None else second`로 교체. 단순 DeprecationWarning이 아니라 namespaced Atom title/category/id를 실제로 버림(MHRA/PIC/S 영향). 수정 후 **MHRA 365일 샘플 회귀 테스트**로 title/body 복원 확인. |
| 3 | Notion 스키마 문서 vs 실제 DB 드리프트 | **하향 조정 → P2 (문서만)** | **이번 세션에서 라이브 DB 검증 완료.** Source URL·Raw Excerpt·Search Query·Evidence Candidate(A/B/C/D)·Source Type·Signal Tier **모두 실재**. 즉 insert 400 silent-fail 리스크는 현재 없음. `notion_intake_db_schema.md`만 Phase 2a 필드 누락 → 문서 갱신으로 처리. |

> 참고: 검증된 실제 DB 스키마(주요 select 옵션)
> - **Source**: Federal Register / OpenFDA Recall / EMA / MHRA Inspectorate / PIC/S / ECA Academy / FDA Warning Letter / RAPS / European Pharma Review / Brave Search → **MFDS 없음 (Phase 2b에서 추가 필요)**
> - **Source Type**: Official API / Official Regulatory Page / Official Regulator Blog / Expert Secondary / Search Result / Official Page Scrape
> - **Evidence Candidate**: A/B/C/D, **Signal Tier**: Tier 1~3, **Status**: New/Processed/Skipped/Error

### P2 — Phase 2b와 병행 가능

| Finding | 결정 | 방향 |
|---------|------|------|
| `total_fail`이 fr+recall+search만 합산, ema/mhra/pics/eca/wl 누락 | **채택** | 모든 소스 insert 실패를 합산. step summary 강조 문구 정확화. |
| HTTP 429 Retry-After 미적용 (FR/OpenFDA/RSS + Notion dedupe query) | **채택** | 공통 helper에 `Retry-After` 기반 backoff. **Notion dedupe query에도 적용**(현재 retry 없음). Phase 2b의 data.go.kr 호출에도 같은 helper 재사용. |
| FDA WL 테이블 미발견 = 조용한 성공 | **채택** | 0건 정상 vs 구조 변경을 구분, 후자는 경고로 표면화. |
| 스키마 문서 갱신 (P1#3에서 하향) | **채택** | `notion_intake_db_schema.md`에 Phase 2a 6개 필드 반영. Phase 2b MFDS 필드도 같이. |

### P3 — 정리/개선 (여유 시)

- `ENABLE_SCRAPE` 미사용 → 제거 또는 "미구현" 로그. **채택**(혼란 방지).
- `OFFICIAL_DOMAINS` 보강: `ec.europa.eu`/`health.ec.europa.eu`, `mhlw.go.jp`, **`mfds.go.kr`**, `nmpa.gov.cn`, `cdsco.gov.in`, `anvisa.gov.br` 추가. `gov.uk`는 과도하게 넓음 → 좁히기. **채택** — `mfds.go.kr`는 Phase 2b와 직결(Brave fallback 시 Evidence B 보장).
- workflow `window_days` raw 출력 escape/생략. **채택**(로그 인젝션 방어).
- `collect_intake.py` 2000줄 모듈 분리(collector/notion client/relevance). **부분 채택** — 전면 리팩터링 대신, **Phase 2b를 `collect_mfds.py` 별도 모듈로 출발**시켜 점진적 분리 시작.
- `RAPS_NEWS=pm` 31일 catch-up 의도 = "low-frequency 보충"으로 **문서에 명시**. 첫 활성화 시 최대 31일 전 기사 포함 가능.

> 실행 순서는 상단 "확정 사항" 참조. P3는 Phase 2b 코드와 함께 자연스럽게 처리.

---

## Part B — Phase 2b 식약처(MFDS) 수집 방향

대상 3종 (사용자 확정, **회수·판매중지 제외**): ① GMP 지침·가이드라인 개정 ② 입법예고 ③ GMP 실태조사 결과

**범위 분할(확정):** ①+② = **Phase 2b-1** (이번 ship 목표), ③ = **Phase 2b-2** (후속). 2b-1의 목표는 "MFDS 공식 신호가 Notion으로 안정적으로 들어온다"를 먼저 증명하는 것.

### 소스별 수집 채널 결정

**① GMP 지침·가이드라인 개정 → MFDS RSS (1순위)**
- 식약처가 공식 RSS 구독 서비스 제공(공지/공고, 보도자료, 입법예고, 행정지시/지침 등). 기존 `collect_intake.py` RSS 파서 패턴 재사용 가능.
- 대상 게시판(후보): 공지/공고(`m_76`), 공무원지침서/민원인안내서(`m_1060`), 행정지시(`m_74`).
- 한국어+영어 키워드 필터로 GMP 관련만 추출(아래 키워드).
- ⚠️ RSS feed의 정확한 endpoint URL은 미확정(RSS 디렉터리가 JS 렌더링). **구현 1단계에서 실제 feed URL 확인 필요**(브라우저 미연결로 이번 세션 미검증).

**② 입법예고 → MFDS RSS data0009 (자동화 primary), ogLmPp API (optional)**
- 정부입법지원센터 REST API(`ogLmPp`) 및 data.go.kr `법제처_정부입법예고`(데이터 ID 15058407)는 구조화 장점이 있으나, 입법정보공개 별도 신청 + 요청 IP 바인딩 리스크가 있음.
- GitHub Actions hosted runner는 출발 IP가 동적이므로 scheduled 자동화 primary는 **MFDS RSS `data0009`**로 확정.
- ogLmPp parser는 유지하되 **`ENABLE_MOLEG_API=true` opt-in 환경에서만** 시도. 401/권한/IP 문제 시 RSS로 안전 강등.

**③ GMP 실태조사 결과 → 후행(Phase 2b-2) 권고**
- 2022.10~ 의약품안전나라(nedrug)/식의약데이터포털에 공개(누적 약 398건). 그러나 **정형 피드/오픈API 부재 가능성 높음**.
- data.go.kr `의약품GMP적합판정서발급현황` API(15097207)는 존재하나 = **"적합판정서 발급 현황"**(누가 적합 받았는지)이지 **실태조사 결과 보고서(지적사항 분류 등)가 아님** → 부분 대체만 가능.
- 권고: ⑴ 1차로 적합판정서 발급현황 API로 "신규 GMP 적합판정" 신호 모니터, ⑵ 실태조사 결과 보고서 본문은 게시판 스크래핑 또는 `site:nedrug.mfds.go.kr` Brave 슬롯으로 **Phase 2b-2 후행**. 피드 불확실성이 커서 ①②와 분리.

### 구현 구조 — `collect_mfds.py` 별도 모듈

- **결정: 별도 파일 분리.** 근거: (a) 한국어 인코딩/소스 특수성, (b) data.go.kr API는 키·요청형식이 기존과 상이, (c) Codex의 2000줄 단일파일 지적 완화의 점진적 출발점.
- 단 **공유 자산은 import로 재사용**(중복 방지): `IntakeItem` 모델, Notion client/insert, dedupe 로직, 429 helper. `collect_intake.py`가 `collect_mfds`를 호출하는 구조.
- feature flag `ENABLE_MFDS`로 게이트(기존 ENABLE_SEARCH와 동일 패턴). **기본값 `false`로 골격 먼저 추가**(3단계).
- **공통 util 분리 시작(확정):** HTTP/429 helper와 Notion insert 정책을 `collect_intake.py`에 묶어두지 말고 얇은 `grm_common.py`로 추출 → `collect_intake.py`/`collect_mfds.py` 양쪽이 import. Phase 2c+ 확장 시 큰 이득. (P2의 429 작업과 같은 타이밍에 시작하면 비용 최소)

### Notion 스키마 추가 (최소 변경)

| 속성 | 변경 | 비고 |
|------|------|------|
| **Source** | 옵션 **`MFDS`** 추가 | 필수 |
| **Source Type** | 기존 옵션 재사용 가능 (`Official API` / `Official Regulatory Page` / `Official Page Scrape`) | 신규 불필요. 필요시 `RSS Feed`만 추가 |
| **Type or Class** | **영문 키** 추가: `legislative-notice`, `gmp-guideline`(2b-2: `gmp-inspection`) | 기계용 라우팅 라벨(영문). 본문엔 노출 안 됨 |
| **Language** (신규 select) | `KO` / `EN` 추가 권고 | Routine 필터용 |
| **Region/Jurisdiction** (신규 select, 선택) | `Korea (MFDS)` 등 | 글로벌 확장 대비. 당장은 Source로 갈음 가능 |
- 한국어 전용 필드 신설 **불필요** — 기존 `Headline`/`Body`에 한국어 원문 그대로 저장(번역·영문병기 없음). `Document ID`는 게시물 seq 또는 입법예고 ID.

### 한국어 Claude Routine 처리

- Claude는 한국어 네이티브 처리 → Weekly Brief에서 한국어 원문 독해·요약 가능.
- **QA Relevance 휴리스틱에 한국어 키워드 병행 추가 필요.** 단, 현재 단어경계(`\b`) 매칭은 한국어 부적합 → **한국어는 substring 매칭 분기**.
  - 키워드 예: `GMP`, `우수의약품제조관리기준`, `밸리데이션`, `데이터 완전성`, `자료 완전성`, `일탈`, `시정조치`, `변경관리`, `안정성`, `무균`, `입법예고`, `실태조사`, `적합판정`, `제조소`.
- **출력 언어(확정):** 한국 항목은 **한글 그대로, 영문 병기 없음**. 분류·검색 보조는 `Type or Class` 영문 키로만.

### 사용자 작업 (비밀값만)
- `DATA_GO_KR_KEY` (data.go.kr 인증키) → GitHub Secrets 등록. 입법예고 API용(Phase 2b-1 ②).

---

## 결정 확정 요약
1. **입법예고 채널**: ✅ MFDS RSS `data0009` scheduled primary + ogLmPp optional.
2. **GMP 실태조사 결과**: ✅ Phase 2b-2 후행(2b-1 범위 제외).
3. **Weekly Brief 한국 항목 언어**: ✅ 한글 유지, 영문 병기 없음(라우팅은 구조화 필드 영문 키).
4. **공통 util**: ✅ `grm_common.py`로 HTTP/429 + Notion insert 정책 분리 시작.

---

## 라이브 Notion DB 스키마 — 적용 완료 (2026-05-29)

ENABLE_MFDS=true 전 선결이던 DB blocker 해소. 기존 select option **내부 ID 전부 보존** 확인 → 기존 row 안전.
- `Source` += `MFDS` (총 11개)
- `Type or Class` += `legislative-notice`, `gmp-guideline`, `gmp-inspection` (총 20개)
- 신규 `Language` select: `KO` / `EN`
- 신규 `Region/Jurisdiction` select: `Korea (MFDS)`
- 참고: `Type or Class` 속성 설명 텍스트는 ALTER 과정에서 비워짐 → 복원 보류(기능 무관, 차후 함께 복원).

---

## Phase 2b-1 구현 스펙 (step 2 확정 + Codex 핸드오프)

### 확정된 MFDS RSS endpoint
- feed 패턴: `https://www.mfds.go.kr/www/rss/brd.do?brdId={brdId}`
- ① GMP 지침·가이드라인: `brdId=data0013` (안내서/지침)
- ② 입법예고(RSS fallback): `brdId=data0009` (입법/행정예고)
- 검증 노트: 유효 brdId는 web_fetch가 빈 응답(=XML), 무효 brdId는 HTML 셸 반환. **구현 1단계에서 원시 XML 필드 구조(RSS `<item><title><link><pubDate>` vs Atom) 1회 확인 필수.**

### 입법예고 optional — ogLmPp/data.go.kr
- `법제처_정부입법예고` (data ID 15058407), `ogLmPp` REST. 소관부처=식약처 필터.
- 단, 입법정보공개 별도 신청과 IP 바인딩이 필요해 GitHub Actions scheduled primary로 쓰기 부적합.
- `ENABLE_MOLEG_API=true` + 정상 인가/허용 IP 환경에서만 시도. 기본값은 false.

### collect_mfds.py 구현 항목 (Phase 2b-1)
1. `collect_mfds(start, end, key)` — `MFDS_RSS_BOARDS` 7보드 수집. Type은 보드/문서 구조 기준(`guidance-industry`, `guidance-internal`, `regulation-final`, `notice-final`, `safety-letter`, `legislative-notice`), GMP·품질 여부는 relevance/tier 내용 신호로 분리.
2. `collect_mfds_legislative(start, end, key)` — 기본은 `data0009` RSS. `ENABLE_MOLEG_API=true`로 key가 전달될 때만 ogLmPp를 먼저 시도하고 실패 시 RSS fallback. type=`legislative-notice`.
3. dedupe key: `MFDS::{seq 또는 입법예고ID}`.
4. 오류 처리: 전체 실패 시 `(items, err_msg)`의 err 반환 → 기존 `enable_mfds and stats.mfds_error` 경로로 exit 1.
5. QA Relevance 휴리스틱: 한국어는 `\b` 대신 substring 분기(키워드 목록은 위 "한국어 Claude Routine 처리" 참조).

### ⚠️ 구현 상태 — 다음 세션 필독 (2026-05-29)

**↓ 아래 표는 2보드 pilot 시점 기록. 최신 상태는 그 아래 "Phase 2b-1 1차 RSS 확장(2026-05-29)" 섹션이 우선한다(7보드 + 통합 relevance + Notion 5옵션 추가 완료).**

| 항목 | 상태 |
|------|------|
| RSS primary 경로 | **✅ 7보드로 확장**(아래 확장 섹션 참조) |
| data.go.kr / 법제처 ogLmPp parser (`_collect_legislative_datago`) | **✅ 구현됨** — 공식 가이드(type=4) 필드 기반 + 샘플 XML smoke 통과 |
| ogLmPp live 권한/응답 | **optional blocker** — `OC=grmintake`와 `OC=test` 모두 HTTP 200 + `<retMsg>401</retMsg>` 반환. 코드가 이를 감지해 RSS fallback으로 안전 강등 |
| strict Phase 2b-1 완료 기준 | **RSS primary 안정 동작**으로 조정. ogLmPp 정상 응답은 후속 optional 개선 |
| 현재 운영 상태 | **`ENABLE_MFDS=false` 유지** |
| manual dry-run | **가능** (RSS primary) |
| scheduled enable 조건 | RSS-only dry-run 성공 + Notion schema 유지 확인 후. `ENABLE_MOLEG_API=false` 유지 |

**ogLmPp 공식 가이드 확인:** `https://opinion.lawmaking.go.kr/api/apiGuideInfo?type=4`. 필터는 `cptOfiOrgCd=1471000`(식품의약품안전처), 날짜는 `stYdFmt`/`edYdFmt`. 주요 응답 필드: `ogLmPpSeq`, `lsNm`, `lsClsNm`, `asndOfiNm`, `pntcNo`, `pntcDt`, `stYd`, `edYd`, `FileName`, `FileDownLink`, `readCnt`, `mappingLbicId`, `announceType`.

**relevance 수정 사유(반영됨):** `type_or_class`의 `"gmp-guideline"` 문자열이 relevance blob에 섞여 data0013 전 항목이 오탐되던 버그 수정 → relevance 입력에서 `type_or_class` 제거, 기관명(`식품의약품안전처/식약처`) 제거, GMP 지침 키워드(`MFDS_GMP_TERMS`)와 의약품 입법예고 키워드(`MFDS_PHARMA_LEGISLATIVE_TERMS`) 분리. **`Pending`(out-of-scope) 항목은 insert에서 제외.**

**이전 pilot 라이브 검증 결과(30일):** data0013/data0009 2보드 시점에는 `legislative-notice` 1건만 수집. 최신 7보드 확장 결과는 아래 섹션 참조.

### Phase 2b-1 1차 RSS 확장 (2026-05-29 확정·구현)

좁은 2보드 pilot(data0013+data0009)이 90일 4건뿐이라, 같은 `brd.do` RSS 계열 보드를 확장 후 한 번에 켜기로 결정(ⓑ).

**핵심 원칙: Type=보드/문서 구조, GMP·품질 여부=내용 신호(qa_relevance·Signal Tier).** (gmp-guideline 오탐 버그의 근본 해결)

보드→Type 매핑(`collect_mfds.MFDS_RSS_BOARDS`):
| brdId | 내용 | Type or Class |
|-------|------|---------------|
| data0013 | 안내서/지침 | guidance-industry |
| data0011 | 민원인안내서 | guidance-industry |
| data0010 | 공무원지침서 | guidance-internal |
| data0009 | 입법/행정예고 | legislative-notice (scheduled primary; ogLmPp optional) |
| data0008 | 최근 개정 법령 | regulation-final |
| data0005 | 고시전문 | notice-final |
| seohan001 | 안전성 서한 | safety-letter (Tier 3 floor) |

- 통합 relevance(`_mfds_relevance`, Type 무관): `pharma_hits>0 OR gmp_hits>0 OR eng∈(Likely,Possible)`이면 수집, 아니면 Pending(drop). 단, 의료기기/식품/화장품 일반 항목은 의약품·GMP rescue 신호가 없으면 제외. GMP/boost → Likely.
- Signal Tier(`_mfds_tier`): safety-letter 또는 고신호(무균·데이터완전성·적합판정·불순물) → Tier 3 floor, 확정 규정/고시(`regulation-final`/`notice-final`) → Tier 2 floor, GMP 일반 → Tier 2 floor.
- **✅ Notion `Type or Class`에 5개 옵션 추가 완료(라이브 DB):** regulation-final, notice-final, guidance-industry, guidance-internal, safety-letter (기존 20개 ID 전부 보존, 총 25개).
- ✅ `collect_mfds.py` 확장 구현(보드 루프 + 통합 relevance/tier). Codex/로컬 py_compile 통과.
- ✅ RSS-only dry-run 결과(2026-05-29 기준): 30일 13건, 90일 36건. 30일 breakdown = guidance-industry 8 / guidance-internal 3 / regulation-final 1 / legislative-notice 1. 90일 breakdown = guidance-industry 19 / guidance-internal 5 / regulation-final 5 / notice-final 3 / legislative-notice 4. `seohan001`은 0건(정상). 의료기기 false positive 1건 제외 확인.
- 회수(plc0139) 1차 미포함 — Phase 2c.

### 잔여 순서 (확정)
1. 실삽입 1회 검증(MFDS row가 새 Type/Language/Region으로 Notion에 정상 insert).
2. `ENABLE_MFDS=true`, `ENABLE_MOLEG_API=false` 정식 전환.
3. ogLmPp는 입법정보공개 별도 신청 + 허용 IP 확보 시 `ENABLE_MOLEG_API=true`로 후속.

### Phase 2c (확정 — 자가점검 차별화)
GRM의 다음 차별점은 "수집"이 아니라 **"이 규제 신호가 우리 공정/SOP에 해당하는가" 자가점검**이다.
- **Tier A 자가점검 연료 소스 확보:** GMP 실태조사 결과·지적사항(nedrug 등), 회수·판매중지(품질결함) → Type `recall-quality`(또는 `quality-recall`), GMP 적합판정서 API. ※ 회수는 plc0139 parser 별도 + 이전 제외 결정의 의식적 복원.
- **자가점검 트리거:** Notion 신규 필드 `Self-Check Required`(`Yes`/`Review`/`No`). Routine이 `safety-letter`/`gmp-inspection`/`recall-quality` 등 Tier A 항목을 만나면 `Review`/`Yes`로 올리고 Weekly Brief에 "우리 SOP/공정 해당 여부 점검" 액션 생성. (선택 확장: `Applicability` = Likely Applicable/Needs Triage/Not Applicable/Unknown)
- 제조/품질 "전부 커버"는 이 단계까지 와야 완성. 1차 RSS 확장은 "규제 변화 모니터링 베이스"까지다.

### P3 (정리 완료)
- ✅ `RAPS_NEWS=pm` 의도/정합성 주석 갱신 (`collect_search.py`의 SLOT_FRESHNESS_OVERRIDE 위). low-frequency catch-up(첫 활성화 시 최대 31일 전 기사 포함 가능)이며, `collect_intake.py`의 `dedup_window=max(window_days,35)`(ENABLE_SEARCH=true)와 짝이라 재삽입 없음. 기존 stale 주석("별도 이슈 추적 중 / dedup window 확장 필요")을 "해소됨"으로 교체.
- (이전 P3였던 `window_days` raw 출력 escape, `ENABLE_SCRAPE` 미구현 로그는 이미 반영 완료.)
