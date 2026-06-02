# MFDS(의약품안전나라) 공공 OpenAPI 강화·추가 제안

| 메타 | 값 |
|---|---|
| 작성일 | 2026-06-02 |
| 작성 배경 | "nedrug 공공 API가 없다"던 기존 가정 재검토 요청. 의약품안전나라 공공데이터 공개 목록(`nedrug.mfds.go.kr/pbp/CCBGA01`) 확인 결과 식약처(기관코드 **1471000**) 의약품 OpenAPI가 다수 존재 |
| 대상 시스템 | GRM `v15.6.3` / `origin/main` 기준 |
| 성격 | 제안(미구현). 채택 시 갱신할 `GRM_SYSTEM.md` 섹션은 §6에 명시 |

---

## 0. 한 줄 결론

nedrug "스크래핑밖에 없다"는 전제는 **부분적으로만 맞습니다.** 식약처 의약품 데이터는 `data.go.kr`·`data.mfds.go.kr`에 **기관코드 1471000 OpenAPI**로 다수 공개돼 있고, 현재 GRM은 그중 **2종(회수·행정처분)만** 쓰고 있습니다. 특히 **① GMP 적합판정서 발급현황**과 **② 의약품 제품 허가정보** API는 GRM의 GMP/QA 미션과 정확히 일치하면서 현재의 nedrug 스크래핑(불안정)을 대체·보강할 수 있는 핵심 후보입니다.

---

## 1. 현재 MFDS 수집 현황 (코드 확인)

| 신호 | 현재 채널 | 코드 | 안정성 |
|---|---|---|---|
| 지침·고시·입법예고·안전성서한 | **RSS** (`mfds.go.kr/www/rss/brd.do`, 7개 보드) | `collect_mfds.py` | 보통 (RSS 포맷 의존) |
| 행정처분 | **OpenAPI** `15058457` (MdcinExaathrService04) | `collect_mfds_admin_action.py` | 양호 |
| 회수·판매중지 | **OpenAPI** `15059114` (MdcinRtrvlSleStpgeInfoService04) | `collect_mfds_recall.py` | 양호 |
| GMP 실태조사 결과 | **nedrug 스크래핑** (`CCBBD03` 게시판 + PDF 본문 파싱) | `collect_mfds_gmp_inspection.py` | **취약** (HTML 구조·PDF 레이아웃 변경에 깨짐) |
| 입법예고(보조) | 법제처 ogLmPp API (opt-in, `ENABLE_MOLEG_API`) | `collect_mfds.py` | 보조 |

요약: **OpenAPI 2종 + RSS + 스크래핑 1종.** 가장 GMP/QA 핵심인 GMP 데이터가 가장 취약한 스크래핑 방식입니다.

---

## 2. nedrug(1471000) 공개 OpenAPI 중 GRM 관련 카탈로그

확인된 식약처 의약품 OpenAPI 가운데 GRM 목적(GMP·품질·안전성 신호)에 관련된 것:

| 데이터ID | API 이름 | 핵심 제공 항목 | GRM 활용도 | 현재 |
|---|---|---|---|---|
| `15097207` | **의약품 GMP 적합판정서 발급현황** (DrugGmpStbltJgmtIssuStusService) | 업체명·공장소재지·완제/원료 구분·제형/제조방법·유효기간 | ★★★ 제조소 GMP 인증 상태 모니터링 | 미사용 |
| `15095677` | **의약품 제품 허가정보** (DrugPrdtPrmsnInfoService) | 품목·주성분·제조원·포장·저장·성상·허가일자·허가번호·희귀의약품 여부 | ★★★ 품목허가·제조원 변경 신호(PL-7) | 미사용 |
| `15095681` | 의약품 국가출하승인정보 | 접수번호·검정종류·품명·제조/수입사·유효기간 | ★★ 출하승인=품질 배치 신호 | 미사용 |
| `15097199` | 통지의약품 정보 | 주성분·함량·제형·신청제출일 | ★★ 신규 통지 품목 | 미사용 |
| `15059486` | DUR 품목정보 (DURPrdlstInfoService03) | 병용·연령·임부·노인 금기, 용량/투여기간 주의 | ★★ 안전성 변경 신호(서한 보강) | 미사용 |
| `15056780` | DUR 성분정보 | 성분 단위 금기/주의 | ★★ 안전성 변경 신호 | 미사용 |
| `15075057` | 의약품개요정보(e약은요) (DrbEasyDrugInfoService) | 효능·용법·주의·상호작용·부작용·저장 | ★ 카드 메타 보강 | 미사용 |
| `15059114` | 의약품 회수·판매중지 | 품명·업체·회수사유·조치유형·일자 | — | **사용 중** |
| `15058457` | 의약품 행정처분 | 처분 내역·대상·일자 | — | **사용 중** |

