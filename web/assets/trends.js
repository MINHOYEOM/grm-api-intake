/* GRM 규제 지적사항 트렌드 대시보드 (FIND-1 F3b) — 정적·클라이언트사이드, 순수 fetch
 * (PostgREST RPC 직접 호출, POST). findings.js 와 자매 페이지지만 데이터원이 다르다 —
 * findings.js 는 행 단위 SELECT(공개 게이트 006 통과분만), 이 페이지는 사전계산 집계
 * RPC 2종(007_findings_stats_rpc.sql)을 쓴다 — 공개 게이트를 우회해 전량(미번역분 포함)
 * 카운트를 반환하지만, 원문/URL 텍스트 필드는 어떤 경로로도 내려주지 않는다(안전 계약,
 * 마이그레이션 파일 원문 참조). 그래서 이 페이지는 카운트·서지 메타(카테고리/월/소스/
 * 증거등급/업체명)만 다루고 지적 내용 원문은 절대 렌더하지 않는다.
 *
 * cfg(url/key/root) 는 템플릿의 #grm-findings-cfg data-속성(env-param)에서 읽는다.
 * url/key 중 하나라도 없으면 "트렌드 서비스 준비 중입니다." 안내로 조용히 종료한다(오류
 * 아님 — 정적 페이지 골든 결정론, env 값과 무관하게 trends.html 자체 출력 byte 는 항상
 * 동일). data-root 는 findings 검색 페이지(카테고리 바 클릭 시 이동)로의 상대경로 계산에만
 * 쓴다(reactions.js 의 data-root 관례와 동형).
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 대입은 컨테이너 비우기 ""
 * 뿐 — findings.js 와 동일 XSS 계약). 업체명(firm_name)·소스(source)·카테고리 라벨은
 * 전부 textContent 로만 삽입한다.
 *
 * [동기화 규칙] CATEGORY_LABELS 는 findings.js 의 동명 상수·grm_findings.FINDING_TAXONOMY
 * 20개 code/label_ko/label_en 과 완전히 일치해야 한다(web/tests/test_render.py 가
 * WebTrendsRenderTest.test_category_labels_sync_with_taxonomy 로 대조). findings.js 를
 * import 할 수 없는 독립 정적 자산이라 값을 그대로 복제해 두되, 드리프트는 테스트가 잡는다.
 *
 * [업체 프로파일 진입] 017_findings_stats_firm_key.sql 적용 라이브에서 top_firms 행에
 * firm_key 가 실려오면, 업체 상세 패널 상단에 findings/firm/index.html?key= 로 가는
 * "업체 프로파일 전체 보기" 링크를 추가한다(findings_firm_stats(p_firm=firm_name) 기반
 * 기존 상세 패널 자체는 그대로 유지 — firm_key 는 오직 이 링크에만 쓴다). 017 미적용
 * 라이브(top_firms 에 firm_key 없음)에서는 링크 렌더를 조용히 생략한다(방어 폴백 —
 * 구버전 top_firms 형태와 신규 형태 둘 다 깨짐 없이 렌더되어야 한다).
 *
 * ── [13차 정직화] 무엇을 말할 수 있고 무엇은 말하면 안 되는가 ────────────────────
 * published_date 는 실사 발생일이 아니라 **문서 공개일**이고, FOIA 대량 공개가 특정
 * 연도에 뭉치는 데다 외부 백필도 진행 중이다. 여기서 파생되는 규칙 셋:
 *   (1) 연도 간 비교는 **구성비(%)** 로만 한다 — 연도별 확보량이 10배 넘게 차이나므로
 *       건수 비교는 규제 활동이 아니라 우리 수집 이력을 비교하는 것이 된다.
 *   (2) 공개일 기반 시계열 증감(전년 대비 %)은 **계산하지 않는다** — 공개 배치 크기를
 *       규제 추세로 오독시키는 대표적 지표라, 12차까지 있던 YoY 문장을 제거했다.
 *   (3) 분포로서 정보가 없는 차트는 렌더하지 않는다 — 증거 등급(A 99% 이상 단일값)
 *       섹션을 이 원칙으로 삭제했다(내부 QA 개념 비노출 원칙과도 정합).
 *   (4) 표본이 작은 구간은 결론을 지지하지 못하므로 빼되, **뺐다는 사실을 화면에 적는다**
 *       (renderHeatmap 의 MIN_YEAR_BASE / tr-heatmap-note).
 *
 * ── [트렌드 고도화 2026-07] 누적 인구조사에서 의사결정 도구로 ────────────────────
 * 위 (1)~(4)는 "무엇을 말하면 안 되는가"를 잘 지켰지만, 그 결과 이 페이지는 **우리가
 * 무엇을 얼마나 모았는가**만 답하고 "지금 무엇을 보라는 것인가"에는 답하지 않았다.
 * 누적 분모가 특정 연도(당시 2024년)의 대량 공개 배치에 크게 치우쳐 있어, 전 기간 순위는
 * 사실상 그 배치의 그림자였다(당시 47%로 적어 뒀으나 이후 백필로 희석돼 낡았다 — 주석에
 * 실측 비율을 박지 말 것. 실제 분포는 '연도별 공개량(참고)' 차트가 그린다).
 * 다음 셋을 더한다 — 셋 다 기존 섹션과 독립된 fetch 라 실패 반경이 자기 자신뿐이다:
 *   ① 최근 12개월(renderRecentWindow) — 041 findings_recent_window. 월별 문서 수 막대
 *      24개월(최근/직전 2색) + 최근 창 카테고리 순위.
 *   ② 달라진 점(renderMovers) — 같은 041 응답에서 파생(추가 fetch 0). 최근 12개월 vs
 *      직전 12개월의 **구성비**(그 창 지적 전체 중 이 영역의 비율) 차이(%p). 처음에
 *      "문서 등장률"로 만들었다가 되돌린 이력과 그 이유는 renderMovers 위 주석 (1) 참조.
 *      교란 요인인 소스 구성 변화는 화면에 적기만 하는 게 아니라 **계산에서 정렬한다** —
 *      052 by_category_source 로 두 창에서 견줄 수 있는 소스만 남겨 분자·분모를 함께
 *      좁히고, 뺀 소스는 구성 표기와 나란히 적는다(alignSourceMix).
 *   ③ 업체 찾기 — 041 findings_firm_search. [존 재편 2026-08-26] 이 폼은 firm.js 로
 *      옮겼다. 통계 페이지 일곱 번째 블록에 있던 조회 도구를 **프로파일 페이지 자체**의
 *      랜딩(/findings/firm/, ?key= 없이 들어왔을 때)으로 승격해, 그 페이지가 스스로
 *      진입로가 되게 했다 — 재편 전에는 이 폼이 업체 프로파일에 닿는 유일한 경로였고
 *      정적 링크는 사이트 어디에도 없었다. 남은 문장은 이력으로 둔다:
 *      이름으로 찾아 013 업체 프로파일
 *      페이지로 보낸다. 그리고 최근 창 카테고리 행을 누르면 **실제 지적 문장**을 펼친다.
 *
 * ★③의 사례 문장은 위 "원문 텍스트를 절대 렌더하지 않는다"의 예외가 아니라 **다른 층**이다.
 *   그 문장은 집계 RPC(007/010/017/038/041)가 아니라 026 findings_search 에서 오고, 그
 *   함수는 security invoker 라 공개 게이트(010 정책)를 통과한 행만 돌려준다 — /findings/
 *   검색 페이지가 이미 쓰는 바로 그 경로다. 집계 RPC 의 무반환 계약은 그대로 불가침이다.
 */
