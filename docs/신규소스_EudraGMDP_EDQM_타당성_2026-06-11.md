# 신규 소스 타당성 조사 — EudraGMDP NCR · EDQM CEP Actions

| 메타 | 값 |
|---|---|
| 작성일 | 2026-06-11 |
| 작성 주체 | Cowork (research-first 단계 — 코드 0줄, K3/BIO-1 관찰과 완전 직교) |
| 대상 시스템 | GRM `v1.28` / `origin/main`(GAP-2 머지 후) |
| 성격 | 제안(미구현). 실제 수집기 구현은 Claude Code 별도 트랙(우선순위 = P3 신규 소스, 관찰과 무관하므로 착수 시점은 자유) |
| 연관 | `GRM_향후과제_우선순위.md` P3-1 · `GRM_SYSTEM.md` §3.4(소스표)·§5.3 · `MFDS_nedrug_OpenAPI_강화제안_2026-06-02.md`(선례 형식) |

---

> ⚠️ **2026-06-11 후속 정정(사용자 "결함 내용 우선" 기준):** 아래 "채택 권고"는 *표면 신호(누가·집행)* 기준이었다. 사용자 기준이 **"무엇을 어떻게 잘못했는지(결함 내용)"** 로 정해지면서 재평가됨 → **EDQM CEP = 드롭**(물질+상태+사유카테고리뿐, 결함 내용 없음), **EudraGMDP NCR = 보류**(상세가 세션 게이트 + 카테고리 수준 why 까지, 483/WHOPIR 보다 얕음). 대신 **WHOPIR/WL 결함 추출 강화 + FDA 483/EIR** 가 우선. 상세 = `결함내용_표출감사_FDA483조사_2026-06-11.md`. 이 문서는 접근성 조사 기록으로 보존.

## 0. 한 줄 결론

**둘 다 채택 권고.** EudraGMDP **GMP 비순응 보고서(NCR)** 와 EDQM **CEP 정지/철회** 는 ① QA 미션(글로벌 GMP·원료 적합성)에 정확히 일치하고, ② **둘 다 비브라우저 fetch 가 차단되지 않으며**(TGA 같은 WAF 없음), ③ 구조화 파싱이 가능하다. 특히 EudraGMDP NCR 은 **WHO NOC 의 EU 정본(正本)** 으로, 현재 GRM 의 가장 큰 글로벌 공백을 메운다. 난이도는 EudraGMDP=중(세션/페이지네이션), EDQM=낮음(정적 페이지·저빈도).

---

## 1. 조사 방법·결과 (2026-06-11 라이브 fetch)

`mcp__workspace__web_fetch` 로 두 소스의 공개 endpoint 를 직접 1회 fetch 해 **접근성·데이터 형태·날짜 필터·중복키**를 확인. 둘 다 정상 응답(차단 없음).

---

## 2. EudraGMDP — GMP Non-Compliance Reports (NCR) 〔채택 권고, 1순위〕

### 2.1 무엇
EMA 가 운영하는 EU 공동 데이터베이스의 **GMP 비순응 성명(Statement of Non-Compliance)** 공개 피드. 규제당국이 제조소를 GMP 부적합으로 결론낼 때 발행하며, **비순응의 성격 + 당국이 취한/제안한 조치**를 담는다. 일부는 EDQM 주관 실사 결과이기도 하다.

### 2.2 접근성 (라이브 확인)
- **endpoint**: `https://eudragmdp.ema.europa.eu/inspections/gmpc/searchGMPNonCompliance.do`
- **GET 한 번에 현재 NCR 전체 표가 서버 렌더 HTML 로 반환됨**(조사 시점 11건). 키 불필요·공개. WAF/403 없음.
- **From/To Issue Date 필터**(YYYY-MM-DD) 폼 존재 → 윈도우 조회 가능.
- **Excel 내보내기**(`action=ExportList`) 버튼 존재 → 구조화 파싱 대안.
- **페이지네이션**: `?ctrl=searchGMPNCResultControlList&action=Page&param=N`. 1페이지 10행.
- **상세 drilldown**: `?action=Drilldown&param=<DocRef>` → 비순응 성격·조치 상세(인용/Evidence A 후보).
- **세션**: URL 에 `jsessionid` 가 동적으로 붙음. 단 base `.do` GET 은 새 세션을 자동 발급하고 현재 NCR 을 반환하므로, **수집기는 jsessionid 없는 base URL 로 GET → HTML 표 파싱**이면 충분. 날짜 필터/페이지네이션은 세션 POST 가 필요할 수 있으나, **현재 NCR 볼륨이 낮아(~11건) 전체 파싱 후 Issue Date 로 클라이언트 측 윈도우 필터**가 더 단순·견고.