> 엔드포인트 베이스는 `https://apis.data.go.kr/1471000/{ServiceName}/{operation}` 형태입니다. 예: GMP 적합판정 = `.../DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq`. 신규 도입 시 정확한 operation명·파라미터는 기존 `probe_datago.py` 패턴으로 1회 탐침 후 확정 권장.

---

## 3. 강화·추가 후보 (우선순위)

### P1 — GMP 적합판정서 발급현황 API로 GMP 수집 보강·대체 〔15097207〕
- **무엇:** 현재 `collect_mfds_gmp_inspection.py`의 nedrug 게시판 스크래핑 + PDF 파싱을, 구조화된 OpenAPI로 대체하거나 병행.
- **왜:** GMP는 GRM의 핵심 미션인데 현재 가장 깨지기 쉬운 방식(HTML/PDF 레이아웃 의존)으로 수집 중. API는 업체·공장소재지·완제/원료·유효기간이 정형 필드로 들어와 **Site Country 분리·제조소 단위 추적**과 바로 맞물림.
- **개선 효과:** 안정성↑, `Site Country` 채움률↑, 유효기간 만료/신규 발급을 Tier 신호로 활용 가능.
- **주의:** 실태조사 "지적사항 본문 요약"은 적합판정 API에 없을 수 있음 → **지적사항 텍스트는 기존 PDF 경로 유지, 인증 상태/유효기간은 API**로 이원화하는 하이브리드가 현실적.
- **난이도:** 중. 신규 수집기 `collect_mfds_gmp_cert.py` 또는 기존 파일 보강. `DATA_GO_KR_SERVICE_KEY` 재사용.

### P1 — 의약품 제품 허가정보 API로 PL-7(품목허가/제조원) 착수 〔15095677〕
- **무엇:** 품목허가 기본정보(주성분·제조원·성상·허가일자·허가번호) 수집기 추가.
- **왜:** 로드맵 잔여 이슈 **PL-7(품목허가 변경·제조방법/규격, A축 확장)**의 진입점. "수집기 부재 소스"로 분류돼 있던 영역을 공식 API로 메움.
- **개선 효과:** 신규 허가/제조원 변동을 신호화. QA가 "이 제조소·이 성분이 우리와 관련 있나" 자가점검(Phase 2c)으로 연결 가능.
- **주의:** 이 API가 **변경허가 이력(diff)**까지 노출하는지는 불확실 → 도입 시 스냅샷 비교(전회 수집분 대비 delta) 로직 필요할 수 있음. 데이터량이 크므로 **수집 윈도우/필터(허가일자 기준)** 설계 필수.
- **난이도:** 중상. 신규 수집기 + delta 전략.

### P2 — DUR 품목/성분 API로 안전성 서한 보강 〔15059486 / 15056780〕
- **무엇:** 병용·연령·임부 금기 등 DUR 변경을 안전성 신호로 수집.
- **왜:** 현재 안전성서한은 RSS(`seohan001`) 단일. DUR 금기 갱신은 더 구조적인 안전성 변화 신호.
- **난이도:** 중. 단, **변경 탐지(delta)** 가 핵심이라 단순 조회만으론 노이즈 큼 → P1 안정화 후 착수 권장.