(function () {
  "use strict";

  var cfg = document.getElementById("grm-findings-cfg");
  var loadingEl = document.getElementById("tr-loading");
  var errorEl = document.getElementById("tr-error");
  var contentEl = document.getElementById("tr-content");
  var statsEl = document.getElementById("tr-stats");
  // [공개 범위 투명성] 스탯 스트립 수치(전량 집계)와 카테고리 클릭 → 검색 페이지 이동 결과
  // (공개 게이트 통과분만) 사이 간극을 명시하는 노트. 이미 보유한 fetchStats() 응답(totals)을
  // 재사용해 채운다(추가 fetch 0) — 엘리먼트가 없는 구버전 셸이어도(하위호환) renderCoverageNote()
  // 가 조용히 no-op 하도록 방어적으로 조회한다(findings.js 의 hasDash 관례와 동형).
  var coverageNoteEl = document.getElementById("tr-coverage-note");
  var coverageTextEl = document.getElementById("tr-coverage-text");
  var catEl = document.getElementById("tr-cat");
  var heatmapBlockEl = document.getElementById("tr-heatmap-block");
  var heatmapEl = document.getElementById("tr-heatmap");
  // 표본 부족으로 제외한 연도를 적는 자리(구버전 셸엔 없을 수 있어 방어적 조회 — 없으면
  // 제외 사실을 못 적으므로 아예 제외도 하지 않는다, renderHeatmap 참조).
  var heatmapNoteEl = document.getElementById("tr-heatmap-note");
  var yearEl = document.getElementById("tr-year");
  var firmsEl = document.getElementById("tr-firms");
  var firmDetailEl = document.getElementById("tr-firm-detail");
  var sourceEl = document.getElementById("tr-source");
  // [해외/미국 실사 비교] 038_findings_zone_category.sql 전용 신규 패널 엘리먼트 —
  // coverageNoteEl/heatmapNoteEl 과 동일하게 아래 하드 게이트에는 넣지 않는다. 넣으면
  // 이 블록이 없는(캐시 스큐 등으로) 구버전 셸을 만났을 때 스크립트 전체가 조기
  // 리턴되어 이미 정상 동작하는 다른 패널까지 함께 죽는다 — 이 패널의 실패 반경은
  // 이 패널 자신으로만 한정해야 한다(과제 요건: "다른 패널에 영향 0").
  var zoneBlockEl = document.getElementById("tr-zone-block");
  var zoneSubEl = document.getElementById("tr-zone-sub");
  var zoneEl = document.getElementById("tr-zone");
  var zoneCountriesEl = document.getElementById("tr-zone-countries");
  // [FDA 의약품 GMP 실사 등급] 058_fda_inspections.sql fda_inspection_stats() 전용
  // 신규 엘리먼트(임무3·기준일은 059) — zoneBlockEl 등과 동일하게 아래 하드 게이트에는 넣지 않는다.
  // 넣으면 이 블록이 없는 구버전 셸(캐시 스큐)을 만났을 때 스크립트 전체가 조기
  // 리턴되어 이미 정상 동작하는 다른 패널까지 함께 죽는다 — 이 패널의 실패 반경은
  // 이 패널 자신으로만 한정해야 한다.
  var fdaBlockEl = document.getElementById("tr-fda-block");
  var fdaScopeEl = document.getElementById("tr-fda-scope");
  // [기준일] 059_fda_inspection_stats_freshness.sql 이 scope 에 더한 신선도 2키 전용.
  // 059 미적용 라이브에서는 키가 없으므로 이 문단은 비어 있는 채로 남는다(:empty 로 숨김).
  var fdaAsOfEl = document.getElementById("tr-fda-asof");
  var fdaStatsEl = document.getElementById("tr-fda-stats");
  var fdaYearEl = document.getElementById("tr-fda-year");
  var fdaCountryEl = document.getElementById("tr-fda-country");
  var fdaNoteEl = document.getElementById("tr-fda-note");
  // [062] 실사일 축(by_quarter) · 한국 슬라이스(korea) — 다른 신규 블록과 같은 원칙으로
  // 하드 게이트 밖에 둔다. 062 미적용 라이브에서 이 두 블록이 없어도(구버전 셸) 이 면의
  // 주 데이터(등급 구성)는 그대로 그려져야 한다.
  var fqBlockEl = document.getElementById("tr-fq-block");
  var fqEl = document.getElementById("tr-fq");
  var fqNoteEl = document.getElementById("tr-fq-note");
  var krBlockEl = document.getElementById("tr-kr-block");
  var krSubEl = document.getElementById("tr-kr-sub");
  var krYearEl = document.getElementById("tr-kr-year");
  var krNoteEl = document.getElementById("tr-kr-note");
  // [최근 12개월 · 달라진 점] 041_findings_recent_window 전용 엘리먼트. zone/heatmap 과
  // 동일하게 아래 하드 게이트에는 넣지 않는다 — 구버전 셸(캐시 스큐)을 만나도 실패
  // 반경이 이 패널들로만 한정되어야 한다.
  var recentCatsEl = document.getElementById("tr-recent-cats");
  // [존 재편 2026-08-26] 순위 보기 전환 — 재편 전에는 같은 축(카테고리별 지적 순위)을
  // 재는 표가 분모만 다른 채로 **세 개의 별개 섹션**으로 흩어져 있었다(최근 12개월 /
  // 전 기간 누적 / 해외vs미국). 분모가 다른 표가 한 스크롤에 나란히 있으니 "왜 숫자가
  // 다르냐"는 오독이 구조적으로 생겼고, 그걸 막으려고 섹션 사이에 구분선과 해설 문단을
  // 두고 있었다. 같은 자리에서 보기를 바꾸면 두 수치가 동시에 보이지 않으므로 오독
  // 자체가 사라진다 — **해설로 막던 것을 구조로 막는다.**
  // ★데이터 모델은 합치지 않는다. 세 pane 은 기존 렌더러의 컨테이너를 그대로 쓰고,
  //   전환은 표시 계층에서만 한다 — 세 RPC 의 분모를 하나로 뭉개는 순간 이 재편이
  //   없애려던 바로 그 오독을 코드가 저지르게 된다.
  var rankBlockEl = document.getElementById("tr-rank-block");
  var rankReadEl = document.getElementById("tr-rank-read");
  var rankSubEl = document.getElementById("tr-rank-sub");
  var rankNoteEl = document.getElementById("tr-rank-note");
  // [컨셉 재정의] 기관 선택 — 아래 모든 수치의 **분모를 정하는** 컨트롤이라 화면 맨 위다.
  var agencyEl = document.getElementById("tr-agency");
  var agencyBtnsEl = document.getElementById("tr-agency-btns");
  var moveBlockEl = document.getElementById("tr-move-block");
  var moveSummaryEl = document.getElementById("tr-move-summary");
  var moveUpEl = document.getElementById("tr-move-up");
  var moveDownEl = document.getElementById("tr-move-down");
  var moveSourceEl = document.getElementById("tr-move-source");
  var moveNoteEl = document.getElementById("tr-move-note");
  // [인용 조항] 042_findings_cfr_ranking 전용.
  var cfrBlockEl = document.getElementById("tr-cfr-block");
  // [컨셉 재정의] 조항 순위의 읽는 법 — 고른 기관에 따라 문장이 달라져야 한다.
  var cfrReadEl = document.getElementById("tr-cfr-read");
  var cfrSubEl = document.getElementById("tr-cfr-sub");
  var cfrEl = document.getElementById("tr-cfr");
  var cfrNoteEl = document.getElementById("tr-cfr-note");
  // [존 재편 2026-08-26] 하드 게이트는 **셸 4개**로만 남긴다.
  // 재편 전 이 게이트는 statsEl/catEl/heatmapEl/yearEl/firmsEl/sourceEl 까지 요구했다 —
  // 한 페이지가 모든 섹션을 갖는다는 전제였고, 존을 세 면으로 나눈 지금 그 전제는 깨졌다
  // (지적 경향 면엔 히트맵이 없고, 실사 결과 면엔 카테고리 순위가 없다). 섹션 엘리먼트를
  // 게이트에 넣으면 자기 면에 없는 섹션 하나 때문에 **스크립트 전체가 조기 리턴**해
  // 그 면이 통째로 죽는다 — 이미 zone/fda/recent 블록이 같은 이유로 게이트 밖에 있었다.
  // 이제 그 원칙을 전 섹션으로 확장하고, 대신 각 렌더러가 자기 엘리먼트를 null 가드한다.
  if (!cfg || !loadingEl || !errorEl || !contentEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();
  var root = (cfg.getAttribute("data-root") || "").trim();
  // [존 재편] 이 면이 무엇을 그릴 수 있는가. 엘리먼트 유무만으로도 렌더는 안전하지만,
  // **로딩 해제 시점**(어느 fetch 가 이 면의 주 데이터인가)은 엘리먼트로 정할 수 없다 —
  // 그래서 면 이름을 셸에서 명시적으로 받는다. 미지정이면 기존 동작(지적 경향)이다.
  var page = (cfg.getAttribute("data-page") || "trends").trim();

  // grm_findings.FINDING_TAXONOMY verbatim(code -> {ko, en}) — findings.js CATEGORY_LABELS
  // 와 동일 복제본(동기화 테스트로 드리프트 차단, 파일 상단 계약 참조).
  // v3(2026-07-12): grm_findings.FINDING_TAXONOMY 순서 변경(complaint_recall,
  // computer_system_validation 이동)에 맞춰 선언 순서도 동기화 -- code/label 값 자체는
  // 불변(20개), 대조 테스트는 순서 무관 dict 비교이지만 이 파일의 관례상 선언 순서는
  // taxonomy 계약 순서를 따른다.
  var CATEGORY_LABELS = {
    data_integrity: { ko: "데이터 완전성", en: "Data integrity" },
    computer_system_validation: { ko: "컴퓨터화시스템", en: "Computer system validation" },
    documentation_records: { ko: "문서화/기록관리", en: "Documentation and records" },
    aseptic_sterility_assurance: { ko: "무균보증/무균공정", en: "Aseptic processing and sterility assurance" },
    environmental_monitoring: { ko: "환경모니터링", en: "Environmental monitoring" },
    cleaning_validation: { ko: "세척밸리데이션", en: "Cleaning validation" },
    complaint_recall: { ko: "불만/회수", en: "Complaint and recall handling" },
    deviation_capa: { ko: "일탈/CAPA/조사", en: "Deviation, CAPA, and investigation" },
    quality_unit_oversight: { ko: "품질부서 관리감독", en: "Quality unit oversight" },
    qc_lab_controls: { ko: "시험실/품질관리", en: "Laboratory and QC controls" },
    process_validation: { ko: "공정밸리데이션", en: "Process validation" },
    equipment_facility: { ko: "설비/시설", en: "Equipment and facility" },
    material_supplier_control: { ko: "원자재/공급업체 관리", en: "Material and supplier control" },
    contamination_control: { ko: "오염/교차오염 관리", en: "Contamination control" },
    validation_qualification: { ko: "밸리데이션/적격성평가", en: "Validation and qualification" },
    stability_storage: { ko: "안정성/보관", en: "Stability and storage" },
    labeling_packaging: { ko: "표시/포장", en: "Labeling and packaging" },
    regulatory_reporting: { ko: "규제보고/변경관리", en: "Regulatory reporting and change control" },
    training_personnel: { ko: "교육/작업자", en: "Training and personnel" },
    other_quality_system: { ko: "기타 품질시스템", en: "Other quality system" },
  };

  // [동기화 규칙 — 056] ISO 3166-1 alpha-2 코드 → 한국어 국가명. 이전(2026-07-31 이전)엔
  // findings.site_country 원문 문자열 23종을 그대로 키로 썼는데, 원문은 자유 텍스트라
  // 실측 85종(2026-08-11)으로 이미 낡아 있었다 — 문자열은 소스가 늘 때마다 새 변종이
  // 생겨 반드시 낡는다. 코드는 유한(ISO2)하고 안정적이라 이 문제가 구조적으로 없다.
  // web/migrations/055_findings_country_key.sql 의 public.grm_normalize_country() /
  // grm_findings.py 의 _COUNTRY_CODE_MAP 이 매핑 정본이다(057_grm_normalize_country_
  // ddapi.sql 이 FDA Data Dashboard API CountryName 27종을 추가해 47→68개 코드로
  // 확장했다 — 이 사전은 그 정본의 **모든 코드**를 커버해야 한다(web/tests/
  // test_render.py 가 대조. country_key 정본 함수가 findings/fda_inspections 양쪽에서
  // 공유되므로 코드 집합이 늘면 이 사전도 함께 늘어야 한다 — "사전은 반드시 낡는다"
  // 규율). findings_zone_category()(055)와 fda_inspection_stats()(058, 임무3)
  // 둘 다 top_countries/by_country[].code 로 이 코드를 내려준다 — 새 정규화 사전을
  // 만들지 않고 이 사전을 그대로 재사용한다(임무서 지시).
  var COUNTRY_LABELS_KO = {
    US: "미국",
    KR: "대한민국",
    PR: "푸에르토리코",
    IN: "인도",
    CN: "중국",
    JP: "일본",
    DE: "독일",
    CA: "캐나다",
    FR: "프랑스",
    GB: "영국",
    IS: "아이슬란드",
    IT: "이탈리아",
    MY: "말레이시아",
    ES: "스페인",
    BE: "벨기에",
    HU: "헝가리",
    TW: "대만",
    CH: "스위스",
    CY: "키프로스",
    AU: "호주",
    IE: "아일랜드",
    SE: "스웨덴",
    JO: "요르단",
    GR: "그리스",
    DK: "덴마크",
    NL: "네덜란드",
    MX: "멕시코",
    CZ: "체코",
    LT: "리투아니아",
    PL: "폴란드",
    CL: "칠레",
    AT: "오스트리아",
    RO: "루마니아",
    ZA: "남아프리카공화국",
    BD: "방글라데시",
    ID: "인도네시아",
    LB: "레바논",
    PT: "포르투갈",
    SK: "슬로바키아",
    LK: "스리랑카",
    TR: "튀르키예",
    NO: "노르웨이",
    FI: "핀란드",
    VN: "베트남",
    BY: "벨라루스",
    SI: "슬로베니아",
    IL: "이스라엘",
    // [057] FDA Data Dashboard API CountryName 확장분(21개 신규 코드 — SG/BR/TH/MT/
    // AR/HR/HK/CO/NZ/BG/DO/LV/OM/CR/EG/MO/PH/UY/AW/EE/AE. KR/CZ/FI/IL/SI/NO 는
    // 위 47개 안에 이미 있으므로 여기서 다시 추가하지 않는다 — 같은 나라의 DDAPI
    // 표기가 다른 코드로 오추가되지 않도록 057 확장분 중 기존 코드 재사용 6개는
    // 의도적으로 생략했다).
    SG: "싱가포르",
    BR: "브라질",
    TH: "태국",
    MT: "몰타",
    AR: "아르헨티나",
    HR: "크로아티아",
    HK: "홍콩",
    CO: "콜롬비아",
    NZ: "뉴질랜드",
    BG: "불가리아",
    DO: "도미니카공화국",
    LV: "라트비아",
    OM: "오만",
    CR: "코스타리카",
    EG: "이집트",
    MO: "마카오",
    PH: "필리핀",
    UY: "우루과이",
    AW: "아루바",
    EE: "에스토니아",
    AE: "아랍에미리트",
  };

  // [코드 우선, 원문 폴백] code(ISO2)가 있으면 그 라벨(없는 코드는 코드 자체를 그대로
  // 노출 — 빈칸/추측 번역 금지, 매핑 정본이 47개보다 더 낡아도 화면은 최소한 코드로는
  // 읽힌다). code 가 아예 없으면(055 미적용 구버전 RPC 응답) country(원문 문자열)로
  // 폴백한다 — 이 폴백이 없으면 055 미배포 상태에서 패널이 통째로 비게 된다.
  function countryLabelKo(code, country) {
    if (code) return COUNTRY_LABELS_KO[code] || code;
    return country || "";
  }

  // [인용 조항] 21 CFR 210/211 조항 뿌리 → 국문 요지. 042_findings_cfr_ranking.sql 이
  // 실제로 돌려주는 41개 조항 전부를 담는다(2026-07-31 실측 기준). 조항 번호만으로는
  // 전 직원이 읽을 수 없어 "무엇을 요구하는 조항인가"를 한 줄로 적는다 — 원문 조문은
  // 각 행의 eCFR 링크로 바로 갈 수 있으므로 여기서는 **요지만** 쓰고 요건을 옮겨 쓰지
  // 않는다(번역문이 조문 행세를 하면 안 된다).
  // 매핑에 없는 조항은 번호만 표시한다(추측 번역 금지 — countryLabelKo 와 동일 원칙).
  var CFR_SECTION_LABELS = {
    "210.3": "정의",
    "211.22": "품질관리부서의 책임·권한",
    "211.25": "작업자 자격·교육",
    "211.28": "작업자 위생·복장",
    "211.42": "건물의 설계·구조(무균 구역 포함)",
    "211.46": "환기·공기 여과",
    "211.56": "청소·위생 관리",
    "211.58": "건물 유지관리",
    "211.63": "설비의 설계·규격·설치 위치",
    "211.67": "설비 세척·유지관리",
    "211.68": "전산화 설비 관리(접근권한·백업)",
    "211.80": "원자재·용기 일반 관리",
    "211.84": "원자재·용기 시험 및 합부 판정",
    "211.87": "승인된 원자재 재시험",
    "211.94": "용기·마개 적합성",
    "211.100": "생산·공정관리 절차서와 일탈 처리",
    "211.101": "원료 칭량·투입",
    "211.110": "공정 중 시료채취·시험",
    "211.111": "공정 단계별 시간 제한",
    "211.113": "미생물 오염 관리(무균공정 밸리데이션)",
    "211.115": "재작업",
    "211.125": "표시자재 불출 관리",
    "211.130": "포장·표시 작업 관리",
    "211.137": "사용기한 설정",
    "211.142": "보관 절차",
    "211.150": "출하·유통 절차",
    "211.160": "시험실 관리 일반(규격·시험방법의 타당성)",
    "211.165": "완제품 시험 및 출하 판정",
    "211.166": "안정성 시험",
    "211.167": "특수 시험(무균·발열성 등)",
    "211.170": "보관용 검체",
    "211.176": "페니실린 교차오염",
    "211.180": "기록·보고 일반(보관기간·연간 품질평가)",
    "211.182": "설비 사용·세척 기록",
    "211.186": "마스터 제조지시서",
    "211.188": "배치 제조기록서",
    "211.192": "제조기록 검토와 일탈 조사",
    "211.194": "시험기록",
    "211.198": "불만 처리 기록",
    "211.204": "반품 의약품",
    "211.208": "회수품 재생",
  };

  function cfrSectionLabel(section) {
    return CFR_SECTION_LABELS[section] || "";
  }

  // 조문 원문(eCFR) 딥링크 — `/current/title-21/section-211.192` 형태가 유효함을
  // 실측 확인했다(HTTP 200). 부/서브파트 경로를 조립하지 않아 조항 이동 시에도 안 깨진다.
  function ecfrHref(section) {
    return "https://www.ecfr.gov/current/title-21/section-" + encodeURIComponent(section);
  }

  // [표본 하한] 그 해 총 지적이 이 값 미만이면 구성비를 말하지 않는다 — 수집 첫 해처럼
  // 문서 한두 건만 있는 연도는 한 건이 20%가 되어 색·비율이 전부 노이즈가 된다. 연도별
  // 구성비 히트맵의 열 제외와 헤드라인 일관성 판정이 같은 기준을 공유한다.
  var MIN_YEAR_BASE = 30;

  // 업체 상세 패널이 열려 있는지(?firm=)·직전 렌더의 top_firms(업체 랭킹 재렌더용)를
  // 여기 담는다 — findings.js 의 단일 state 객체 관례와 동형(별도 저장소 난립 금지).
  // openFirmKey — [업체 프로파일 진입] 017_findings_stats_firm_key.sql 적용 라이브에서만
  // top_firms 행에 firm_key 가 실려온다. 013 미적용/017 미적용 라이브(구버전 top_firms,
  // firm_name 만 있는 형태)에서는 빈 문자열로 남아 프로필 링크를 방어적으로 생략한다.
  // openCat/recentCats/recentCurDocs/exampleNode — [최근 12개월] 카테고리 순위에서 펼쳐진
  // 행과 그 사례 패널. 사례 패널은 비동기로 도착하므로 노드를 state 에 들고 있다가
  // renderRecentCats() 가 매번 같은 자리에 다시 끼워 넣는다(별도 저장소 난립 금지).
  var state = {
    // [컨셉 재정의] 기관 선택. 저장된 값이 있으면 그것, 없으면 식약처(기본).
    // ★fetch 응답을 통째로 들고 있는다 — 기관을 바꿀 때마다 다시 받지 않고 같은
    //   응답을 다시 접는다(추가 네트워크 호출 0). 041 한 번이 세 기관을 모두 담는다.
    agency: "",
    recentData: null,
    openFirm: "", openFirmKey: "", lastFirms: [],
    openCat: "", recentCats: [], recentCurFindings: 0, exampleNode: null,
    openCfr: "", cfrItems: [], cfrExampleNode: null,
  };

  // [인용 조항] 화면에 그리는 조항 수. 41개 전부 늘어놓으면 훑을 수 없고, 꼬리는 문서
  // 한두 건짜리라 순위로서 의미가 없다.
  var CFR_ROWS = 12;

  // ── [최근 12개월 · 달라진 점] 판정 상수 ─────────────────────────────────────
  // 창 전체 표본 하한 — 두 창 중 어느 쪽이든 지적이 이보다 적으면 증감 비교 자체를
  // 하지 않는다(패널을 숨긴다). 수십 건짜리 창에서는 한두 건이 몇 %p 를 움직여,
  // 비교가 신호가 아니라 잡음이 된다.
  var WINDOW_MIN_FINDINGS = 200;
  // 카테고리별 표본 하한 — 두 창 지적 수 합이 이보다 적은 영역은 증감 목록에서 뺀다.
  var MOVER_MIN_SAMPLE = 20;
  // 유의 폭 — 이보다 작은 변화는 "달라졌다"고 말하지 않는다(반올림 표기로는 커 보여도
  // 몇 건 차이인 구간이다).
  var MOVER_MIN_PP = 1.0;
  // 각 방향(증가/감소) 최대 표시 개수.
  var MOVER_MAX_ROWS = 5;
  // ── [소스 구성 정렬] 두 창의 모집단을 맞추는 기준 ─────────────────────────
  // ★왜 필요한가(실측 2026-08-06, 최근 2025-09~2026-08 vs 직전 2024-09~2025-08):
  //   국내 백필로 식약처 점유율이 직전 10.63% → 최근 30.01% 로 벌어졌다. 그 비대칭이
  //   구성비에 그대로 실려, 표시 8행 중 **5행이 유령이고 그중 3행은 부호까지 반대**였다
  //   (기타 품질시스템 +3.75 → 정렬 후 −0.18, 세척밸리데이션 +1.12 → −0.09,
  //    품질부서 감독 −1.61 → +0.02). 진짜 신호인 컴퓨터화시스템(+1.18)은 임계 아래에
  //   가려 안 보였다. 카테고리가 변한 게 아니라 모집단이 변한 것이다.
  //   → 두 창에서 견줄 수 있는 소스만 남겨 **분자와 분모를 함께** 좁힌다.
  //   ※ 분모에서만 빼면 결함이 커진다(실측: 기타 품질시스템 +3.65 → +5.27, 유령 2행 추가).
  // 표본 하한 — 한쪽 창의 건수가 이보다 적으면 그 소스는 그 기간을 대표하지 못한다.
  // (실측: MHRA GMP NCR 은 두 창 모두 1건 — 구성비를 말할 수 있는 표본이 아니다.)
  var MOVER_SOURCE_MIN = 10;
  // 점유율 배율 상한 — 두 창의 레인 점유율이 이 배수를 넘게 벌어졌으면 그 레인은
  // 카테고리를 말하는 게 아니라 **자기 자신이 들어오거나 빠진 것**이다.
  // ★★이 값을 쫓지 마라. 하루에 두 번 낡았다 — 설계 시점 식약처 99.2배(상한 3 제안)
  //   → 세션2 적재 후 2.82(상한 2 로 낮춤) → 세션3 적재 후 **1.572 로 게이트 통과**.
  //   임계값을 또 낮추는 것은 답이 아니었다. **문제는 임계값이 아니라 축의 입도였다** —
  //   식약처를 한 덩어리로 세니 회수(1.22)·GMP실사(3.69)·행정처분(67.0)이 서로를 가려
  //   합계가 "정상"으로 보였다. 축을 레인으로 낮추자 **같은 상한 2 로 정확히 갈렸다**.
  //   그러니 다음에 이 게이트가 헛돌면 값을 만지기 전에 **비교 단위가 맞는지** 먼저 보라.
  var MOVER_SOURCE_MAX_RATIO = 2;
  // 최근 12개월 카테고리 순위에 그리는 행 수.
  var RECENT_CAT_ROWS = 8;
  // 카테고리 사례 패널에 보여 줄 지적 문장 수 / 한 문장 표시 상한(글자).
  var EXAMPLE_ROWS = 3;
  var EXAMPLE_MAX_CHARS = 220;

  // ── 공용 헬퍼 ────────────────────────────────────────────────────────────
  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") e.textContent = text;
    return e;
  }

  function fmtNum(n) {
    var s = String(Math.round(Number(n) || 0));
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // [firm_name 엔티티 디코드 M5] findings.js 의 동명 헬퍼와 동일 계약(별도 파일이라
  // 재사용 불가, 계약만 복제) — DB firm_name 에 &amp;/&#039; 가 이미 이스케이프된 채로
  // 저장된 행을 표시 직전(textContent 대입 전)에만 되돌린다(순수 문자열 치환, XSS 무관).
  function decodeFirmDisplay(s) {
    return String(s || "").replace(/&amp;/g, "&").replace(/&#039;/g, "'");
  }

  // 클릭 가능한 div 행(role=button+tabindex+Enter/Space) — findings.js 의 동명 헬퍼와
  // 동일 계약(별도 파일이라 재사용 불가, 계약만 복제).
  function makeClickableRow(node, ariaLabel, onActivate) {
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.setAttribute("aria-label", ariaLabel);
    node.addEventListener("click", onActivate);
    node.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
        ev.preventDefault();
        onActivate();
      }
    });
  }

  // 카테고리 바 클릭 → 검색 페이지 필터 링크. findings.js 의 URL_KEYS.category_code="cat"
  // 계약을 그대로 따른다(파라미터명이 다르면 findings 페이지에서 필터가 걸리지 않는다).
  function findingsHref(paramKey, value) {
    return root + "findings/index.html?" + paramKey + "=" + encodeURIComponent(value);
  }

  function aggregateYears(byMonth) {
    var sums = {};
    (byMonth || []).forEach(function (r) {
      var m = r.month || "";
      if (m.length < 4) return;
      var y = m.slice(0, 4);
      sums[y] = (sums[y] || 0) + (r.cnt || 0);
    });
    return Object.keys(sums).sort().map(function (y) { return { year: y, cnt: sums[y] }; });
  }

  function aggregateCategories(byAgencyCategory) {
    var totals = {}, byAgency = {};
    (byAgencyCategory || []).forEach(function (r) {
      if (!r.category_code) return;
      totals[r.category_code] = (totals[r.category_code] || 0) + (r.cnt || 0);
      byAgency[r.category_code] = byAgency[r.category_code] || {};
      byAgency[r.category_code][r.agency || ""] =
        (byAgency[r.category_code][r.agency || ""] || 0) + (r.cnt || 0);
    });
    return Object.keys(totals).map(function (code) {
      var cat = CATEGORY_LABELS[code];
      var agencies = byAgency[code] || {};
      var agencyTitle = Object.keys(agencies)
        .filter(Boolean)
        .sort(function (a, b) { return agencies[b] - agencies[a]; })
        .map(function (a) { return a + " " + fmtNum(agencies[a]); })
        .join(" · ");
      return { code: code, ko: cat ? cat.ko : code, cnt: totals[code], agencyTitle: agencyTitle };
    }).sort(function (a, b) { return b.cnt - a.cnt || a.code.localeCompare(b.code); });
  }

  // ── 한눈 요약(에디토리얼 헤드라인) — 결정론 생성, 억지 통계 금지 ────────────────
  // 문장1 = 최다 카테고리 + 전체 대비 구성비(항상, 건수만으론 규모감이 안 잡힌다).
  // 문장2 = 그 카테고리가 **연도마다도** 1위인지(appendConsistencyLine — 008 매트릭스가
  //         도착한 뒤 조건부로 덧붙임). 조건 미충족이면 문장2 없이 끝낸다.
  //
  // 12차까지 있던 두 문장을 뺐다:
  //   · YoY 증감 — 공개일 기준이라 "규제가 늘었다"가 아니라 "그 해 공개가 많았다"를 잰다.
  //   · 최다 업체 — 공개 문서가 많은 업체가 1위로 잡히는 같은 편향이라, 업체 순위는
  //     "품질 순위가 아니다"라는 읽는 법을 붙인 랭킹 섹션에서만 다룬다.
  function catTotal(cats) {
    return cats.reduce(function (s, c) { return s + (c.cnt || 0); }, 0);
  }

  // 표시용 반올림 — 1% 미만을 "0%"로 적으면 없는 것처럼 읽히므로 소수 1자리로 내린다.
  function pctText(part, whole) {
    if (!whole) return "0%";
    var p = (part / whole) * 100;
    return (p > 0 && p < 10 ? Math.round(p * 10) / 10 : Math.round(p)) + "%";
  }

  // [헤드라인 제거 2026-07] "가장 많이 지적된 영역은…" 요약 + "연도별로 나눠 봐도…"
  // 연도별 일관성 문장을 통째로 제거했다 — 바로 아래 "카테고리 순위"(구성비 병기)와
  // "연도별 구성비" 히트맵이 1위 영역·연도별 일관성을 시각적으로 이미 보여줘 중복이었다.
  // (008 매트릭스는 히트맵 렌더 전용으로만 쓰인다.)

  // ── 스탯 스트립 ──────────────────────────────────────────────────────────
  function buildStat(num, label) {
    var block = el("div", "tr-stat");
    block.appendChild(el("span", "tr-stat-num", num));
    block.appendChild(el("span", "tr-stat-lbl", label));
    return block;
  }

  // [문서 수 병기] totals.documents(010_findings_scope_purity.sql findings_stats 신규
  // 키 — count distinct raw_signal_id, scope_status='ok' 기준)가 있을 때만 "분석 문서"
  // 스탯을 끼워 넣는다. 010 을 프로덕션 SQL Editor 에서 아직 적용하지 않은 라이브에서는
  // 이 키가 undefined 이므로 무조건 조용히 생략한다(레이아웃 깨짐 없음 — 기존 커버리지
  // 노트의 독립 폴백과 동일 정신). "지적 N건" 만 보면 문서(실사) 수로 오해하는 문제를
  // 완화하기 위해 총 지적사항 바로 옆에 둔다.
  function hasDocumentsCount(totals) {
    return typeof totals.documents === "number" && !isNaN(totals.documents);
  }

  // [완역 자동 전환] 미번역 잔량 판정 — 2026-07-15 백로그 완역 이후 잔량은 당일
  // 수집분(다음 날 아침 번역 배치가 처리) 또는 OCR 완파손뿐이므로, 잔량이 작으면
  // 미번역 백로그가 있는 것처럼 읽히는 보조 문구를 스스로 감춘다. renderCoverageNote()
  // 의 완료형 전환과 같은 기준을 공유한다.
  function untranslatedGap(totals) {
    return Number(totals.findings || 0) - Number(totals.public_findings || 0);
  }

  function renderStats(totals) {
    if (!statsEl) return;   // [존 재편] 이 면에 스탯 스트립이 없으면 조용히 no-op
    statsEl.innerHTML = "";
    statsEl.appendChild(buildStat(fmtNum(totals.findings), "총 지적사항"));
    if (hasDocumentsCount(totals)) {
      statsEl.appendChild(buildStat(fmtNum(totals.documents), "분석 문서"));
    }
    statsEl.appendChild(buildStat(fmtNum(totals.firms), "업체"));
    statsEl.appendChild(buildStat(fmtNum(totals.raw_signals), "원문서"));
    var pub = buildStat(fmtNum(totals.public_findings), "국문 열람 가능");
    if (Number(totals.findings || 0) > 0 && untranslatedGap(totals) > 5) {
      pub.appendChild(el("span", "tr-stat-note", "나머지는 집계에만 반영(원문 영문)"));
    }
    statsEl.appendChild(pub);
  }


  // [공개 범위 투명성] totals 는 fetchStats() 가 이미 fetch 한 findings_stats RPC 응답 —
  // 추가 네트워크 호출 없이 재사용한다. 요소가 없으면(구버전 셸) 조용히 no-op.
  // [문서 수 병기] totals.documents 가 있으면 첫 문장을 "규제 문서 N건에서 추출한 개별
  // 지적사항 M건"으로 바꿔 문서-지적 1:N 관계를 명시한다(010 미적용 시 undefined → 기존
  // "전체 M건" 문안 그대로 유지, 방어적 생략).
  function renderCoverageNote(totals) {
    if (!coverageNoteEl || !coverageTextEl) return;
    var total = Number(totals.findings || 0).toLocaleString("ko-KR");
    var intro = hasDocumentsCount(totals)
      ? "숫자는 규제 문서 " + Number(totals.documents).toLocaleString("ko-KR") +
        "건에서 뽑은 지적사항 " + total + "건 기준입니다."
      : "숫자는 전체 " + total + "건 기준입니다.";
    // [완역 자동 전환] 미번역 잔량이 5건 이하면(2026-07-15 백로그 완역 — 잔여는 OCR
    // 완파손 등 번역 불능 원문뿐) 미완료 경고를 완료형으로 스스로 전환한다(완역 시점엔
    // 카테고리 클릭 결과와 집계 수치가 일치하므로 경고 자체가 무의미).
    var isComplete = Number(totals.findings || 0) > 0 && untranslatedGap(totals) <= 5;
    // 미완료 분기는 완역 이후엔 당일 수집분이 다음 날 아침 번역 배치를 기다리는 짧은
    // 구간에만 나타난다 — "번역이 밀려 있다"가 아니라 "신규분이 번역 중"으로 읽히도록
    // 지연 사유를 명시한다. 집계 수치와 클릭 결과가 다를 수 있다는 실질 안내는 유지.
    coverageTextEl.textContent = isComplete
      ? intro + " 모두 국문으로 볼 수 있습니다."
      : intro + " 신규 수집분은 번역 완료 전까지 목록에서 영어 원문으로만 표시됩니다.";
    coverageNoteEl.hidden = false;
  }

  // ── [기관 선택] 컨셉 재정의 2026-08-26 ───────────────────────────────────
  // ★왜 합산을 기본값으로 두지 않는가(실측): '기타'를 빼고 기관별로 최근 12개월 상위 5를
  //   세면 **FDA 와 식약처가 하나도 겹치지 않는다** —
  //     FDA   : 무균보증 319 · 설비/시설 204 · 시험실 199 · 표시/포장 174 · 품질부서 167
  //     식약처: 불만/회수 187 · 세척밸리 69 · 문서화 68 · 밸리데이션 51 · 안정성 50
  //   합산 순위는 세 개의 다른 규제 현실을 평균 낸 값이고, 그 평균은 **어느 기관의
  //   현실도 아니다**. 그것을 기본 화면으로 두는 것은 오답을 기본값으로 두는 것이다.
  //   이 저장소가 052/053/054 에서 세 번 겪은 "성격이 다른 모집단을 한 축에 합치면
  //   진단이 반드시 틀린다"를 표시 계층에서 반복하지 않는다.
  //
  // ★기본값이 식약처인 이유: 독자가 국내 제약 실무자다. FDA 는 수출하는 사람에게만
  //   걸리지만 식약처는 전원에게 걸린다. 한 번 고르면 브라우저가 기억한다.
  //
  // ★레인 매칭은 **접두 문자열**이다(정규식 아님) — 053 의 lane 은
  //   'MFDS/gmp-inspection' 처럼 채널까지 쪼개져 있고, 새 채널이 생겨도 접두가 같으면
  //   자동으로 편입된다. 반대로 정규식을 쓰면 이스케이프 실수 하나가 조용히 0건을 만든다.
  var AGENCY_VIEWS = [
    { key: "mfds", label: "식약처", prefix: "MFDS" },
    { key: "fda", label: "FDA", prefix: "FDA" },
    { key: "all", label: "전체", prefix: "" },
  ];
  var AGENCY_STORE_KEY = "grm-trends-agency";

  function agencyView(key) {
    for (var i = 0; i < AGENCY_VIEWS.length; i += 1) {
      if (AGENCY_VIEWS[i].key === key) return AGENCY_VIEWS[i];
    }
    return AGENCY_VIEWS[0];
  }

  // 저장된 선택 읽기 — 사생활 모드·저장 차단 브라우저에서 접근 자체가 던지므로 감싼다.
  function readStoredAgency() {
    try {
      var v = window.localStorage.getItem(AGENCY_STORE_KEY);
      return v && agencyView(v).key === v ? v : "";
    } catch (e) { return ""; }
  }

  function storeAgency(key) {
    try { window.localStorage.setItem(AGENCY_STORE_KEY, key); } catch (e) { /* 무시 */ }
  }

  // 교차표에서 이 기관에 속하는 레인만 골라 kept 맵을 만든다(foldCategorySource 입력).
  function agencyKept(grid, view) {
    var kept = {};
    (grid || []).forEach(function (r) {
      var lane = r.lane || r.source;      // 053 미적용 응답은 lane 이 없다 → 소스 축 폴백
      if (!lane) return;
      if (!view.prefix || lane.indexOf(view.prefix) === 0) kept[lane] = true;
    });
    return kept;
  }

  // 고른 기관의 순위 행을 만든다.
  // ★'기타 품질시스템'은 **순위에서 빼되 분모에는 남긴다**. 빼면서 분모까지 줄이면
  //   나머지 항목의 비율이 부풀어 "무균보증이 12%"가 "무균보증이 15%"로 보인다 —
  //   분류 실패를 감추려다 다른 수치를 거짓말하게 만드는 흔한 실수다.
  function buildAgencyRanking(data, view) {
    var grid = (data && data.by_category_source) || [];
    if (!grid.length) return null;        // 052/053 미적용 → 기관 선택 자체가 불가
    var kept = agencyKept(grid, view);
    if (!Object.keys(kept).length) return null;
    var folded = foldCategorySource(grid, kept);
    var total = 0, excluded = 0;
    folded.forEach(function (c) {
      var n = Number(c.cur_cnt) || 0;
      total += n;                          // 분모 = 기타 포함 전량
      if (c.category_code === "other_quality_system") excluded += n;
    });
    if (!(total > 0)) return null;
    var rows = folded.filter(function (c) {
      return c.category_code !== "other_quality_system" && (Number(c.cur_cnt) || 0) > 0;
    }).map(function (c) {
      var label = CATEGORY_LABELS[c.category_code];
      return {
        code: c.category_code,
        ko: label ? label.ko : c.category_code,
        docs: c.cur_docs || 0,
        cnt: Number(c.cur_cnt) || 0,
      };
    }).sort(function (a, b) {
      return b.cnt - a.cnt || a.code.localeCompare(b.code);
    }).slice(0, RECENT_CAT_ROWS);
    return { rows: rows, total: total, excluded: excluded, lanes: Object.keys(kept) };
  }

  // 읽는 법은 **고른 기관에 맞춰 다시 적는다** — 분모가 바뀌면 설명도 바뀌어야 한다.
  function agencyReadText(view) {
    if (view.key === "all")
      // ★"전체"가 무엇을 합친 것인지 말한다 — 안 적으면 독자가 자기와 관련 있는 기관만
      //   들어 있다고 가정한다(실제로는 캐나다 실사가 이 창의 32%를 차지한다).
      return "식약처·FDA 에 캐나다 실사와 EU·영국 GMP 비준수까지 합쳐 센 순위입니다. " +
        "기관마다 많이 지적하는 영역이 크게 달라(식약처와 FDA 는 상위 항목이 겹치지 않습니다) " +
        "실사를 준비하신다면 해당 기관을 골라 보세요.";
    // ★기관명에 한국어 조사를 붙이지 않는다 — 조사는 앞말의 받침으로 갈리는데 기관명엔
    //   영문 약어(FDA)가 섞여 규칙이 성립하지 않는다("FDA이(가)"). 명사구로만 잇는다.
    return "최근 12개월 " + view.label + " 문서에서만 셉니다. 오른쪽 %는 그 기간 " +
      view.label + " 지적 전체 중 이 영역이 차지하는 비율이에요. 줄을 누르면 실제 지적 문장을 볼 수 있습니다.";
  }

  // 선택을 화면에 반영한다(버튼 상태 + 순위 + 달라진 점). 클릭·최초 렌더 양쪽에서 호출.
  function applyAgency() {
    if (!rankBlockEl) return;
    var data = state.recentData;
    if (!data) return;
    var view = agencyView(state.agency);
    var built = buildAgencyRanking(data, view);
    if (!built) {
      // 레인을 가를 수 없는 응답(052/053 미적용) — 기관 선택을 숨기고 종전 합산으로 후퇴.
      if (agencyEl) agencyEl.hidden = true;
      built = fallbackRanking(data);
      if (!built) return;
      view = agencyView("all");
    }
    if (agencyBtnsEl) {
      var kids = agencyBtnsEl.children;
      for (var i = 0; i < kids.length; i += 1) {
        var on = kids[i].getAttribute("data-agency") === view.key;
        kids[i].setAttribute("aria-pressed", on ? "true" : "false");
      }
    }
    state.recentCats = built.rows;
    state.recentCurFindings = built.total;
    state.openCat = "";                    // 기관을 바꾸면 펼쳐 둔 사례 패널을 닫는다
    state.exampleNode = null;
    renderRecentCats();
    if (rankReadEl) rankReadEl.textContent = agencyReadText(view);
    if (rankSubEl) {
      var scope = (data.scope || {});
      rankSubEl.textContent = monthLabelKo(scope.cur_from) + " ~ " + monthLabelKo(scope.cur_to) +
        " · " + view.label + " 지적 " + fmtNum(built.total) + "건";
    }
    if (rankNoteEl) {
      var note = "";
      if (built.excluded > 0) {
        // 규율 2 — 감추지 않고 크기를 밝힌다. 분모에는 남아 있다는 사실도 함께 적는다.
        note = "이 기간 " + view.label + " 지적의 " + pctText(built.excluded, built.total) +
          "(" + fmtNum(built.excluded) + "건)는 아직 세부 분류가 되지 않아 순위에서 뺐습니다 — " +
          "위 비율의 분모에는 그대로 들어 있습니다.";
      }
      rankNoteEl.textContent = note;
      rankNoteEl.hidden = !note;
    }
    renderMovers(data, view);
    applyAgencyToCfr(view);
    rankBlockEl.hidden = false;
  }

  // 조항 순위는 042 가 21 CFR 만 센다(식약처 조항 축은 아직 없다). 식약처를 고른
  // 사용자에게 이 순위는 **다른 나라 규정**이므로, 그 사실을 읽는 법이 먼저 말한다 —
  // 말없이 그대로 두면 "식약처 기준"으로 읽히고, 그게 이 재정의가 없애려는 오독이다.
  function applyAgencyToCfr(view) {
    if (!cfrReadEl) return;
    var tail = "줄을 누르면 그 조항으로 지적된 실제 문장과 조문 원문으로 갑니다.";
    cfrReadEl.textContent = view && view.key === "mfds"
      // 식약처를 고른 사람에게 21 CFR 은 다른 나라 규정이다. 말없이 두면 "식약처 기준"으로
      // 읽히므로 그 사실을 먼저 말하되, 버리라는 뜻이 아니라는 것도 함께 말한다.
      ? "아래는 미국 21 CFR 조항 순위입니다 — 식약처 지적서에는 이 조항이 인용되지 않습니다. " +
        "다만 요구사항 자체는 GMP 공통이라 무엇을 확인해야 하는지의 목록으로는 그대로 쓸 수 있어요. " + tail
      : "규제기관이 지적서에 실제로 적은 조항 순위입니다. 카테고리보다 한 단계 구체적이라 " +
        "사내 절차서와 바로 맞대어 볼 수 있어요 — " + tail;
  }

  // 052/053 미적용 라이브용 후퇴 경로 — by_category(합산)로 종전처럼 그린다.
  function fallbackRanking(data) {
    var byCat = (data && data.by_category) || [];
    var cur = ((data.totals || {}).cur) || {};
    if (!byCat.length || !(Number(cur.findings) > 0)) return null;
    var rows = byCat.map(function (c) {
      var label = CATEGORY_LABELS[c.category_code];
      return {
        code: c.category_code,
        ko: label ? label.ko : c.category_code,
        docs: c.cur_docs || 0,
        cnt: Number(c.cur_cnt) || 0,
      };
    }).filter(function (c) { return c.cnt > 0; })
      .sort(function (a, b) { return b.cnt - a.cnt || a.code.localeCompare(b.code); })
      .slice(0, RECENT_CAT_ROWS);
    return { rows: rows, total: Number(cur.findings) || 0, excluded: 0, lanes: [] };
  }

  function wireAgency() {
    if (!agencyBtnsEl) return;
    agencyBtnsEl.innerHTML = "";
    AGENCY_VIEWS.forEach(function (v) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "tr-agency-btn";
      b.setAttribute("data-agency", v.key);
      b.setAttribute("aria-pressed", v.key === state.agency ? "true" : "false");
      b.textContent = v.label;
      b.addEventListener("click", function () {
        state.agency = v.key;
        storeAgency(v.key);
        applyAgency();
      });
      agencyBtnsEl.appendChild(b);
    });
  }

  // ── 카테고리 순위(메인 시각) — 상위 10, 순위별 opacity 100→40% 농도 단계 ─────────
  // 건수 옆에 전체 대비 구성비를 항상 병기한다(13차) — "2,405건"만으로는 그게 전체의
  // 3%인지 30%인지 알 수 없어, 순위표가 규모감 없는 숫자 나열로 읽혔다.
  function buildCatRow(entry, idx, maxCnt, total) {
    var a = document.createElement("a");
    a.className = "tr-cat-row";
    a.href = findingsHref("cat", entry.code);
    if (entry.agencyTitle) a.title = entry.agencyTitle;
    a.appendChild(el("span", "tr-cat-rank", String(idx + 1)));
    a.appendChild(el("span", "tr-cat-label", entry.ko));
    var track = document.createElement("div");
    track.className = "tr-cat-track";
    var bar = document.createElement("div");
    bar.className = "tr-cat-bar";
    var ratio = maxCnt > 0 ? entry.cnt / maxCnt : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    bar.style.opacity = String(Math.max(0.4, 1 - idx * (0.6 / 9)));
    track.appendChild(bar);
    a.appendChild(track);
    a.appendChild(el("span", "tr-cat-count", fmtNum(entry.cnt) + "건"));
    a.appendChild(el("span", "tr-cat-share", pctText(entry.cnt, total)));
    return a;
  }

  function renderCategoryRanking(byAgencyCategory) {
    if (!catEl) return;     // [존 재편] 이 면에 전 기간 순위 pane 이 없으면 조용히 no-op
    catEl.innerHTML = "";
    var all = aggregateCategories(byAgencyCategory);
    var total = catTotal(all);              // 구성비 분모는 상위 10이 아니라 전체 카테고리
    var cats = all.slice(0, 10);
    if (!cats.length) {
      catEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = cats[0].cnt || 1;
    cats.forEach(function (c, i) { catEl.appendChild(buildCatRow(c, i, maxCnt, total)); });
  }

  // ── 연도별 구성비 히트맵(H1) ─────────────────────────────────────────────
  // findings_stats()(007)엔 카테고리×시간 매트릭스가 없어 findings_category_matrix()
  // (008)를 별도 RPC 로 병렬 fetch 한다 — 실패해도(008 미적용 라이브 포함) 이 섹션만
  // 조용히 숨겨진 채로 남고 다른 섹션엔 전혀 영향이 없다(§ 오케스트레이션 하단 참조).
  //
  // [13차] 셀 값 = 건수 → **그 해 전체 대비 비율(%)**, 즉 열 정규화(각 열의 합 = 100%).
  // 이유: 공개 배치 편중으로 연도별 확보량이 10배 넘게 차이나, 건수 히트맵은 한 해 열만
  // 진하게 타오르고 나머지는 전부 흐린 "공개량 지도"였다. 열을 정규화하면 연도끼리 모양을
  // 비교할 수 있게 되고, 특정 영역이 매년 반복되는 구조적 패턴인지 한 해짜리 잡음인지가
  // 비로소 보인다.
  //
  // 농도 버킷도 행렬 최댓값 상대 → **비율 절대 기준**으로 바꿨다. 상대 기준이면 같은 색이
  // 표마다 다른 뜻이 되지만, 절대 기준이면 "진한 칸 = 그 해의 25% 이상"으로 어느 열에서나
  // 같은 의미를 갖는다.
  var HEATMAP_OPACITY_STEPS = [0.08, 0.25, 0.45, 0.7, 1.0];
  var HEATMAP_SHARE_BREAKS = [25, 15, 8, 3];   // % 기준 — 위에서부터 진한 단계

  function shareOpacity(share) {
    if (!share || share <= 0) return 0;
    if (share >= HEATMAP_SHARE_BREAKS[0]) return HEATMAP_OPACITY_STEPS[4];
    if (share >= HEATMAP_SHARE_BREAKS[1]) return HEATMAP_OPACITY_STEPS[3];
    if (share >= HEATMAP_SHARE_BREAKS[2]) return HEATMAP_OPACITY_STEPS[2];
    if (share >= HEATMAP_SHARE_BREAKS[3]) return HEATMAP_OPACITY_STEPS[1];
    return HEATMAP_OPACITY_STEPS[0];
  }

  function renderHeatmap(data) {
    if (!heatmapEl || !heatmapBlockEl) return;   // [존 재편] 데이터 현황 면에만 있다
    heatmapEl.innerHTML = "";
    var allYears = data.years || [];
    var cats = (data.category_totals || []).slice(0, 12);
    if (!cats.length || !allYears.length) {
      heatmapEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      heatmapBlockEl.hidden = false;
      return;
    }
    var cellMap = {}, yearBase = {};
    // 분모(그 해 총 지적)는 표에 그리는 상위 12개가 아니라 **전 카테고리** 합이어야 한다 —
    // 상위 12개만으로 나누면 각 열의 비율이 실제보다 부풀려진다.
    (data.cells || []).forEach(function (c) {
      cellMap[c.category_code + "|" + c.year] = c.cnt || 0;
      yearBase[c.year] = (yearBase[c.year] || 0) + (c.cnt || 0);
    });

    // 표본이 얇은 연도는 열 자체를 뺀다 — 다만 뺐다는 사실을 적을 자리가 없으면(구버전 셸)
    // 침묵 절단이 되므로 아예 빼지 않는다(조용한 축소 금지).
    var years = allYears, dropped = [];
    if (heatmapNoteEl) {
      years = allYears.filter(function (y) { return (yearBase[y] || 0) >= MIN_YEAR_BASE; });
      dropped = allYears.filter(function (y) { return (yearBase[y] || 0) < MIN_YEAR_BASE; });
      if (!years.length) { years = allYears; dropped = []; }
    }

    var scroll = document.createElement("div");
    scroll.className = "tr-heatmap-scroll";
    var table = document.createElement("table");
    table.className = "tr-heatmap-table";

    var caption = document.createElement("caption");
    caption.className = "tr-heatmap-caption";
    caption.textContent = "연도별 지적 구성비(각 연도를 100%로 본 비율 — 코럴 농도가 그 해에서의 비중을 나타냅니다)";
    table.appendChild(caption);

    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    var cornerTh = document.createElement("th");
    cornerTh.setAttribute("scope", "col");
    cornerTh.className = "tr-heatmap-corner";
    cornerTh.textContent = "카테고리";
    headRow.appendChild(cornerTh);
    years.forEach(function (y) {
      var th = document.createElement("th");
      th.setAttribute("scope", "col");
      th.className = "tr-heatmap-yearhead";
      th.appendChild(el("span", "", y));
      // 분모 병기 — %만 있으면 표본 크기를 알 수 없어 얇은 해의 큰 %를 과대해석하게 된다.
      th.appendChild(el("span", "tr-heatmap-yearbase", fmtNum(yearBase[y] || 0) + "건"));
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    cats.forEach(function (c) {
      var label = CATEGORY_LABELS[c.category_code];
      var ko = label ? label.ko : c.category_code;
      var row = document.createElement("tr");
      var rowTh = document.createElement("th");
      rowTh.setAttribute("scope", "row");
      rowTh.className = "tr-heatmap-rowhead";
      rowTh.textContent = ko;
      row.appendChild(rowTh);
      years.forEach(function (y) {
        var cnt = cellMap[c.category_code + "|" + y] || 0;
        var base = yearBase[y] || 0;
        var share = base > 0 ? (cnt / base) * 100 : 0;
        var td = document.createElement("td");
        td.className = "tr-heatmap-cell";
        td.title = ko + " · " + y + " · " + fmtNum(cnt) + "건(그 해 " + fmtNum(base) +
          "건 중 " + pctText(cnt, base) + ")";
        if (cnt > 0) {
          var opacity = shareOpacity(share);
          td.style.backgroundColor = "rgba(194,96,63," + opacity + ")";
          td.style.color = opacity > 0.45 ? "var(--on-coral)" : "var(--ink)";
          td.textContent = pctText(cnt, base);
        } else {
          td.classList.add("tr-heatmap-cell-empty");
        }
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    scroll.appendChild(table);
    heatmapEl.appendChild(scroll);

    if (heatmapNoteEl) {
      heatmapNoteEl.textContent = dropped.length
        ? ("표에는 지적이 " + MIN_YEAR_BASE + "건 이상 쌓인 연도만 넣었습니다 — " +
           dropped.join("·") + "년은 자료가 너무 적어 비율이 의미를 갖지 못해 뺐습니다.")
        : "";
      heatmapNoteEl.hidden = !dropped.length;
    }
    heatmapBlockEl.hidden = false;
  }

  // ── 연도별 공개량(참고) ──────────────────────────────────────────────────
  // 이 차트만은 절대 건수를 그대로 둔다 — 여기서 재는 것이 규제 활동이 아니라 "연도별로
  // 우리가 확보한 자료의 양" 자체이기 때문이다(제목·읽는 법이 그렇게 못박는다). 대신 전체
  // 대비 비중을 병기해, 한 해가 전체의 몇 %를 차지하는지(=공개 배치 편중)를 드러낸다.
  function renderYearTrend(byMonth) {
    if (!yearEl) return;    // [존 재편] 데이터 현황 면에만 있다
    yearEl.innerHTML = "";
    var years = aggregateYears(byMonth);
    if (!years.length) {
      yearEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = years.reduce(function (m, y) { return Math.max(m, y.cnt); }, 0) || 1;
    var total = years.reduce(function (s, y) { return s + y.cnt; }, 0);
    var wrap = document.createElement("div");
    wrap.className = "tr-year-bars";
    years.forEach(function (y) {
      var col = document.createElement("div");
      col.className = "tr-year-col";
      var barwrap = document.createElement("div");
      barwrap.className = "tr-year-barwrap";
      var bar = document.createElement("div");
      bar.className = "tr-year-bar";
      bar.style.height = Math.max(4, Math.round((y.cnt / maxCnt) * 100)) + "%";
      barwrap.appendChild(bar);
      col.appendChild(barwrap);
      col.appendChild(el("span", "tr-year-lbl", y.year));
      col.appendChild(el("span", "tr-year-count", fmtNum(y.cnt)));
      col.appendChild(el("span", "tr-year-share", pctText(y.cnt, total)));
      wrap.appendChild(col);
    });
    yearEl.appendChild(wrap);
  }

  // ── 업체 랭킹 Top 30 + 상세 패널 ─────────────────────────────────────────
  function buildFirmRow(f, idx, maxCnt) {
    var row = document.createElement("div");
    row.className = "tr-firm-row";
    if (state.openFirm === f.firm_name) row.classList.add("on");
    // [firm_name 엔티티 디코드 M5] 클릭/state 비교는 raw f.firm_name(DB 원본값) 그대로 —
    // openFirm()/syncFirmUrl() 이 그 값을 findings_firm_stats RPC exact-match 파라미터로
    // 쓰므로 디코드하면 어긋난다. 디코드는 표시(라벨·aria-label)에만 적용한다.
    var firmDisplay = decodeFirmDisplay(f.firm_name);
    makeClickableRow(row, firmDisplay + " 상세 보기: " + f.cnt + "건", function () {
      if (state.openFirm === f.firm_name) closeFirm();
      else openFirm(f.firm_name, f.firm_key);
    });
    row.appendChild(el("span", "tr-firm-rank", String(idx + 1)));
    row.appendChild(el("span", "tr-firm-name", firmDisplay));
    var track = document.createElement("div");
    track.className = "tr-firm-bar";
    var fill = document.createElement("div");
    fill.className = "tr-firm-bar-fill";
    var ratio = maxCnt > 0 ? f.cnt / maxCnt : 0;
    fill.style.width = Math.max(2, Math.round(ratio * 100)) + "%";
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("span", "tr-firm-count", fmtNum(f.cnt)));
    return row;
  }

  // [존 재편 2026-08-26] 30행 → 10행. 이 순위는 '품질이 나쁜 순'이 아니라 **우리가
  // 확보한 문서가 많은 순**이고, 재편 전 화면은 그 사실을 자기 설명문에서 부인하면서
  // 30행을 실었다. 순위를 지우지는 않는다 — 데이터 현황 면의 주제가 바로 "우리 데이터가
  // 어느 쪽으로 기울어 있나"라 여기서는 그 기울기가 정보다. 다만 10행이면 기울기를
  // 보여주기에 충분하고, 30행은 순위표로 오독될 여지만 키운다.
  var FIRM_ROWS = 10;

  function renderFirmRanking(topFirms) {
    if (!firmsEl) return;   // [존 재편] 데이터 현황 면에만 있다
    firmsEl.innerHTML = "";
    if (!topFirms.length) {
      firmsEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var rows = topFirms.slice(0, FIRM_ROWS);
    var maxCnt = rows[0].cnt || 1;
    rows.forEach(function (f, i) { firmsEl.appendChild(buildFirmRow(f, i, maxCnt)); });
  }

  function buildFirmDetailCatCol(byCategory) {
    var col = document.createElement("div");
    col.appendChild(el("h4", "tr-fd-h", "카테고리 분포"));
    var rows = byCategory.map(function (r) {
      var cat = CATEGORY_LABELS[r.category_code];
      return { ko: cat ? cat.ko : r.category_code, cnt: r.cnt || 0 };
    }).sort(function (a, b) { return b.cnt - a.cnt; }).slice(0, 6);
    if (!rows.length) {
      col.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return col;
    }
    var maxCnt = rows[0].cnt || 1;
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "tr-fd-row";
      row.appendChild(el("span", "tr-fd-label", r.ko));
      var track = document.createElement("div");
      track.className = "tr-fd-track";
      var bar = document.createElement("div");
      bar.className = "tr-fd-bar";
      bar.style.transform = "scaleX(" + Math.max(0.02, r.cnt / maxCnt) + ")";
      track.appendChild(bar);
      row.appendChild(track);
      row.appendChild(el("span", "tr-fd-count", fmtNum(r.cnt)));
      col.appendChild(row);
    });
    return col;
  }

  function buildFirmDetailYearCol(byMonth) {
    var col = document.createElement("div");
    // "추이"가 아니라 "공개량" — 이 막대도 실사 시점이 아니라 공개 시점 분포다(페이지 전체
    // 규칙과 같은 이유, 파일 머리 §13차 (1)). 업체 단위라 표본이 더 작으니 더욱 그렇다.
    col.appendChild(el("h4", "tr-fd-h", "연도별 공개량"));
    var years = aggregateYears(byMonth);
    if (!years.length) {
      col.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return col;
    }
    var maxCnt = years.reduce(function (m, y) { return Math.max(m, y.cnt); }, 0) || 1;
    var wrap = document.createElement("div");
    wrap.className = "tr-fd-year-bars";
    years.forEach(function (y) {
      var c = document.createElement("div");
      c.className = "tr-fd-year-col";
      var barwrap = document.createElement("div");
      barwrap.className = "tr-fd-year-barwrap";
      var bar = document.createElement("div");
      bar.className = "tr-fd-year-bar";
      bar.style.height = Math.max(4, Math.round((y.cnt / maxCnt) * 100)) + "%";
      barwrap.appendChild(bar);
      c.appendChild(barwrap);
      c.appendChild(el("span", "tr-fd-year-lbl", y.year.slice(2)));
      wrap.appendChild(c);
    });
    col.appendChild(wrap);
    return col;
  }

  function buildFirmDetailSourceRow(bySource) {
    var wrap = document.createElement("div");
    wrap.className = "tr-fd-src";
    wrap.appendChild(el("h4", "tr-fd-h", "소스 구성"));
    var sorted = bySource.slice().sort(function (a, b) { return (b.cnt || 0) - (a.cnt || 0); });
    if (!sorted.length) {
      wrap.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return wrap;
    }
    var list = document.createElement("p");
    list.className = "tr-fd-src-list";
    sorted.forEach(function (s, i) {
      if (i > 0) list.appendChild(document.createTextNode(" · "));
      list.appendChild(document.createTextNode(s.source + " " + fmtNum(s.cnt) + "건"));
    });
    wrap.appendChild(list);
    return wrap;
  }

  // [업체 프로파일 진입] 017_findings_stats_firm_key.sql 적용 라이브에서 top_firms
  // 행에 firm_key 가 실려올 때만 렌더한다(state.openFirmKey, openFirm 호출 시점에
  // 세팅) — 017 미적용 라이브(top_firms 에 firm_key 없음)에서는 빈 문자열이라 링크
  // 자체를 생략한다(방어, 레이아웃 깨짐 없음). findings/firm/index.html 은 findings/
  // trends/index.html 과 같은 findings/ 하위 형제 디렉터리라 rel_root 계산 없이
  // "../firm/index.html" 상대경로 하나로 충분하다(findings.js buildDocHead 의
  // "firm/index.html" 관례와 동형 — 깊이만 한 단계 다르다).
  function buildFirmProfileLink(firmKey) {
    var a = document.createElement("a");
    a.className = "tr-fd-profile-link";
    a.href = "../firm/index.html?key=" + encodeURIComponent(firmKey);
    a.textContent = "업체 프로파일 전체 보기 →";
    return a;
  }

  function renderFirmDetail(data) {
    firmDetailEl.innerHTML = "";
    if (state.openFirmKey) {
      firmDetailEl.appendChild(buildFirmProfileLink(state.openFirmKey));
    }
    var head = document.createElement("div");
    head.className = "tr-firm-detail-head";
    var idbox = document.createElement("div");
    idbox.appendChild(el("h3", "tr-firm-detail-name", decodeFirmDisplay(data.firm_name || "")));
    var period = (data.first_seen || "?") + " ~ " + (data.last_seen || "?");
    idbox.appendChild(el("p", "tr-firm-detail-meta",
      period + " · 총 " + fmtNum((data.totals || {}).findings || 0) + "건"));
    head.appendChild(idbox);
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "tr-firm-detail-close";
    closeBtn.setAttribute("aria-label", "업체 상세 닫기");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", closeFirm);
    head.appendChild(closeBtn);
    firmDetailEl.appendChild(head);

    var grid = document.createElement("div");
    grid.className = "tr-firm-detail-grid";
    grid.appendChild(buildFirmDetailCatCol(data.by_category || []));
    grid.appendChild(buildFirmDetailYearCol(data.by_month || []));
    firmDetailEl.appendChild(grid);
    firmDetailEl.appendChild(buildFirmDetailSourceRow(data.by_source || []));
  }

  function renderFirmDetailLoading() {
    firmDetailEl.innerHTML = "";
    firmDetailEl.appendChild(el("p", "tr-empty", "불러오는 중…"));
  }

  function renderFirmDetailError() {
    firmDetailEl.innerHTML = "";
    firmDetailEl.appendChild(el("p", "tr-empty", "업체 통계를 불러오지 못했습니다."));
  }

  // ?firm= 은 findings_firm_stats(p_firm) 의 exact-match 계약을 따른다(top_firms.firm_name
  // 값 그대로만 넘긴다) — URLSearchParams 가 인코딩/디코딩을 전담(pushState 는 쓰지 않는다,
  // 뒤로가기 히스토리 오염 방지, findings.js 와 동일 원칙).
  function syncFirmUrl(name) {
    if (typeof history === "undefined" || !history.replaceState || typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    if (name) params.set("firm", name); else params.delete("firm");
    var qs = params.toString();
    var newUrl = location.pathname + (qs ? "?" + qs : "") + location.hash;
    history.replaceState(null, "", newUrl);
  }

  function openFirm(name, firmKey) {
    if (!firmDetailEl) return;   // [존 재편] 이 면엔 업체 상세 패널이 없다(딥링크 무시)
    state.openFirm = name;
    state.openFirmKey = firmKey || "";
    renderFirmRanking(state.lastFirms);
    syncFirmUrl(name);
    firmDetailEl.hidden = false;
    renderFirmDetailLoading();
    fetchFirmStats(name).then(function (data) {
      renderFirmDetail(data);
      if (typeof firmDetailEl.scrollIntoView === "function") {
        firmDetailEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }).catch(function () {
      renderFirmDetailError();
    });
  }

  function closeFirm() {
    state.openFirm = "";
    state.openFirmKey = "";
    renderFirmRanking(state.lastFirms);
    syncFirmUrl("");
    firmDetailEl.hidden = true;
    firmDetailEl.innerHTML = "";
  }

  // ?firm= 으로 직접 진입한 경우(북마크·공유 링크 등)에도 프로필 링크가 뜨도록,
  // 이미 fetch 된 state.lastFirms(top_firms) 에서 이름이 일치하는 행의 firm_key 를
  // 찾아 함께 넘긴다 — 017 미적용 라이브에서는 어차피 firm_key 가 없어 "" 로 방어된다.
  function findFirmKeyByName(name) {
    for (var i = 0; i < state.lastFirms.length; i++) {
      if (state.lastFirms[i].firm_name === name) return state.lastFirms[i].firm_key || "";
    }
    return "";
  }

  function maybeOpenFirmFromUrl() {
    if (typeof URLSearchParams === "undefined") return;
    var params = new URLSearchParams(location.search);
    var f = params.get("firm");
    if (f) openFirm(f, findFirmKeyByName(f));
  }

  // ── 소스 구성 ────────────────────────────────────────────────────────────
  // 구성비를 병기한다 — 소스 편중은 이 페이지 전체 해석의 전제라, 건수만 적어 두면 독자가
  // 스스로 나눠 봐야 알 수 있다.
  // ★이 주석에 적혀 있던 "현재 FDA 483 이 98% 이상"은 **낡아서 크게 틀린 값**이었다
  // (2026-08 실측 41.0% · Health Canada 실사 38.3%). 주석에 실측치를 박아 두면 반드시
  // 낡고, 낡은 주석은 다음 사람의 판단을 망친다 — 실제로 이 세션에서 "겹침의 원인은
  // 라벨 CSS"라고 단언한 주석이 두 번째 원인을 몇 주간 가렸다. 비율은 화면이 그린다.
  //
  // 13차에 이 옆에 있던 "증거 등급 구성"(A/B/C 스택 바)은 삭제했다: 실데이터가 A 99% 이상
  // 단일값이라 분포 차트로서 정보가 0이고, 증거 등급 자체가 내부 QA 개념이다.
  function renderSource(bySource) {
    if (!sourceEl) return;  // [존 재편] 데이터 현황 면에만 있다
    sourceEl.innerHTML = "";
    var sorted = (bySource || []).slice().sort(function (a, b) { return (b.cnt || 0) - (a.cnt || 0); });
    if (!sorted.length) {
      sourceEl.appendChild(el("p", "tr-empty", "표시할 데이터가 없습니다."));
      return;
    }
    var maxCnt = sorted[0].cnt || 1;
    var total = sorted.reduce(function (s, r) { return s + (r.cnt || 0); }, 0);
    sorted.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "tr-src-row";
      row.appendChild(el("span", "tr-src-label", s.source));
      var track = document.createElement("div");
      track.className = "tr-src-track";
      var bar = document.createElement("div");
      bar.className = "tr-src-bar";
      bar.style.transform = "scaleX(" + Math.max(0.02, s.cnt / maxCnt) + ")";
      track.appendChild(bar);
      row.appendChild(track);
      row.appendChild(el("span", "tr-src-count", fmtNum(s.cnt)));
      row.appendChild(el("span", "tr-src-share", pctText(s.cnt, total)));
      sourceEl.appendChild(row);
    });
  }

  // ── [해외 vs 미국 내 실사] 카테고리별 지적 패턴 비교 ─────────────────────────
  // findings_zone_category()(038, 파라미터 없음) — findings_stats/findings_category_matrix
  // 와 독립된 별개 RPC. FDA 483 소재국(site_country)만 채워져 있어(WL 은 전량 미상) 이
  // 함수 자체가 483 으로 범위를 한정하고, 그 사실을 응답의 scope.source 에 실어 온다.
  // 실패해도(RPC 미배포 라이브 포함) 이 섹션만 조용히 숨겨진 채로 남고 다른 섹션엔
  // 전혀 영향이 없다(히트맵과 동일 원칙 — § 오케스트레이션 하단 fetchZoneCategory 참조).
  //
  // ★비율은 서버가 안 준다(038 계약 — "서버는 센다") — 각 zone(해외/미국) 카테고리
  // 합(Σforeign_cnt/Σus_cnt)으로 나눈 **zone 내 점유율**을 여기서 계산한다. 절대 건수를
  // 그대로 비교하면 안 된다 — 미국(7,288건)이 해외(905건)보다 8배 커서, 건수만 보면
  // 카테고리 크기 차이가 아니라 표본 크기 차이를 보는 꼴이 된다.
  //
  // 분모는 totals.foreign.findings/us.findings(RPC 응답 상단 집계값)가 아니라
  // by_category 행 자체의 합으로 고정한다 — 화면에 그려지는 막대·배지·% 표시가 전부
  // "이 표 안에서" 서로 정합하도록 하기 위해서다(응답 상단 totals 와 by_category 합이
  // 어긋나는 경우가 생겨도 화면 안에서는 항상 100%가 맞아떨어진다).
  function safeShare(part, whole) {
    return whole > 0 ? (part || 0) / whole : 0;
  }

  // [해외 상대비 배지] 이 배수 미만이면 배지를 달지 않는다 — 1.5배 미만은 "약간 더
  // 많다" 수준이라 배지로 못박아 강조할 근거가 약하다.
  var ZONE_BADGE_RATIO = 1.5;
  // [표본 하한] 해외 쪽 표본(foreign_cnt)이 이 미만인 카테고리는 배지를 달지 않는다 —
  // 해외 전체가 905건뿐인 얇은 모집단이라, 카테고리당 건수가 한 자릿수~십여 건이면
  // 한두 건의 증감만으로 점유율이 몇 %p 씩 흔들리고 배수(N배)는 그보다 더 크게
  // 요동친다(예: foreign_cnt 가 5→7로 늘어도 배수가 40% 뛴다 — 표본이 얇을수록 분산이
  // 커지는 일반 통계 성질). 미국 쪽은 표본이 7,288건으로 압도적으로 커 같은 문제가
  // 생기지 않으므로 하한을 따로 두지 않는다.
  var ZONE_MIN_FOREIGN_SAMPLE = 20;

  function buildZoneBarLine(tag, share, maxShare, pctStr, barClass) {
    var line = document.createElement("div");
    line.className = "tr-zone-barline";
    line.appendChild(el("span", "tr-zone-tag", tag));
    var track = document.createElement("div");
    track.className = "tr-zone-track";
    var bar = document.createElement("div");
    bar.className = "tr-zone-bar " + barClass;
    var ratio = maxShare > 0 ? share / maxShare : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, ratio) + ")";
    track.appendChild(bar);
    line.appendChild(track);
    line.appendChild(el("span", "tr-zone-pct", pctStr));
    return line;
  }

  function buildZoneRow(r, maxShare) {
    var row = document.createElement("div");
    row.className = "tr-zone-row";
    var head = document.createElement("div");
    head.className = "tr-zone-head";
    head.appendChild(el("span", "tr-zone-label", r.ko));
    // isFinite 가드 — usShare 가 0(그 카테고리가 미국 쪽엔 아예 없음)이면 배수가
    // 무한대가 되어 "N배" 형식으로 표기할 수 없다(실데이터 20개 카테고리에선 발생하지
    // 않지만, 방어적으로 배지를 생략한다 — 억지 표기 금지).
    if (isFinite(r.ratio) && r.ratio >= ZONE_BADGE_RATIO && r.foreignCnt >= ZONE_MIN_FOREIGN_SAMPLE) {
      head.appendChild(el("span", "tr-zone-badge", "해외 " + (Math.round(r.ratio * 10) / 10) + "배"));
    }
    row.appendChild(head);
    var bars = document.createElement("div");
    bars.className = "tr-zone-bars";
    bars.appendChild(buildZoneBarLine("해외", r.foreignShare, maxShare, r.foreignPctText, "tr-zone-bar-foreign"));
    bars.appendChild(buildZoneBarLine("미국", r.usShare, maxShare, r.usPctText, "tr-zone-bar-us"));
    row.appendChild(bars);
    return row;
  }

  // data 는 findings_zone_category() 응답 verbatim. 구버전 캐시 셸(zoneBlockEl/zoneEl
  // 없음)·빈 응답·0 분모는 전부 조용히 숨김 유지로 처리한다(패널을 명시적으로
  // hidden=true 로 되돌리지 않는다 — 정적 셸 기본값을 그대로 두는 편이 안전하다).
  // 해석·권고 문구는 만들지 않는다 — 관측된 분포만 기술한다(트랙C 품질 기준,
  // "한국 공장은 ~해야" 류 금지).
  function renderZonePanel(data) {
    if (!zoneBlockEl || !zoneEl) return;
    var d = data || {};
    var totals = d.totals || {};
    var foreign = totals.foreign || {};
    var us = totals.us || {};
    var cats = d.by_category || [];
    if (!cats.length) return;   // 빈 응답 → 패널 숨김 유지

    var foreignTotal = cats.reduce(function (s, c) { return s + (c.foreign_cnt || 0); }, 0);
    var usTotal = cats.reduce(function (s, c) { return s + (c.us_cnt || 0); }, 0);
    if (!(foreignTotal > 0) || !(usTotal > 0)) return;   // 0 나눗셈 방어 — 어느 zone 합계든 0 이면 비교 불성립

    var rows = cats.map(function (c) {
      var label = CATEGORY_LABELS[c.category_code];
      var fCnt = c.foreign_cnt || 0, uCnt = c.us_cnt || 0;
      var fShare = safeShare(fCnt, foreignTotal);
      var uShare = safeShare(uCnt, usTotal);
      return {
        code: c.category_code,
        ko: label ? label.ko : c.category_code,
        foreignCnt: fCnt,
        usCnt: uCnt,
        foreignShare: fShare,
        usShare: uShare,
        foreignPctText: pctText(fCnt, foreignTotal),
        usPctText: pctText(uCnt, usTotal),
        ratio: uShare > 0 ? (fShare / uShare) : (fShare > 0 ? Infinity : 0),
      };
    }).sort(function (a, b) { return b.foreignShare - a.foreignShare; });   // 해외 점유율 내림차순

    if (zoneSubEl) {
      // [부제 필수] 숫자는 전부 이 응답(totals/scope)에서 뽑는다 — 하드코딩 금지.
      // scope.excluded_unknown_country 는 0 이거나 없을 수 있어 방어적으로만 덧붙인다.
      var scope = d.scope || {};
      var sub = "FDA 483 기준 · 해외 " + fmtNum(foreign.findings) + "건(" +
        fmtNum(foreign.documents) + "개 문서·" + fmtNum(foreign.countries) + "개국) vs 미국 내 " +
        fmtNum(us.findings) + "건(" + fmtNum(us.documents) + "개 문서)";
      if (typeof scope.excluded_unknown_country === "number" && scope.excluded_unknown_country > 0) {
        sub += " · 소재국 미상 " + fmtNum(scope.excluded_unknown_country) + "건 제외";
      }
      zoneSubEl.textContent = sub;
    }

    zoneEl.innerHTML = "";
    // 막대 길이는 두 zone 을 합친 공통 스케일(maxShare)로 정규화한다 — zone 마다 따로
    // 정규화하면(자기 zone 내 최댓값 기준) 한 행 안에서 두 막대 길이를 비교해도
    // "어느 zone 에 더 몰렸는가"를 읽을 수 없다(각기 다른 잣대이므로).
    var maxShare = rows.reduce(function (m, r) { return Math.max(m, r.foreignShare, r.usShare); }, 0) || 1;
    rows.forEach(function (r) { zoneEl.appendChild(buildZoneRow(r, maxShare)); });

    if (zoneCountriesEl) {
      zoneCountriesEl.innerHTML = "";
      // [해외 국가 구성] "해외"는 균질한 덩어리가 아니다(인도가 압도적) — 이걸 안 보여
      // 주면 독자가 "해외 = 한국"으로 오독한다. top_countries 상위 5개만 짧게 표기.
      var top = (d.top_countries || []).slice(0, 5);
      if (top.length) {
        zoneCountriesEl.appendChild(document.createTextNode("해외 실사 구성: "));
        top.forEach(function (c, i) {
          if (i > 0) zoneCountriesEl.appendChild(document.createTextNode(" · "));
          zoneCountriesEl.appendChild(
            document.createTextNode(countryLabelKo(c.code, c.country) + " " + fmtNum(c.findings))
          );
        });
      }
    }

    // [컨셉 재정의] 이 패널은 '데이터 현황' 면의 독립 섹션으로 돌아갔다(지적 경향
    // 면에서는 뺐다 — "해외"가 인도 61%인 덩어리라 국내 사용자가 "해외=우리"로 읽기
    // 쉽고, 보고 끝나는 블록이라 규율 3 을 못 지킨다). 그래서 자기를 직접 편다.
    zoneBlockEl.hidden = false;
  }

  // ── [FDA 의약품 GMP 실사 등급] 058_fda_inspections.sql fda_inspection_stats() ──────
  // findings 계열 RPC(findings_stats/findings_category_matrix/findings_zone_category/
  // findings_recent_window)와 완전히 독립된 별도 소스다. findings 는 문서에서 뽑은
  // "지적사항"(문장) 단위고, 이 RPC 는 FDA Data Dashboard API 실사 "건"(등급 하나)
  // 단위라 서로 다른 잣대다 — 그래서 이 섹션은 findings 총계를 나누는 분모로 계산해
  // 쓰지 않는다(임무서: "findings 는 분자만 말한다"의 답은 findings 수치 옆에 이
  // 모집단의 크기·구성을 나란히 보여주는 것이지, 두 수치를 나누는 것이 아니다 — 실사
  // 한 건에서 지적이 여러 개 나올 수 있어 나누면 의미가 없다).
  //
  // ★scope 는 하드코딩하지 않고 RPC 응답을 그대로 읽어 화면에 적는다(054 "축을 바꾸지
  // 말고 밝혀라") — 이 표는 ProjectArea='Drug Quality Assurance'(GMP 제조소 실사)
  // 단일 모집단만 담고, 성격이 다른 Bioresearch Monitoring 등은 애초에 표에 없다
  // (058 마이그레이션 헤더 실측: 합치면 OAI 비율이 14.9%→8.6%로 무의미해진다).
  //
  // ★비율은 서버가 안 준다(007/038 계약과 동일 — 서버는 센다, 나누기는 클라이언트가
  // 한다) — OAI 비율/연도별 등급 구성/국가별 OAI 비중 전부 여기서 계산한다.
  //
  // ★데이터가 아직 없을 때(058 미적용 라이브·0건)는 섹션을 그리지 않는다(빈 껍데기
  // 금지) — totals.inspections 가 0 이하면 조용히 return 하고 정적 셸의 기본값인
  // hidden 상태를 그대로 둔다.
  var FDA_GRADE_LABELS = { nai: "NAI(적합)", vai: "VAI(경미)", oai: "OAI(중대)" };

  function buildFdaYearRow(y) {
    var nai = y.nai || 0, vai = y.vai || 0, oai = y.oai || 0;
    var total = nai + vai + oai;
    var row = el("div", "tr-fda-y-row");
    row.appendChild(el("span", "tr-fda-y-label", "FY" + y.fiscal_year));
    var track = document.createElement("div");
    track.className = "tr-fda-y-track";
    ["nai", "vai", "oai"].forEach(function (k) {
      var seg = document.createElement("div");
      seg.className = "tr-fda-y-seg " + k;
      var cnt = k === "nai" ? nai : k === "vai" ? vai : oai;
      var share = total > 0 ? cnt / total : 0;
      seg.style.flex = "0 0 " + (share * 100) + "%";
      seg.title = "FY" + y.fiscal_year + " " + FDA_GRADE_LABELS[k] + " " + fmtNum(cnt) + "건";
      track.appendChild(seg);
    });
    row.appendChild(track);
    row.appendChild(el("span", "tr-fda-y-oai-pct", "OAI " + pctText(oai, total)));
    row.appendChild(el("span", "tr-fda-y-total", fmtNum(total) + "건"));
    return row;
  }

  // 상위 몇 개국을 그릴지 — findings_zone_category 의 top_countries.slice(0,5) 보다
  // 넉넉히 둔다(이 RPC 는 by_country 를 절단 없이 전량 반환하므로, 화면 표시 상한은
  // trends.js 소관이고 RPC 잘림이 아니다 — 058 헤더 참조).
  var FDA_COUNTRY_ROWS = 12;

  function buildFdaCountryRow(c, isKorea) {
    var row = el("div", "tr-fda-c-row" + (isKorea ? " is-kr" : ""));
    var label = c.code ? countryLabelKo(c.code, c.country) : "국가명 미확인";
    row.appendChild(el("span", "tr-fda-c-label", label));
    var track = document.createElement("div");
    track.className = "tr-fda-c-track";
    var bar = document.createElement("div");
    bar.className = "tr-fda-c-bar";
    var oaiShare = c.total > 0 ? (c.oai || 0) / c.total : 0;
    bar.style.transform = "scaleX(" + Math.max(0.02, oaiShare) + ")";
    track.appendChild(bar);
    row.appendChild(track);
    row.appendChild(el("span", "tr-fda-c-count", fmtNum(c.total) + "건"));
    row.appendChild(el("span", "tr-fda-c-oai", "OAI " + fmtNum(c.oai) + "건(" + pctText(c.oai, c.total) + ")"));
    return row;
  }

  // data 는 fda_inspection_stats() 응답 verbatim. 구버전 캐시 셸(엘리먼트 없음)·0건·
  // fetch 실패는 전부 조용히 숨김 유지로 처리한다(zoneBlockEl 과 동일 원칙 — 패널을
  // 명시적으로 hidden=true 로 되돌리지 않는다, 정적 셸 기본값을 그대로 두는 편이 안전).
  function renderFdaInspections(data) {
    if (!fdaBlockEl || !fdaStatsEl || !fdaYearEl || !fdaCountryEl) return;
    var d = data || {};
    var totals = d.totals || {};
    var total = totals.inspections || 0;
    if (!(total > 0)) return;   // 미적용/빈 응답 → 빈 껍데기를 그리지 않는다

    var scope = d.scope || {};
    if (fdaScopeEl) {
      var scopeText = (scope.project_area || "GMP 실사") + " 한정";
      var excluded = scope.excluded_project_areas || [];
      if (excluded.length) scopeText += " · " + excluded.join("·") + " 등 다른 모집단 제외";
      if (typeof scope.fiscal_year_min === "number" && typeof scope.fiscal_year_max === "number") {
        scopeText += " · FY" + scope.fiscal_year_min + "~FY" + scope.fiscal_year_max;
      }
      scopeText += " · 출처: " + (scope.source || "FDA Data Dashboard API");
      fdaScopeEl.textContent = scopeText;
    }

    // [기준일] 059 가 scope 에 더한 신선도 2키. **두 날짜는 뜻이 다르다** —
    //   · last_ingested_date_kst    = 우리가 새 실사를 마지막으로 받아온 날(우리 쪽 시각)
    //   · latest_inspection_end_date = 표에 담긴 실사 중 가장 최근 종료일(FDA 쪽 날짜)
    // 둘을 한 문장으로 뭉치면 "2026-07-16까지 최신"처럼 읽혀 오도한다. 라벨을 각각 붙여
    // 무엇을 재는 날짜인지 문장 안에서 갈라 적는다.
    //
    // ★없는 날짜를 지어내지 않는다. 위 project_area/source 처럼 `|| "..."` 폴백을 쓰면
    // 059 미적용 라이브·구버전 캐시에서 **최신인 척하는 거짓 날짜**가 화면에 나간다.
    // fiscal_year_min/max 와 같은 방식(값의 타입을 확인하고, 없으면 그 항목을 통째로
    // 생략)만 쓴다. 둘 다 없으면 문단이 빈 채로 남고(높이 0) 위아래 여백만 유지된다.
    if (fdaAsOfEl) {
      var asOf = [];
      if (typeof scope.last_ingested_date_kst === "string" && scope.last_ingested_date_kst) {
        asOf.push("새 실사를 마지막으로 받아온 날 " + scope.last_ingested_date_kst);
      }
      if (typeof scope.latest_inspection_end_date === "string" && scope.latest_inspection_end_date) {
        asOf.push("담긴 실사 중 가장 최근 종료일 " + scope.latest_inspection_end_date);
      }
      fdaAsOfEl.textContent = asOf.length ? "숫자 기준일 — " + asOf.join(" · ") : "";
    }

    fdaStatsEl.innerHTML = "";
    fdaStatsEl.appendChild(buildStat(fmtNum(total), "FDA 의약품 GMP 실사"));
    fdaStatsEl.appendChild(buildStat(fmtNum(totals.oai) + "건(" + pctText(totals.oai, total) + ")", "중대 지적 OAI"));
    fdaStatsEl.appendChild(buildStat(fmtNum(totals.vai) + "건(" + pctText(totals.vai, total) + ")", "경미 지적 VAI"));
    fdaStatsEl.appendChild(buildStat(fmtNum(totals.nai) + "건(" + pctText(totals.nai, total) + ")", "적합 NAI"));

    fdaYearEl.innerHTML = "";
    (d.by_year || []).forEach(function (y) { fdaYearEl.appendChild(buildFdaYearRow(y)); });

    fdaCountryEl.innerHTML = "";
    var countries = d.by_country || [];
    var top = countries.slice(0, FDA_COUNTRY_ROWS);
    var korea = null;
    for (var i = 0; i < countries.length; i++) {
      if (countries[i].code === "KR") { korea = countries[i]; break; }
    }
    var koreaInTop = korea && top.indexOf(korea) !== -1;
    top.forEach(function (c) { fdaCountryEl.appendChild(buildFdaCountryRow(c, c === korea)); });
    // [한국 강조] 상위 목록 밖에 있어도 한국은 항상 보이게 별도로 덧붙인다(임무서
    // "한국을 눈에 띄게" 요구 — top N 절단으로 한국이 가려지면 안 된다).
    if (korea && !koreaInTop) fdaCountryEl.appendChild(buildFdaCountryRow(korea, true));

    if (fdaNoteEl) {
      var shownTotal = top.reduce(function (s, c) { return s + (c.total || 0); }, 0);
      var note = "국가 " + countries.length + "종 중 건수 상위 " + top.length +
        "곳(전체의 " + pctText(shownTotal, total) + ")만 표시" +
        (korea && !koreaInTop ? " · 한국은 목록 밖이라 따로 덧붙임" : "") + ".";
      if (typeof scope.unmapped_country_count === "number" && scope.unmapped_country_count > 0) {
        note += " 국가명 미확인 " + fmtNum(scope.unmapped_country_count) + "건 포함.";
      }
      fdaNoteEl.textContent = note;
    }

    fdaBlockEl.hidden = false;
  }

  // ── [062] 실사일 기준 분기 추이 ─────────────────────────────────────────────
  // ★이 섹션이 재는 날짜는 위 by_year 와 **다르다**(회계연도 vs 실사 종료일). 같은
  //   데이터인데 FY 축에서는 OAI 비율이 크게 출렁이는 것처럼 보이고 실사일 분기로
  //   다시 재면 최근 2년이 안정이다 — 그래서 **비율을 주 축으로** 그린다.
  //
  // ★미완 분기 판정을 여기서 하는 이유: 서버(062)는 세기만 한다(임계는 반드시 낡는다는
  //   007/038/058/059 공통 계약). 대신 059 가 이미 주는 scope.latest_inspection_end_date
  //   = **데이터의 전선**에서 파생한다 — 전선보다 한 분기 이내에 끝난 분기는 FDA 등급
  //   확정·공개가 아직 진행 중이라 낮게 보인다. 상수(예: "마지막 2개")를 박지 않는
  //   이유가 이것이다. 데이터가 앞으로 나아가면 판정도 저절로 따라간다.
  var FQ_ROWS = 12;                 // 최근 3년치. 28개 전부 그리면 훑을 수 없다.
  var FQ_SCALE_MAX = 0.30;          // 막대 정규화 상한(OAI 30%) — 실측 최댓값 22% 여유.

  function fqIsPartial(row, frontier) {
    if (!frontier || !row.quarter_end) return false;
    // 분기 종료일이 (전선 − 3개월)보다 뒤면 아직 채워지는 중으로 본다.
    var f = new Date(frontier + "T00:00:00Z");
    if (isNaN(f.getTime())) return false;
    f.setUTCMonth(f.getUTCMonth() - 3);
    return new Date(row.quarter_end + "T00:00:00Z") > f;
  }

  function buildFqRow(row, frontier) {
    var total = row.total || 0;
    var oai = row.oai || 0;
    var partial = fqIsPartial(row, frontier);
    var el_ = el("div", "tr-fq-row" + (partial ? " is-partial" : ""));
    el_.appendChild(el("span", "tr-fq-label", row.quarter || ""));
    var track = el("div", "tr-fq-track");
    var bar = el("div", "tr-fq-bar");
    var share = total > 0 ? oai / total : 0;
    bar.style.width = Math.min(100, (share / FQ_SCALE_MAX) * 100) + "%";
    track.appendChild(bar);
    el_.appendChild(track);
    el_.appendChild(el("span", "tr-fq-pct", pctText(oai, total)));
    el_.appendChild(el("span", "tr-fq-total", fmtNum(total) + "건"));
    el_.appendChild(el("span", "tr-fq-tag", partial ? "채워지는 중" : ""));
    el_.title = row.quarter + " · 실사 " + fmtNum(total) + "건 · 중대 지적 " +
      fmtNum(oai) + "건 · 지적서 공개 " + fmtNum(row.citations_posted) + "건";
    return el_;
  }

  function renderFdaQuarters(data) {
    if (!fqBlockEl || !fqEl) return;
    var rows = (data && data.by_quarter) || [];
    if (!rows.length) return;                       // 062 미적용 라이브 → 숨김 유지
    var frontier = ((data.scope || {}).latest_inspection_end_date) || "";
    var shown = rows.slice(-FQ_ROWS);
    fqEl.innerHTML = "";
    shown.forEach(function (r) { fqEl.appendChild(buildFqRow(r, frontier)); });
    if (fqNoteEl) {
      // 완결 분기만으로 범위를 적는다 — 미완 분기를 섞어 "최근 N%~M%" 라고 쓰면 그
      // 폭이 공개 지연 때문에 벌어진 것을 규제 변화로 읽게 만든다.
      var solid = shown.filter(function (r) {
        return !fqIsPartial(r, frontier) && (r.total || 0) > 0;
      });
      var note = "막대는 그 분기 실사 중 중대 지적(OAI) 비율입니다(가로 축 최대 " +
        Math.round(FQ_SCALE_MAX * 100) + "%).";
      if (solid.length >= 2) {
        var pcts = solid.map(function (r) { return (r.oai || 0) / r.total; });
        var lo = Math.min.apply(null, pcts), hi = Math.max.apply(null, pcts);
        note += " 표시된 분기 중 자료가 다 찬 " + solid.length + "개 분기는 " +
          (lo * 100).toFixed(1) + "%~" + (hi * 100).toFixed(1) + "% 사이입니다.";
      }
      if (frontier) {
        note += " ‘채워지는 중’으로 표시한 분기는 FDA 의 등급 확정·공개가 아직 끝나지 " +
          "않아 낮게 보입니다 — 담긴 실사 중 가장 최근 종료일은 " + frontier + "입니다.";
      }
      fqNoteEl.textContent = note;
    }
    fqBlockEl.hidden = false;
  }

  // ── [062] 한국 소재 제조소 ──────────────────────────────────────────────────
  // 연도별 등급 구성은 위 buildFdaYearRow 와 **같은 모양**이라 그 컴포넌트를 그대로
  // 쓴다(라벨만 FY → 달력연도). 신규 CSS 0.
  function buildKrYearRow(y) {
    var nai = y.nai || 0, vai = y.vai || 0, oai = y.oai || 0;
    var total = nai + vai + oai;
    var row = el("div", "tr-fda-y-row");
    row.appendChild(el("span", "tr-fda-y-label", String(y.year) + "년"));
    var track = el("div", "tr-fda-y-track");
    ["nai", "vai", "oai"].forEach(function (k) {
      var seg = el("div", "tr-fda-y-seg " + k);
      var cnt = k === "nai" ? nai : k === "vai" ? vai : oai;
      seg.style.flex = "0 0 " + (total > 0 ? (cnt / total) * 100 : 0) + "%";
      seg.title = y.year + "년 " + FDA_GRADE_LABELS[k] + " " + fmtNum(cnt) + "건";
      track.appendChild(seg);
    });
    row.appendChild(track);
    row.appendChild(el("span", "tr-fda-y-oai-pct", "OAI " + fmtNum(oai) + "건"));
    row.appendChild(el("span", "tr-fda-y-total", fmtNum(total) + "건"));
    return row;
  }

  function renderKorea(data) {
    if (!krBlockEl || !krYearEl) return;
    var kr = (data && data.korea) || null;
    if (!kr) return;                                // 062 미적용 라이브 → 숨김 유지
    var t = kr.totals || {};
    var years = kr.by_year || [];
    if (!(Number(t.inspections) > 0) || !years.length) return;
    if (krSubEl) {
      krSubEl.textContent = "누적 실사 " + fmtNum(t.inspections) + "건 · 사업장 " +
        fmtNum(t.firms) + "곳 · 중대 지적 " + fmtNum(t.oai) + "건(" +
        pctText(t.oai, t.inspections) + ")";
    }
    krYearEl.innerHTML = "";
    years.forEach(function (y) { krYearEl.appendChild(buildKrYearRow(y)); });
    if (krNoteEl) {
      // ★표본이 작다는 사실을 화면이 먼저 말한다 — 연 2~22건에서 비율을 앞세우면
      //   한두 건 차이가 큰 변화로 읽힌다(히트맵의 표본 하한 관례와 같은 취지).
      var maxYear = years.reduce(function (m, y) { return Math.max(m, y.total || 0); }, 0);
      var note = "연도별 실사 수가 " + fmtNum(maxYear) + "건 이하라 비율보다 건수로 보셔야 합니다 — " +
        "한두 건 차이가 비율로는 크게 흔들립니다.";
      // 같은 해에 두 번 실사받은 사업장이 있었는지는 세어서 그대로 말한다(해석 없이).
      var repeated = years.filter(function (y) { return (y.total || 0) > (y.firms || 0); });
      note += repeated.length
        ? " 같은 해에 두 번 이상 실사받은 사업장이 있는 해: " +
          repeated.map(function (y) { return y.year + "년"; }).join(" · ") + "."
        : " 표시된 모든 해에서 실사 수와 사업장 수가 같습니다 — 같은 해에 두 번 실사받은 사업장은 없었습니다.";
      // ★위 부제의 "누적 실사 N건 · 사업장 M곳"에서 N > M 이면 **해를 건너뛴 재실사**가
      //   있었다는 뜻이다. 이 다리를 놓지 않으면 바로 위 문장("같은 해에 두 번은 없었다")과
      //   부제가 서로 어긋나 보인다 — 두 수치가 다른 것을 세고 있다는 사실을 화면이
      //   말해야 한다(계기판 합산 오진을 문장 층에서 되풀이하지 않는다).
      var revisited = Number(t.inspections || 0) - Number(t.firms || 0);
      if (revisited > 0) {
        note += " 다만 누적으로는 실사 " + fmtNum(t.inspections) + "건이 사업장 " +
          fmtNum(t.firms) + "곳에서 나왔습니다 — " + fmtNum(revisited) +
          "건은 앞서 실사받았던 곳을 다른 해에 다시 실사한 것입니다.";
      }
      krNoteEl.textContent = note;
    }
    krBlockEl.hidden = false;
  }

  // ── [최근 12개월] 041_findings_recent_window ─────────────────────────────
  // findings_stats/findings_category_matrix/findings_zone_category 와 독립된 별개 RPC.
  // 실패해도(041 미배포 라이브 포함) 이 섹션만 조용히 숨겨진 채로 남고 다른 섹션엔 전혀
  // 영향이 없다(히트맵·zone 과 동일 원칙 — § 오케스트레이션 하단 fetchRecentWindow 참조).
  //
  // ★이 페이지에서 유일하게 **시간에 따른 변화**를 말할 수 있는 자리다. 그래서 아래 세
  //   규칙을 지킨다:
  //   (1) 비교 단위는 **구성비(그 창 전체 지적 중 이 영역의 비율)** 다. 각 창에서 합이
  //       정확히 100%가 되므로 늘어난 만큼 어딘가는 줄고, 두 창을 견주는 것이 성립한다.
  //       ※ 처음엔 "문서 등장률"(그 창 문서 중 이 영역이 지적된 문서 비율)로 만들었다가
  //         되돌렸다 — 등장률은 합이 100%로 고정되지 않아서, 창마다 **문서당 지적 수**가
  //         달라지면(직전 3.9건/문서 → 최근 2.7건/문서) 전 영역이 한 방향으로 쏠린다.
  //         실측에서 무균보증이 문서 119→136건으로 **늘었는데도** 등장률만 33%→20%로
  //         떨어져 "줄었다"로 표시됐다. 분모의 성격이 변하는 지표는 추세를 못 잰다.
  //   (2) 구성비 변화는 **건수 증감이 아니다**. 위 무균보증은 구성비가 22%→17%로 내려가도
  //       건수는 312→309건으로 거의 그대로다 — 다른 영역이 늘어 비중이 밀린 것이다.
  //       그래서 각 행에 건수를 함께 적고, 표제도 "줄어든"이 아니라 "비중이 줄어든"이다.
  //   (3) 최대 교란 요인인 **소스 구성 변화**를 같은 응답(by_source 의 cur/prev)으로 화면에
  //       함께 적는다 — 한쪽 창에만 새 소스가 들어와 있으면 카테고리 구성이 달라진 게
  //       아니라 모집단이 달라진 것이다. 해석·권고 문구는 만들지 않는다(트랙C 품질 기준).

  function monthLabelKo(ym) {
    var s = String(ym || "");
    if (s.length < 7) return s;
    return s.slice(0, 4) + "년 " + String(Number(s.slice(5, 7))) + "월";
  }

  function shareOf(part, whole) {
    return whole > 0 ? (part || 0) / whole : 0;
  }

  // 소수 1자리 고정 %p 표기(+/− 부호 포함). pctText 와 달리 **차이**를 적는 자리라
  // 부호가 정보의 절반이다.
  function ppText(pp) {
    var v = Math.round(pp * 10) / 10;
    return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v) + "%p";
  }

  function buildRecentCatRow(entry, idx, maxCnt, curFindings) {
    var row = el("div", "tr-rc-row" + (state.openCat === entry.code ? " on" : ""));
    makeClickableRow(row, entry.ko + " 실제 지적 사례 보기", function () {
      if (state.openCat === entry.code) closeRecentCat();
      else openRecentCat(entry.code, entry.ko);
    });
    row.appendChild(el("span", "tr-rc-rank", String(idx + 1)));
    row.appendChild(el("span", "tr-rc-label", entry.ko));
    var track = el("div", "tr-rc-track");
    var bar = el("div", "tr-rc-bar");
    bar.style.transform = "scaleX(" + Math.max(0.02, maxCnt > 0 ? entry.cnt / maxCnt : 0) + ")";
    track.appendChild(bar);
    row.appendChild(track);
    row.appendChild(el("span", "tr-rc-docs",
      fmtNum(entry.cnt) + "건 · " + pctText(entry.cnt, curFindings)));
    row.appendChild(el("span", "tr-rc-caret", state.openCat === entry.code ? "▲" : "▼"));
    row.title = entry.ko + " · 최근 12개월 지적 " + fmtNum(entry.cnt) + "건 · 문서 " +
      fmtNum(entry.docs) + "건";
    return row;
  }

  function renderRecentCats() {
    if (!recentCatsEl) return;
    recentCatsEl.innerHTML = "";
    var rows = state.recentCats || [];
    if (!rows.length) return;
    var maxCnt = rows[0].cnt || 1;
    var curFindings = state.recentCurFindings || 0;
    rows.forEach(function (entry, i) {
      recentCatsEl.appendChild(buildRecentCatRow(entry, i, maxCnt, curFindings));
      if (state.openCat === entry.code) {
        recentCatsEl.appendChild(state.exampleNode || el("p", "tr-empty", "불러오는 중…"));
      }
    });
  }

  // [컨셉 재정의] 이 함수는 이제 **응답을 보관하고 기관 선택을 적용**하기만 한다.
  // 월별 막대(24개)는 삭제했다 — 그 막대가 세는 것은 "그 달에 공개된 문서 수"이고,
  // 그건 FDA·식약처의 공개 행정 리듬이지 규제 신호가 아니다. 우리도 알고 있었다:
  // "마지막 막대는 아직 진행 중인 달이라 낮게 보입니다"라는 주석이 그 사실을 인정하는
  // 문장이었다. 보고 나서 할 일이 없는 블록은 싣지 않는다(규율 3).
  function renderRecentWindow(data) {
    if (!rankBlockEl) return;              // 이 면엔 순위 섹션이 없다(조용히 no-op)
    var d = data || {};
    var cur = (d.totals || {}).cur || {};
    if (!(Number(cur.findings) > 0)) return;   // 빈 응답 → 숨김 유지
    state.recentData = d;
    if (agencyEl && (d.by_category_source || []).length) agencyEl.hidden = false;
    applyAgency();
  }


  // ── [달라진 점] 최근 창 vs 직전 창 구성비 차이 ─────────────────────────────
  // 각 행은 구성비 변화(%p)와 **건수**를 함께 적는다 — 둘을 같이 보여 주지 않으면
  // "비중이 줄었다"가 "건수가 줄었다"로 읽힌다(실측: 무균보증 312→309건인데 구성비는
  // 22%→17%). 문서 수는 툴팁에 둔다.
  function buildMoverRow(r, isUp) {
    var row = el("div", "tr-mv-row " + (isUp ? "tr-mv-up" : "tr-mv-down"));
    var label = el("span", "tr-mv-label", r.ko);
    label.appendChild(el("span", "tr-mv-cnt",
      "지적 " + fmtNum(r.prevCnt) + " → " + fmtNum(r.curCnt) + "건"));
    row.appendChild(label);
    row.appendChild(el("span", "tr-mv-shift", r.prevPct + " → " + r.curPct));
    row.appendChild(el("span", "tr-mv-badge", ppText(r.deltaPp)));
    row.title = r.ko + " · 문서 " + fmtNum(r.prevDocs) + "건 → " + fmtNum(r.curDocs) + "건";
    return row;
  }

  function fillMoverList(listEl, rows, isUp) {
    if (!listEl) return;
    listEl.innerHTML = "";
    if (!rows.length) {
      listEl.appendChild(el("p", "tr-empty", "기준(" + MOVER_MIN_PP + "%p 이상)을 넘는 변화가 없습니다."));
      return;
    }
    rows.forEach(function (r) { listEl.appendChild(buildMoverRow(r, isUp)); });
  }

  // ── [레인] 비교 단위는 기관이 아니라 **수집 채널**이다 ──────────────────────
  // 식약처는 한 기관이지만 공개 채널 셋을 운영하고(행정처분 API·회수 API·nedrug 게시판)
  // 채널마다 공개 이력 길이와 마스킹 정책이 다르다. 한 덩어리로 세면 극단적으로 다른
  // 것들이 서로를 가린다 — 실측 증가율 회수 1.22 · GMP실사 3.69 · 행정처분 67.0 인데
  // 합치면 2.45(점유율 배율 1.57 = "정상")로 보인다.
  // ★화면에는 내부 키가 아니라 사람이 읽는 이름을 쓴다.
  var LANE_LABELS = {
    "MFDS/admin-action": "MFDS 행정처분",
    "MFDS/gmp-inspection": "MFDS GMP 실사",
    "MFDS/recall-quality": "MFDS 회수",
  };
  function laneLabel(lane) { return LANE_LABELS[lane] || lane; }

  // 교차표에서 레인 단위 창 합계를 직접 만든다 — by_source 는 소스 축이라 식약처 3채널이
  // 한 덩어리로 합쳐져 있어 여기에 게이트를 걸 수 없다. 교차표는 카테고리 전수를 담으므로
  // (모든 finding 이 category_code 를 갖는다) 카테고리로 합치면 그 레인의 창 합계가 된다.
  function laneTotals(grid) {
    var t = {};
    (grid || []).forEach(function (r) {
      var k = r.lane || r.source;   // 053 미적용 응답은 lane 이 없다 → 소스 축으로 폴백
      var e = t[k] || (t[k] = { lane: k, cur: 0, prev: 0 });
      e.cur += Number(r.cur_cnt) || 0;
      e.prev += Number(r.prev_cnt) || 0;
    });
    return Object.keys(t).map(function (k) { return t[k]; });
  }

  // 053 by_category_source 를 남긴 레인으로만 접어 by_category 모양으로 되돌린다.
  // 문서 수(docs)도 함께 접는다 — raw_signal 은 소스 하나에만 속하므로 소스별 문서 수의
  // 합이 그 카테고리의 문서 수와 정확히 같다(실측 20/20 카테고리, 두 창 모두 불일치 0).
  // 건수만 좁히고 문서 수를 그대로 두면 행의 툴팁("문서 N건 → M건")이 본문과 어긋난다.
  function foldCategorySource(grid, kept) {
    var byCode = {};
    (grid || []).forEach(function (r) {
      if (!kept[r.lane || r.source]) return;
      var e = byCode[r.category_code];
      if (!e) {
        e = byCode[r.category_code] = {
          category_code: r.category_code,
          cur_cnt: 0, prev_cnt: 0, cur_docs: 0, prev_docs: 0,
        };
      }
      e.cur_cnt += Number(r.cur_cnt) || 0;
      e.prev_cnt += Number(r.prev_cnt) || 0;
      e.cur_docs += Number(r.cur_docs) || 0;
      e.prev_docs += Number(r.prev_docs) || 0;
    });
    return Object.keys(byCode).map(function (k) { return byCode[k]; });
  }

  // 두 창에서 견줄 수 있는 소스만 남긴 비교 모집단을 만든다.
  //   · 052 by_category_source 가 없는 응답(041 만 배포된 라이브·구버전 캐시)에서는
  //     조정하지 않고 by_category 를 그대로 돌려준다 — 신·구 어느 응답에서도 이 패널이
  //     깨지면 안 된다.
  //   · 뺄 소스가 없으면 역시 조정하지 않는다(접기 결과가 by_category 와 같다).
  //   · ★남는 소스가 하나도 없으면 `usable=false` 로 알린다. 그 상태로 조정 전 표를
  //     그대로 내면 **가장 못 믿을 상황에서 아무 고지 없이 유령 표가 나간다** — 이
  //     저장소가 반복해 겪은 "캐치올이 정상 응답처럼 보인다" 와 같은 형태다.
  // [컨셉 재정의] `scope` = 고른 기관의 레인 맵. 이 함수가 두 창의 소스 구성을 맞추는
  // 일은 그대로이되, **그 기관 안에서만** 한다. 기관을 골랐는데 정렬 대상이 전 기관이면
  // 조정 결과가 그 기관의 것이 아니게 된다.
  // ★scope 가 비었으면(기관 선택 불가 응답) 종전처럼 전 레인을 본다 — 후퇴 경로.
  function alignSourceMix(d, curFindings, prevFindings, scope) {
    var grid = d.by_category_source;
    var scoped = scope && Object.keys(scope).length ? scope : null;
    var raw = {
      applied: false, usable: true,
      // 후퇴 시 기준 표: 기관을 가를 수 있으면 그 기관으로 접은 표, 아니면 합산 by_category.
      cats: (scoped && grid && grid.length)
        ? foldCategorySource(grid, scoped)
        : (d.by_category || []),
      curFindings: curFindings, prevFindings: prevFindings,
      dropped: [],
    };
    if (!grid || !grid.length) return raw;

    var kept = {}, dropped = [], keptCur = 0, keptPrev = 0;
    laneTotals(grid).forEach(function (s) {
      if (scoped && !scoped[s.lane]) return;   // 다른 기관의 레인은 아예 보지 않는다
      var c = s.cur, p = s.prev;
      // 표본이 얇으면 배율을 따지는 것 자체가 무의미하므로 이 검사가 먼저다.
      // ★사유 문구는 실제로 성립한 조건과 같아야 한다 — 조건은 OR 이므로
      //   "두 기간 모두 적다"가 아니라 "한쪽이 적다"로 적는다(한쪽만 적어도 걸린다).
      if (c < MOVER_SOURCE_MIN || p < MOVER_SOURCE_MIN) {
        dropped.push({ source: laneLabel(s.lane), curCnt: c, prevCnt: p, reason: "thin" });
        return;
      }
      var ratio = shareOf(c, curFindings) / shareOf(p, prevFindings);
      // !(ratio <= MAX) 형태는 NaN·Infinity 를 전부 제외 쪽으로 떨어뜨린다.
      if (!(ratio <= MOVER_SOURCE_MAX_RATIO) || ratio < 1 / MOVER_SOURCE_MAX_RATIO) {
        dropped.push({ source: laneLabel(s.lane), curCnt: c, prevCnt: p, reason: "skew" });
        return;
      }
      kept[s.lane] = true;
      keptCur += c;
      keptPrev += p;
    });
    if (!dropped.length) return raw;

    var cats = foldCategorySource(grid, kept);
    if (!cats.length) {
      // 견줄 수 있는 소스가 하나도 없다 — 조정 전 표를 대신 내보내지 않는다.
      raw.usable = false;
      raw.dropped = dropped;
      return raw;
    }
    return {
      applied: true, usable: true,
      cats: cats,
      curFindings: keptCur, prevFindings: keptPrev,
      dropped: dropped,
    };
  }

  // 두 창의 소스 구성을 나란히 적는다 — 증감 비교의 최대 교란 요인이라 감추지 않는다.
  // 분모는 위 증감과 **같은 단위(지적 건수)** 로 맞춘다(문서 기준으로 적으면 독자가 두
  // 수치를 같은 잣대로 견줄 수 없다). 어느 쪽 창에서든 1% 이상인 소스만 적는다.
  // ★이 줄만은 **조정 전 총량** 기준이다 — 뺀 소스까지 포함한 전체 구성을 보여야 독자가
  //   무엇을 뺐는지 대조할 수 있다. 조정 총량으로 바꾸면 정직성 고지 자체가 사라진다.
  function renderMoverSourceLine(bySource, curFindings, prevFindings, droppedSources) {
    if (!moveSourceEl) return;
    var parts = (bySource || []).map(function (s) {
      return {
        source: s.source,
        cur: shareOf(s.cnt, curFindings),
        prev: shareOf(s.prev_cnt, prevFindings),
        curPct: pctText(s.cnt || 0, curFindings),
        prevPct: pctText(s.prev_cnt || 0, prevFindings),
      };
    }).filter(function (s) { return s.cur >= 0.01 || s.prev >= 0.01; })
      .sort(function (a, b) { return b.cur - a.cur; });
    var text = "";
    if (parts.length) {
      text = "두 기간의 소스 구성: " +
        parts.map(function (s) { return s.source + " " + s.prevPct + " → " + s.curPct; }).join(" · ") +
        ".";
    }
    // ★뺀 소스는 이름·건수·이유를 그대로 적는다 — 조용히 빼면 위 표가 전량 비교처럼 보인다.
    var out = (droppedSources || []);
    if (out.length) {
      text += (text ? " " : "") + "비교에서 뺀 소스: " + out.map(function (s) {
        return s.source + "(직전 " + fmtNum(s.prevCnt) + "건 → 최근 " + fmtNum(s.curCnt) + "건, " +
          (s.reason === "thin" ? "한쪽 기간이 " + MOVER_SOURCE_MIN + "건 미만"
                               : "두 기간 자료량 차이가 큼") + ")";
      }).join(" · ") + ". 두 기간에 걸쳐 견줄 수 있는 소스만 남겨 위 증감을 계산했습니다.";
    } else if (text) {
      text += " 소스 구성이 달라지면 위 증감도 함께 움직입니다.";
    }
    moveSourceEl.textContent = text;
  }

  // [컨셉 재정의] 두 번째 인자로 **고른 기관**을 받는다. 기관을 바꾸면 이 표도 그
  // 기관 안에서 다시 계산되어야 한다 — 순위는 식약처인데 증감은 합산이면 두 표가 서로
  // 다른 모집단을 말하게 되고, 그것이 이 재정의가 없애려는 바로 그 혼동이다.
  function renderMovers(data, view) {
    if (!moveBlockEl || !moveUpEl || !moveDownEl) return;
    moveBlockEl.hidden = true;             // 기관을 바꿀 때마다 다시 판정한다(잔상 금지)
    var d = data || {};
    var totals = d.totals || {};
    var v = view || agencyView(state.agency);
    var grid = d.by_category_source || [];
    // [컨셉 재정의] 창 합계도 **고른 기관 안에서** 센다. 전 기관 합계로 문턱을 넘겨
    // 놓고 표만 기관별로 그리면, 얇은 기관에서 한두 건짜리 변화가 크게 보인다.
    var scope = grid.length ? agencyKept(grid, v) : null;
    var curFindings = 0, prevFindings = 0;
    if (scope && Object.keys(scope).length) {
      laneTotals(grid).forEach(function (t) {
        if (!scope[t.lane]) return;
        curFindings += t.cur;
        prevFindings += t.prev;
      });
    } else {
      curFindings = Number((totals.cur || {}).findings) || 0;
      prevFindings = Number((totals.prev || {}).findings) || 0;
    }
    // 창이 얇으면 비교 자체를 하지 않는다 — 숨김 유지(억지 해석 금지).
    if (curFindings < WINDOW_MIN_FINDINGS || prevFindings < WINDOW_MIN_FINDINGS) return;

    // 두 창의 소스 구성을 맞춘다. 견줄 수 있는 소스가 없거나, 맞추고 나니 창이 얇아지면
    // 비교를 하지 않는다 — 조정 전 표를 대신 내보내지 않는다(숨김 유지).
    var mix = alignSourceMix(d, curFindings, prevFindings, scope);
    if (!mix.usable) return;
    if (mix.curFindings < WINDOW_MIN_FINDINGS || mix.prevFindings < WINDOW_MIN_FINDINGS) return;

    var dropped = 0;
    var rows = (mix.cats || []).map(function (c) {
      var label = CATEGORY_LABELS[c.category_code];
      var cc = c.cur_cnt || 0, pc = c.prev_cnt || 0;
      return {
        code: c.category_code,
        ko: label ? label.ko : c.category_code,
        curCnt: cc,
        prevCnt: pc,
        curDocs: c.cur_docs || 0,
        prevDocs: c.prev_docs || 0,
        curPct: pctText(cc, mix.curFindings),
        prevPct: pctText(pc, mix.prevFindings),
        deltaPp: (shareOf(cc, mix.curFindings) - shareOf(pc, mix.prevFindings)) * 100,
      };
    }).filter(function (r) {
      if (r.curCnt + r.prevCnt >= MOVER_MIN_SAMPLE) return true;
      dropped += 1;
      return false;
    });

    var up = rows.filter(function (r) { return r.deltaPp >= MOVER_MIN_PP; })
      .sort(function (a, b) { return b.deltaPp - a.deltaPp; }).slice(0, MOVER_MAX_ROWS);
    var down = rows.filter(function (r) { return r.deltaPp <= -MOVER_MIN_PP; })
      .sort(function (a, b) { return a.deltaPp - b.deltaPp; }).slice(0, MOVER_MAX_ROWS);
    fillMoverList(moveUpEl, up, true);
    fillMoverList(moveDownEl, down, false);

    // 소스 구성 줄은 **조정 전 총량** 기준으로 넘긴다(위 함수 주석 참조).
    renderMoverSourceLine(d.by_source, curFindings, prevFindings, mix.dropped);
    if (moveNoteEl) {
      // ★"비중이 줄었다 ≠ 건수가 줄었다" — 이 한 줄이 없으면 표가 오독된다.
      var note = "여기서 재는 것은 전체에서 차지하는 비중입니다. 건수가 늘어도 다른 영역이 " +
        "더 늘면 비중은 줄어듭니다 — 각 줄의 건수를 함께 보세요. 비교 기준: 최근 12개월 지적 " +
        fmtNum(mix.curFindings) + "건 vs 직전 12개월 " + fmtNum(mix.prevFindings) + "건" +
        (mix.applied ? "(견줄 수 있는 소스만)" : "") + ".";
      if (dropped > 0) {
        note += " 두 기간 합이 " + MOVER_MIN_SAMPLE + "건 미만인 " + dropped +
          "개 영역은 비율이 흔들려 뺐습니다.";
      }
      note += " 날짜는 자료가 공개된 날 기준이라 실사 시점과는 다릅니다.";
      moveNoteEl.textContent = note;
    }
    // [존 재편] 이 섹션은 기본 접힘이다. 열기 전에 안에 뭐가 있는지 알 수 있어야
    // 접힘이 은폐가 되지 않는다 — summary 에 실제 산출 행 수를 적는다(라이브 실측에서
    // 이 값은 "커진 1 · 줄어든 0"이고, 그 사실 자체가 이 섹션을 접어 둔 근거다).
    if (moveSummaryEl) {
      moveSummaryEl.textContent = up.length || down.length
        ? "비중이 커진 영역 " + up.length + "개 · 줄어든 영역 " + down.length + "개"
        : "기준(1%p 이상)을 넘는 변화 없음";
    }
    moveBlockEl.hidden = false;
  }

  // ── [카테고리 → 실제 지적 사례] 026 findings_search 재사용 ──────────────────
  // ★이 패널만은 집계 RPC 가 아니라 findings_search(security invoker + RLS)에서 온다 —
  // /findings/ 검색 페이지가 이미 쓰는 공개 경로 그대로이며, 공개 게이트(010 정책)를
  // 통과한 행만 내려온다. 즉 "집계 수치 ≥ 여기 보이는 건수"가 정상이고, 그 차이는
  // 커버리지 노트가 이미 설명한다.
  // 정렬은 date_desc — 최근 창의 순위 아래 붙는 패널이므로 사례도 최신부터 보여 준다.
  function truncateText(s) {
    var t = String(s || "").replace(/\s+/g, " ").trim();
    return t.length > EXAMPLE_MAX_CHARS ? t.slice(0, EXAMPLE_MAX_CHARS) + "…" : t;
  }

  function buildExampleItem(f) {
    var item = el("div", "tr-ex-item");
    var meta = [decodeFirmDisplay(f.firm_name), f.published_date, f.source]
      .filter(Boolean).join(" · ");
    item.appendChild(el("p", "tr-ex-meta", meta));
    // 국문이 있으면 국문, 없으면 영어 원문(빈칸으로 두지 않는다 — 부재 어휘 규칙).
    var body = (f.finding_text_ko || "").trim() || (f.finding_text || "").trim();
    item.appendChild(el("p", "tr-ex-text", truncateText(body)));
    return item;
  }

  function buildExamplePanel(code, ko, payload) {
    var panel = el("div", "tr-ex");
    var docs = (payload && payload.documents) || [];
    var picked = [];
    docs.forEach(function (doc) {
      (doc.findings || []).forEach(function (f) {
        if (picked.length < EXAMPLE_ROWS && f.category_code === code) picked.push(f);
      });
    });
    if (!picked.length) {
      panel.appendChild(el("p", "tr-empty",
        "이 영역은 아직 국문으로 열람할 수 있는 지적이 없습니다."));
      return panel;
    }
    picked.forEach(function (f) { panel.appendChild(buildExampleItem(f)); });
    // ★이 건수는 findings_search 가 센 **전 기간** 공개분이다 — 이 패널이 달린 순위 행은
    // 최근 12개월 수치라(실측: 무균보증 309건 vs 2,976건) 범위를 안 적으면 두 숫자가
    // 어긋나 보인다. 링크가 실제로 가는 곳도 기간 필터 없는 검색이므로 "전체 기간"이
    // 정확한 표기다.
    var total = ((payload && payload.totals) || {}).findings || 0;
    var foot = el("p", "tr-ex-foot");
    var a = document.createElement("a");
    a.className = "tr-ex-more";
    a.href = findingsHref("cat", code);
    a.textContent = "전체 기간 " + ko + " 지적 " + fmtNum(total) + "건 보기 →";
    foot.appendChild(a);
    panel.appendChild(foot);
    return panel;
  }

  function openRecentCat(code, ko) {
    state.openCat = code;
    state.exampleNode = el("p", "tr-empty", "불러오는 중…");
    renderRecentCats();
    fetchCategoryExamples(code).then(function (payload) {
      if (state.openCat !== code) return;          // 그 사이 다른 행을 열었으면 버린다
      state.exampleNode = buildExamplePanel(code, ko, payload);
      renderRecentCats();
    }).catch(function () {
      if (state.openCat !== code) return;
      state.exampleNode = el("p", "tr-empty", "사례를 불러오지 못했습니다.");
      renderRecentCats();
    });
  }

  function closeRecentCat() {
    state.openCat = "";
    state.exampleNode = null;
    renderRecentCats();
  }

  // ── [인용 조항] 042_findings_cfr_ranking ────────────────────────────────
  // 카테고리는 우리가 붙인 분류지만 조항은 규제기관이 적은 것이고 사내 SOP 와 1:1로
  // 붙는다 — 그래서 각 행이 **조문 원문(eCFR)** 과 **실제 지적 문장** 양쪽으로 이어진다.
  //
  // 보일러플레이트 제외·부 필터·범위 한계(사실상 WL 전용)는 전부 응답의 scope 에서
  // 읽어 화면에 적는다(하드코딩 금지) — 무엇을 뺐는지 밝히지 않으면 이 순위는 검증
  // 불가능한 주장이 된다(renderHeatmap 의 "뺐다는 사실을 적는다"와 같은 원칙).
  function buildCfrRow(item, idx, maxDocs) {
    var row = el("div", "tr-cf-row" + (state.openCfr === item.section ? " on" : ""));
    var name = cfrSectionLabel(item.section);
    makeClickableRow(row, "21 CFR " + item.section + " 지적 사례와 조문 보기", function () {
      if (state.openCfr === item.section) closeCfr();
      else openCfr(item.section);
    });
    row.appendChild(el("span", "tr-cf-rank", String(idx + 1)));
    row.appendChild(el("span", "tr-cf-sec", item.section));
    row.appendChild(el("span", "tr-cf-name", name));
    var track = el("div", "tr-cf-track");
    var bar = el("div", "tr-cf-bar");
    bar.style.transform = "scaleX(" + Math.max(0.02, maxDocs > 0 ? item.docs / maxDocs : 0) + ")";
    track.appendChild(bar);
    row.appendChild(track);
    var docs = el("span", "tr-cf-docs", "문서 " + fmtNum(item.docs) + "건");
    // 누적만 보면 "예전에 많이 걸렸던 조항"과 "지금도 걸리는 조항"이 구분되지 않는다.
    docs.appendChild(el("span", "tr-cf-recent", "최근 12개월 " + fmtNum(item.recent_docs) + "건"));
    row.appendChild(docs);
    row.appendChild(el("span", "tr-cf-caret", state.openCfr === item.section ? "▲" : "▼"));
    return row;
  }

  function renderCfrRows() {
    if (!cfrEl) return;
    cfrEl.innerHTML = "";
    var rows = state.cfrItems || [];
    if (!rows.length) return;
    var maxDocs = rows[0].docs || 1;
    rows.forEach(function (item, i) {
      cfrEl.appendChild(buildCfrRow(item, i, maxDocs));
      if (state.openCfr === item.section) {
        cfrEl.appendChild(state.cfrExampleNode || el("p", "tr-empty", "불러오는 중…"));
      }
    });
  }

  // 사례 패널 머리에 조문 링크를 먼저 둔다 — 실무에서는 사례보다 조문 원문이 먼저
  // 필요한 경우가 많다. 인용된 하위 항목((a)/(d) 등)도 함께 적어 어느 항이 걸렸는지
  // 알 수 있게 한다(조항 뿌리로 합치면서 버린 정보를 여기서 돌려준다).
  function buildCfrLinkLine(item) {
    var wrap = el("p", "tr-cf-links");
    var a = document.createElement("a");
    a.className = "tr-cf-link";
    a.href = ecfrHref(item.section);
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "21 CFR " + item.section + " 조문 원문 보기(eCFR) →";
    wrap.appendChild(a);
    var variants = item.variants || [];
    if (variants.length) {
      wrap.appendChild(el("span", "tr-cf-variants",
        "실제 인용된 항: " + variants.join(" · ")));
    }
    return wrap;
  }

  function buildCfrExamplePanel(item, payload) {
    var panel = el("div", "tr-ex");
    panel.appendChild(buildCfrLinkLine(item));
    var docs = (payload && payload.documents) || [];
    var picked = [];
    docs.forEach(function (doc) {
      (doc.findings || []).forEach(function (f) {
        if (picked.length >= EXAMPLE_ROWS) return;
        // 검색은 blob ILIKE 라 본문에 번호만 스친 행도 걸린다 — 실제로 그 조항이
        // 인용된 지적만 남긴다(cfr_refs 는 findings_search 가 행마다 함께 준다).
        var refs = f.cfr_refs || [];
        for (var i = 0; i < refs.length; i++) {
          if (String(refs[i]).indexOf(item.section) >= 0) { picked.push(f); return; }
        }
      });
    });
    if (!picked.length) {
      panel.appendChild(el("p", "tr-empty",
        "이 조항으로 지적된 문장 중 국문으로 열람할 수 있는 것이 아직 없습니다."));
      return panel;
    }
    picked.forEach(function (f) { panel.appendChild(buildExampleItem(f)); });
    var foot = el("p", "tr-ex-foot");
    var more = document.createElement("a");
    more.className = "tr-ex-more";
    more.href = findingsHref("q", item.section);
    more.textContent = "이 조항이 인용된 지적 검색 결과 보기 →";
    foot.appendChild(more);
    panel.appendChild(foot);
    return panel;
  }

  function openCfr(section) {
    state.openCfr = section;
    state.cfrExampleNode = el("p", "tr-empty", "불러오는 중…");
    renderCfrRows();
    var item = null;
    (state.cfrItems || []).forEach(function (r) { if (r.section === section) item = r; });
    if (!item) return;
    fetchCfrExamples(section).then(function (payload) {
      if (state.openCfr !== section) return;      // 그 사이 다른 행을 열었으면 버린다
      state.cfrExampleNode = buildCfrExamplePanel(item, payload);
      renderCfrRows();
    }).catch(function () {
      if (state.openCfr !== section) return;
      state.cfrExampleNode = buildCfrExamplePanel(item, null);
      renderCfrRows();
    });
  }

  function closeCfr() {
    state.openCfr = "";
    state.cfrExampleNode = null;
    renderCfrRows();
  }

  function renderCfrRanking(data) {
    if (!cfrBlockEl || !cfrEl) return;
    var d = data || {};
    var scope = d.scope || {};
    var items = (d.items || []).filter(function (i) { return (i.docs || 0) > 0; });
    if (!items.length) return;                    // 빈 응답 → 숨김 유지

    state.cfrItems = items.slice(0, CFR_ROWS);
    renderCfrRows();

    if (cfrSubEl) {
      var sources = (scope.sources || []).map(function (s) {
        return s.source + " " + fmtNum(s.docs) + "건";
      }).join(" · ");
      cfrSubEl.textContent = "조항이 명시된 문서 " + fmtNum(scope.docs_with_clause) +
        "건 기준" + (sources ? " (" + sources + ")" : "") + " · 막대는 그 조항을 인용한 문서 수입니다.";
    }
    if (cfrNoteEl) {
      // 무엇을 세지 않았는지 밝히지 않으면 이 순위는 검증 불가능한 주장이 된다.
      var note = "FDA 483은 조항 대신 요구사항을 문장으로 적어 조항 인용이 거의 없습니다 — " +
        "이 순위는 사실상 Warning Letter 기준입니다. " +
        (scope.part_filter ? scope.part_filter + " 조항만 셌고(표시·OTC 모노그래프·임상 조항 제외), " : "");
      var ex = scope.excluded_sections || [];
      if (ex.length) {
        note += "모든 경고서한 맺음말에 붙는 권고·정의 조항(" + ex.join(" · ") +
          ")은 위반 인용이 아니라 뺐습니다. ";
      }
      note += "211.22(a)처럼 항으로 갈라진 인용은 조항 단위로 합쳤습니다.";
      cfrNoteEl.textContent = note;
    }
    cfrBlockEl.hidden = false;
  }

  // ── 오케스트레이션 ───────────────────────────────────────────────────────
  function renderAll(data) {
    var totals = data.totals || {};
    renderStats(totals);
    renderCoverageNote(totals);
    renderCategoryRanking(data.by_agency_category || []);
    renderYearTrend(data.by_month || []);
    state.lastFirms = data.top_firms || [];
    renderFirmRanking(state.lastFirms);
    renderSource(data.by_source || []);
  }

  function rpcEndpoint(name) {
    return url.replace(/\/$/, "") + "/rest/v1/rpc/" + name;
  }

  function fetchStats() {
    return fetch(rpcEndpoint("findings_stats"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_stats " + r.status);
      return r.json();
    });
  }

  // 008_findings_category_matrix.sql — findings_stats() 와 별개 RPC(H1). 008 미적용
  // 라이브에서 404 를 반환하므로 이 fetch 만 독립적으로 실패 처리한다(아래 오케스트레이션).
  function fetchCategoryMatrix() {
    return fetch(rpcEndpoint("findings_category_matrix"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_category_matrix " + r.status);
      return r.json();
    });
  }

  // 038_findings_zone_category.sql — findings_stats/findings_category_matrix 와 별개
  // RPC(파라미터 없음). 미배포 라이브에서 404 를 반환하므로 이 fetch 만 독립적으로
  // 실패 처리한다(아래 오케스트레이션, fetchCategoryMatrix 와 동일 원칙).
  function fetchZoneCategory() {
    return fetch(rpcEndpoint("findings_zone_category"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_zone_category " + r.status);
      return r.json();
    });
  }

  // 058_fda_inspections.sql — findings 계열 RPC 전부와 별개 소스(파라미터 없음).
  // 058 미적용 라이브에서 404 를 반환하므로 이 fetch 만 독립적으로 실패 처리한다
  // (fetchZoneCategory 와 동일 원칙).
  function fetchFdaInspectionStats() {
    return fetch(rpcEndpoint("fda_inspection_stats"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error("fda_inspection_stats " + r.status);
      return r.json();
    });
  }

  // 041_findings_recent_window.sql — findings_stats 와 별개 RPC. 041 미적용 라이브에서
  // 404 를 반환하므로 이 fetch 만 독립적으로 실패 처리한다(fetchCategoryMatrix/
  // fetchZoneCategory 와 동일 원칙).
  function fetchRecentWindow() {
    return fetch(rpcEndpoint("findings_recent_window"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_months: 12 }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_recent_window " + r.status);
      return r.json();
    });
  }

  // 026_findings_search.sql — 카테고리별 실제 지적 사례(공개 게이트 RLS 통과분만).
  // 이 페이지에서 유일하게 지적 **문장**을 가져오는 경로다(집계 RPC 아님, 위 §계약 참조).
  function fetchCategoryExamples(code) {
    return fetch(rpcEndpoint("findings_search"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({
        p_category: code, p_sort: "date_desc", p_page: 1, p_docs_per_page: EXAMPLE_ROWS,
      }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_search " + r.status);
      return r.json();
    });
  }

  // 042_findings_cfr_ranking.sql — findings_stats/findings_recent_window 와 별개 RPC.
  // 042 미적용 라이브에서 404 를 반환하므로 이 fetch 만 독립적으로 실패 처리한다.
  function fetchCfrRanking() {
    return fetch(rpcEndpoint("findings_cfr_ranking"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_months: 12 }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_cfr_ranking " + r.status);
      return r.json();
    });
  }

  // 조항별 사례 — 026 findings_search 의 검색 blob 에 cfr_refs 가 들어 있어(026 계약)
  // 조항 번호 질의가 그 조항을 인용한 지적을 잡는다. 문서 단위 페이지네이션이라
  // 필터 후 3건을 확보하려면 문서를 조금 넉넉히 받는다.
  function fetchCfrExamples(section) {
    return fetch(rpcEndpoint("findings_search"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({
        p_q: section, p_sort: "date_desc", p_page: 1, p_docs_per_page: EXAMPLE_ROWS * 2,
      }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_search " + r.status);
      return r.json();
    });
  }

  function fetchFirmStats(firmName) {
    return fetch(rpcEndpoint("findings_firm_stats"), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ p_firm: firmName }),
    }).then(function (r) {
      if (!r.ok) throw new Error("findings_firm_stats " + r.status);
      return r.json();
    });
  }

  // ── [존 재편 2026-08-26] 면별 오케스트레이션 ─────────────────────────────
  // 재편 전에는 한 페이지가 여섯 개 RPC 를 **전부** 병렬로 쳤다. 존을 세 면으로 나눈
  // 지금 그대로 두면 각 면이 자기가 그릴 수 없는 데이터까지 받아 버린다(실사 결과 면이
  // findings 누적 집계를 받는 식) — 낭비이기도 하지만, 더 나쁘게는 **그 면에 없는
  // 섹션의 fetch 실패가 그 면의 오류로 보이게** 된다.
  // 그래서 면마다 "무엇을 그릴 수 있는가"를 선언하고 그것만 친다.
  //
  // ★각 면의 **주 데이터**가 로딩 해제를 책임진다(revealContent). 부 데이터는 실패해도
  //   자기 블록만 숨긴 채로 남는다 — 기존 "실패 반경은 자기 자신뿐" 원칙 그대로다.
  var WANT = ({
    // [컨셉 재정의] 지적 경향 면에서 zone 이 빠지고(→ 데이터 현황) stats 도 빠졌다
    // (핵심 통계 5개 삭제 — 누적 총량은 사용자 질문이 아니다). 이 면이 치는 RPC 는
    // **041(최근 창)과 042(조항) 둘**뿐이다.
    trends: { recent: true, cfr: true },
    inspections: { fda: true },
    coverage: { stats: true, matrix: true, zone: true },
  })[page] || { stats: true, recent: true, cfr: true, zone: true };

  function revealContent() {
    loadingEl.hidden = true;
    contentEl.hidden = false;
  }

  function failContent() {
    loadingEl.hidden = true;
    errorEl.hidden = false;
  }

  if (!url || !key) {
    // 안내 문구는 면마다 다르다(템플릿의 오류 div 가 정본) — 여기서 새로 짓지 않고
    // 그 문장을 그대로 옮겨 쓴다. 비어 있으면 공통 문안으로 폴백한다.
    loadingEl.textContent = (errorEl.textContent || "").trim() || "통계 서비스 준비 중입니다.";
    return;
  }

  // [컨셉 재정의] 기관 선택 초기값 — 저장된 값이 있으면 그것, 없으면 식약처.
  // ★기본이 '전체'가 아닌 이유는 AGENCY_VIEWS 위 주석 참조(합산은 어느 기관의
  //   현실도 아니라, 그것을 기본 화면으로 두면 오답을 기본값으로 두는 것이 된다).
  state.agency = readStoredAgency() || "mfds";
  wireAgency();

  // [주 데이터 · 데이터 현황] 007 findings_stats.
  // ★지적 경향 면은 더 이상 이 RPC 를 치지 않는다(핵심 통계 5개를 걷어냈다) —
  //   그 면의 주 데이터는 041 이고, 로딩 해제도 041 이 책임진다.
  if (WANT.stats) {
    fetchStats()
      .then(function (data) {
        revealContent();
        renderAll(data);
        maybeOpenFirmFromUrl();
      })
      .catch(failContent);
  }

  // [주 데이터 · 실사 결과] 058/059 fda_inspection_stats — findings 계열과 완전히 다른
  // 소스다. 이 면에서는 이것이 주 데이터라, 0건·구버전 응답으로 블록이 끝내 펴지지
  // 않으면 빈 화면을 보여주는 대신 안내로 내린다(조용한 빈 페이지 금지).
  if (WANT.fda) {
    fetchFdaInspectionStats()
      .then(function (data) {
        renderFdaInspections(data);
        // [062] 같은 응답에서 파생 — 추가 네트워크 호출 0. 062 미적용 라이브에서는
        // 두 키가 없어 각 렌더러가 조용히 no-op 한다(주 데이터는 059 만으로 그려진다).
        renderFdaQuarters(data);
        renderKorea(data);
        if (fdaBlockEl && !fdaBlockEl.hidden) revealContent();
        else failContent();
      })
      .catch(failContent);
  }

  // [부 데이터] 008 히트맵 — 실패해도(008 미적용 라이브 포함) tr-heatmap-block 은 정적
  // 셸의 기본값인 hidden 상태 그대로 남는다. 다른 섹션엔 전혀 영향이 없다.
  if (WANT.matrix) {
    fetchCategoryMatrix()
      .then(function (data) { renderHeatmap(data); })
      .catch(function () { /* 조용히 숨김 유지 */ });
  }

  // [부 데이터] 038 해외 vs 미국 — '데이터 현황' 면의 독립 섹션(FDA 483 코퍼스의
  // 지리적 구성). 실패해도 그 블록만 hidden 으로 남는다.
  if (WANT.zone) {
    fetchZoneCategory()
      .then(function (data) { renderZonePanel(data); })
      .catch(function () { /* 조용히 숨김 유지 */ });
  }

  // [부 데이터] 041 최근 12개월 — 한 번의 응답으로 월별 막대·최근 순위 보기·달라진 점
  // 셋을 모두 그린다(추가 네트워크 호출 0).
  if (WANT.recent) {
    fetchRecentWindow()
      .then(function (data) {
        // 지적 경향 면에서는 이것이 **주 데이터**다 — 순위가 그려지면 로딩을 푼다.
        // renderMovers 는 renderRecentWindow → applyAgency 안에서 기관과 함께 불린다
        // (여기서 따로 부르면 기관 없이 합산으로 한 번 그려졌다가 덮이는 깜빡임이 난다).
        renderRecentWindow(data);
        if (!WANT.stats) {
          if (rankBlockEl && !rankBlockEl.hidden) revealContent();
          else failContent();
        }
      })
      .catch(function () { if (!WANT.stats) failContent(); });
  }

  // [부 데이터] 042 인용 조항 — 실패해도 tr-cfr-block 은 hidden 그대로.
  if (WANT.cfr) {
    fetchCfrRanking()
      .then(function (data) { renderCfrRanking(data); })
      .catch(function () { /* 조용히 숨김 유지 */ });
  }
})();