### 2.3 데이터 형태 (실 응답 필드)
`Report Number` · `EudraGMDP Document Reference Number`(예: 185010, **안정 숫자 ID**) · `MIA Number` · `Site Name` · `Site Address` · `OMS Location Identifier`(LOC-…) · `City` · `Postcode` · `Country` · `Inspection End Date` · `Issue Date`.

예시 행: `MT/003NCR/2026 | 185010 | Navesta Pharmaceuticals (Pvt) Ltd. | … | Horana | Sri Lanka | 2026-04-21 | 2026-05-11`. 사이트는 **글로벌**(인도·스리랑카·네덜란드 등) — 제조소 GMP 비순응을 국적 무관 포착.

### 2.4 GRM 매핑
- `document_id`(dedup) = **Document Reference Number**(안정 숫자) + `Report Number` 보조.
- `firm` = Site Name · `Site Country`/`Region` = Country/City(GRM 의 Site Country 분리와 정확히 맞물림) · `date` = Issue Date(윈도우) · 보조 = Inspection End Date.
- `Signal Tier` = **Tier 3**(GMP 비순응은 고위험 집행 신호) · `Evidence` = drilldown 상세 fetch 시 A 후보(비순응 성격·조치 인용).
- `Modality` = 대체로 Other/Chemical(원료·제조소 단위), 일부 무균/주사제(예: Swiss Parenterals) — `compute_modality` 폴백으로 처리.
- 듀얼링크: 📰 = NCR drilldown URL · 📎 = 같은 EudraGMDP 페이지(공식 정본).

### 2.5 난이도·주의
- **중.** 신규 수집기 `collect_eudragmdp.py`(`ENABLE_EUDRAGMDP`, 기본 off). `requests` 만으로 충분(PDF 없음·PyMuPDF 불요). HTML 표 파싱은 `collect_intake.py` 의 FDA WL 스크래핑 패턴 재사용.
- 주의: ① jsessionid 동적 — base GET 으로 회피. ② 윈도우 내 NCR 이 10건 초과면 페이지네이션 필요(현재는 단일 페이지). ③ WHO NOC·PIC/S 와 **부분 중복 가능**(같은 사이트가 여러 피드에 노출) → dedup 키(사이트+날짜)로 흡수, EudraGMDP 를 EU 정본으로 우선.

---

## 3. EDQM — CEP Suspensions/Withdrawals/Restorations 〔채택 권고, 2순위〕

### 3.1 무엇
유럽약전 적합성인증(CEP) 의 **정지·철회·복원** 공개 목록. CEP 는 원료(API)가 약전 모노그래프에 적합함을 인증하는 것이라, 정지/철회 = **원료 공급 적합성 리스크** 신호. GMP 비순응에 따른 정지/철회가 별도 분류돼 있어 EudraGMDP NCR(사이트 관점)과 **상호 보완**(EDQM=원료/물질 관점).

### 3.2 접근성 (라이브 확인)
- **endpoint**: `https://www.edqm.eu/en/actions-on-ceps` — 정적 HTML, 깔끔하게 렌더·차단 없음.
- 섹션 구조: **CEP Suspensions**(① 보유자 요청 ② GMP 비순응 ③ 인증절차 미충족) · **CEP Withdrawals**(① GMP 비순응 ② 인증절차 미충족 ③ 모노그래프 삭제) · **Restoration of suspended CEP**.
- 각 행: `Date` · `Substance name` · `CEP Number`(예: `03/04/2024 Esomeprazole Magnesium trihydrate CEP 2014-147`).
- "Read the current Non-Compliance Reports" 링크 → EudraGMDP 로 연결(두 소스의 연계 확인).