### P3 — 국가출하승인·통지의약품·e약은요 (보조·메타) 〔15095681 / 15097199 / 15075057〕
- **국가출하승인(15095681):** 백신·혈액제제 등 출하승인=품질 배치 신호. OSD(경구고형제) 중심 사용자에는 관련성 낮아 후순위.
- **통지의약품(15097199):** 신규 통지 품목 모니터링.
- **e약은요(15075057):** 카드에 품목 메타(효능·주의) 보강용. 신호 소스라기보단 enrichment.

---

## 4. "강화"로 분류되는 항목 (신규 추가 아님)

- **GMP 수집 안정화:** P1대로 스크래핑 → API 하이브리드.
- **회수/행정처분 필드 점검:** 이미 API 사용 중이나, 응답 필드(조치유형·사유 코드 등)를 카드 Tier 판정에 충분히 반영하는지 재점검 여지.
- **Weekly Brief DB `출처 기관`에 MFDS 옵션 추가:** 기존 known 이슈와 연동. 신규 API로 국내 카드가 늘면 더 시급해짐.

---

## 5. 권장 도입 순서

1. **probe 먼저:** `15097207`(GMP), `15095677`(제품허가) 두 API를 `probe_datago.py` 패턴으로 1회 탐침 → 실제 operation명·파라미터·응답 필드·일자 필터 가능 여부 확정.
2. **P1-GMP** 하이브리드 도입(인증상태=API, 지적사항=기존 PDF).
3. **P1-제품허가** 수집기 + delta 전략.
4. 안정화 후 **P2-DUR**, 필요 시 **P3** 보조.

> 모든 신규 API는 `DATA_GO_KR_SERVICE_KEY`(이미 보유) 재사용. 운영 IP 동적 이슈는 기존 회수/행정처분 API가 GitHub Actions에서 동작 중이므로 동일 처리 가능. 신규 소스마다 `ENABLE_MFDS_*` 플래그 + `.env.example` 추가(루트 평면·`archive/` 비추적 규칙 유지).

---

## 6. 채택 시 갱신할 `GRM_SYSTEM.md` 섹션

| 섹션 | 갱신 내용 |
|---|---|
| §2.1 / §3.4 (소스 표) | MFDS 채널에 GMP 적합판정·제품허가 API 추가 반영 |
| §4.1 (코드 파일 트리) | 신규 수집기(`collect_mfds_gmp_cert.py` 등) 등록 |
| §4.2 (플래그) | `ENABLE_MFDS_GMP_CERT` / `ENABLE_MFDS_PRODUCT` 등 추가 |
| §5.2 (PL-7) | 제품허가 API로 착수 → 상태 갱신 |
| §5.2 (known 이슈) | Weekly Brief DB MFDS 태그 추가 시급도 상향 |
| 각 섹션 📝 변경 이력 + 상단 메타 | 동반 갱신 |

---

## 출처

- 의약품안전나라 공공데이터 공개 목록: https://nedrug.mfds.go.kr/pbp/CCBGA01
- 의약품 공공데이터 개요: https://nedrug.mfds.go.kr/cntnts/80
- 의약품 GMP 적합판정서 발급현황: https://www.data.go.kr/data/15097207/openapi.do
- 의약품 제품 허가정보: https://www.data.go.kr/data/15095677/openapi.do
- 의약품 국가출하승인정보: https://www.data.go.kr/data/15095681/openapi.do
- 통지의약품 정보: https://www.data.go.kr/data/15097199/openapi.do
- DUR 품목정보: https://www.data.go.kr/data/15059486/openapi.do
- DUR 성분정보: https://www.data.go.kr/data/15056780/openapi.do
- 의약품개요정보(e약은요): https://www.data.go.kr/data/15075057/openapi.do
- 의약품 회수·판매중지(사용 중): https://www.data.go.kr/data/15059114/openapi.do
- 식의약 데이터 포털: https://data.mfds.go.kr/