### 3.3 데이터 형태·GRM 매핑
- `document_id`(dedup) = `CEP Number` + 액션유형(suspension/withdrawal/restoration) + 사유 + Date.
- `firm_or_product` = Substance name(원료명) · `date` = Date · `Region` = EU/EDQM.
- `Signal Tier` = **Tier 2~3**(GMP 비순응 정지/철회 = Tier 3, 절차 미충족·복원 = Tier 2).
- `Modality` = 원료(API) → 대체로 Chemical, 일부 무균(sterile 표기) → 폴백 처리.
- 듀얼링크: 📰/📎 = actions-on-ceps 페이지(정본).

### 3.4 난이도·주의
- **낮음.** 저빈도(주기 갱신·"지난 6개월" 단위)·단일 정적 페이지. 신규 수집기 `collect_edqm_cep.py`(`ENABLE_EDQM_CEP`, 기본 off) 또는 글로벌 보조 수집기에 합류.
- 주의: ① 사람이 큐레이팅한 HTML 이라 레이아웃 변경에 깨질 수 있음(nedrug 스크래핑과 동급 취약성) → 파서를 섹션 헤더 앵커 기반으로 견고하게. ② 날짜 포맷 혼재(예: `22/09//2023` 오타) 방어 파싱. ③ 갱신 빈도 낮아 일일 0건 정상(§3.5 0건 판정 원칙 적용).

---

## 4. 권고·도입 순서

1. **EudraGMDP NCR 먼저**(1순위·고가치): WHO NOC EU 정본·글로벌 GMP 비순응 공백 해소. `collect_eudragmdp.py` 신규.
2. **EDQM CEP**(2순위·저난이도): 원료 적합성 신호·PL-7(DMF/원료) 인접. `collect_edqm_cep.py` 신규 또는 글로벌 보조 합류.
3. 둘 다 **기본 off opt-in**(`ENABLE_*`) + dry-run 라이브 검증(ICH/WHO/HC 바이오 1단계와 동일 게이트: dry-run → 실적재 0실패 → Source Select 옵션 사전등록 → 운영 ON).
4. **이 작업은 K3/BIO-1 관찰과 직교**(새 수집기는 기존 파이프라인에 additive, scaffold·v16·golden 무관) — 관찰 종료를 기다릴 필요 없음. 단, 운영 활성(ENABLE on)은 관찰 안정성을 위해 한 소스씩.

### 채택 시 갱신할 `GRM_SYSTEM.md` 섹션
| 섹션 | 갱신 |
|---|---|
| §2.1/§3.4 소스표 | EudraGMDP(#12)·EDQM CEP(#13) 추가(글로벌 확장, opt-in) |
| §4.1 트리 | `collect_eudragmdp.py`·`collect_edqm_cep.py` 등록 |
| §4.3 플래그 | `ENABLE_EUDRAGMDP`·`ENABLE_EDQM_CEP`(기본 off) |
| §5.2/§5.3 | 신규 소스 후보 → 착수/완료로 상태 전이 · P3-1 갱신 |
| Weekly Brief DB `출처 기관` | EMA(EudraGMDP)·EDQM 옵션 추가 검토 |

---

## 5. 출처
- [EudraGMDP NCR 검색(공개)](https://eudragmdp.ema.europa.eu/inspections/gmpc/searchGMPNonCompliance.do)
- [EMA — Statements of non-compliance now public in EudraGMDP](https://www.ema.europa.eu/en/news/statements-non-compliance-gmp-now-publicly-available-eudragmdp)
- [EDQM — Actions on CEPs(정지/철회/복원)](https://www.edqm.eu/en/actions-on-ceps)
- [EDQM — The Inspection Programme](https://www.edqm.eu/en/the-inspection-programme)

## 📝 변경 이력
| 날짜 | 변경 내용 |
|---|---|
| 2026-06-11 | 최초 작성. EudraGMDP NCR·EDQM CEP 라이브 fetch 타당성 조사 — 둘 다 fetch 가능·구조화 파싱 가능·채택 권고. 구현은 Claude Code 별도 트랙(P3, 관찰과 직교) |
