# FIND-1 M7 — findings 대시보드 밴드(`/findings/` 필터 연동 분포·추이·업체 통계)

> 날짜: 2026-07-09
> 상태: 코드 완료·테스트 green(웹 95, 신규 assertion 6개 포함·루트 1378+842)
> 코드 정본: `web/assets/findings.js`(`computeStats`/`computeAgencyDist`/`computeCategoryDist`/`computeMonthTrend`/`computeFirmTop`, `renderDash*`, `toggleCategoryFilter`/`toggleMonthFilter`/`toggleFirmFilter`, `makeClickableRow`), `web/templates/findings.html`(`#fnd-dash` 셸+인라인 스타일)
> 테스트 정본: `web/tests/test_render.py`(`WebFindingsRenderTest`, 신규 6개 — `test_dash_shell_present_and_hidden_by_default`·`test_dash_compute_and_click_wiring_markers_present`·`test_dash_accessibility_markers_present`·`test_dash_hides_when_zero_results`·`test_dash_no_innerhtml_data_injection`·`test_dash_no_new_external_resources`), `web/tests/golden/findings.expected.html`(재동결)
> 커밋: `b11e7e3`

---

## 0. M7 게이트 — 하는 것 / 안 하는 것

M6까지 `/findings/`는 국문 병기(M6a~d)로 카드 단위 가독성을 확보했지만, 여러 건을 한눈에 조망할 방법이 없었다 — 사용자는 검색·필터를 반복해야만 "이 필터 결과에 어떤 기관/카테고리/시기/업체가 몰려 있는지"를 알 수 있었다. 동시에 M4(`ENABLE_FINDINGS_SUPABASE_APPEND`)가 raw-only 관찰 단계이고 M4 2단계(`_FINDINGS_APPEND`)가 활성화되면 신규 findings 행이 매일 쌓이기 시작한다(§4 이월) — 데이터가 늘어나기 전에 조망 계층을 먼저 붙여두는 것이 이번 M7의 동기다.

- **하는 것:** `/findings/` 검색 결과 위에 **필터 컨트롤과 완전히 연동된** 콤팩트 대시보드 밴드(`#fnd-dash`)를 추가한다. 모든 수치는 페이지 로드 시점의 전체 데이터가 아니라 **현재 필터가 적용된 결과(`matched`)** 기준으로 `render()`가 호출될 때마다 재계산된다 — 즉 이 밴드는 별도의 요약 화면이 아니라 검색 자체를 돕는 **탐색 도구**다. 구성 4블록: ① 요약 스탯(총 N건·기관별 칩·검토 필요 n건·번역 대기 n건), ② 카테고리 분포(상위 6+"그 외" 가로 막대), ③ 월별 추이(최대 12개월 세로 미니 막대), ④ 업체 상위 5. 각 블록의 클릭 가능한 항목(카테고리 행/월 열/업체 행)은 페이지의 기존 필터 상태(`state.category_code`/`state.month`/`state.q`)를 토글하고 대응 `<select>`/검색창 값을 동기화한 뒤 기존 `render()`를 재호출한다 — 대시보드 전용 상태 저장소를 새로 만들지 않는다.
- **하는 것(접근성):** 클릭 가능한 모든 행/열은 `role="button"`+`tabIndex=0`+`aria-label`+`Enter`/`Space` 키보드 활성화를 갖춘다(마우스 전용 UI 금지). 카테고리·월 막대는 색상(`--coral`)만으로 크기를 전달하지 않고 옆에 숫자를 항상 병기한다.
- **하는 것(결정론 보존):** 정적 셸(`findings.html`의 `#fnd-dash`)은 다섯 개 빈 컨테이너(`#fnd-dash-stats`/`-cat`/`-month`/`-firm`)와 `hidden` 속성만 렌더한다 — 골든 `findings.expected.html`은 이 빈 셸만 동결하면 되고, 실제 통계 채움은 전부 런타임(`findings.js`)이 담당하므로 라이브 데이터가 늘어나도 골든이 깨지지 않는다.
- **하지 않는 것:** 새 외부 라이브러리·CDN·차트 프레임워크 도입(막대는 전부 `<div>` 폭/높이 스타일링, `<canvas>`·`chart.js`·`d3` 등 0), `grm.css` 수정(모든 시각 스타일은 `findings.html` 안 `<style>` 블록에 인라인으로 두되 `var(--coral)`/`var(--line-2)`/`var(--card)` 등 기존 토큰만 참조), `base.html`/`render.py` 등 공용 렌더 경로 접촉, 서버 측 집계(모든 계산은 브라우저에서 fetch 이후 순수 JS로 수행 — 신규 API 없음), M2 오프라인 뷰어(`findings_search_page.py`) 확장(이 뷰어는 정적 단일 HTML 스냅샷이라 실시간 필터 연동 대시보드의 전제[살아있는 `state`]와 맞지 않아 범위 밖으로 명시적으로 제외), findings 레코드 스키마·taxonomy·번역 계약 변경(M1~M6 전부 불변).

---

## 1. 대시보드 계약

### 1.1 셸 vs 런타임 분리

`findings.html`은 `#fnd-dash` 섹션을 `hidden` 속성과 함께 렌더한다(§4.5 골든 결정론). 다섯 개 자식 컨테이너(`#fnd-dash-stats`, `#fnd-dash-cat`, `#fnd-dash-month`, `#fnd-dash-firm`, 그리고 각 블록 제목 `<h2>`)는 정적 셸 단계에서 전부 비어 있다. `findings.js`는 `hasDash = !!(dashEl && dashStatsEl && dashCatEl && dashMonthEl && dashFirmEl)`로 다섯 엘리먼트가 모두 존재할 때만 대시보드 로직을 활성화한다 — 셸이 없거나 일부만 있으면(예: 템플릿 드리프트) 검색 자체는 기존과 동일하게 계속 동작한다(하위호환·비차단).

### 1.2 집계 정의(`computeStats`, 순수 함수)

입력은 언제나 **현재 필터를 통과한 rows 배열**(`matched` — `state`의 `agency`/`category_code`/`source`/`evidence_level`/`review_status`/`month`/`q` 조건을 모두 만족하는 부분집합)이다. 부작용 없음(네트워크·DOM 접근 없음), 매 `render()` 호출마다 처음부터 다시 계산한다(누적/캐시 없음).

| 필드 | 정의 |
|---|---|
| `total` | `matched.length` |
| `agencies` | `agency`별 건수, 내림차순(동률은 기관명 사전순) — 0건 기관은 미노출 |
| `needsReview` | `review_status === "needs_review"` 건수 |
| `pendingTranslation` | `finding_text_ko`가 공백 제거 후 비어있는 건수(M6 번역 진행률의 역) |
| `categories` | `category_code`별 건수, 내림차순(동률은 code 사전순) — 라벨은 `CATEGORY_LABELS[code].ko`(M6d 동기화 테이블 재사용) |
| `months` | `published_date.slice(0,7)`(YYYY-MM)별 건수, 월 오름차순 정렬 후 **최근 12개월만** 유지(13개월 이상이면 앞부분 자름) |
| `firms` | `firm_name.trim()`별 건수, 내림차순(동률은 업체명 사전순) — **상위 5건만** |

빈 `agency`/`category_code`/`published_date`/`firm_name`은 각 집계에서 제외한다(분모 왜곡 방지). `total`만 필터 결과 전체 건수이고, 나머지 4개 집계는 각자 유효한 필드가 있는 행만 대상으로 한다는 점에 주의 — 예를 들어 `agencies` 합계가 `total`보다 작을 수 있다(agency 공란 행 존재 시).

### 1.3 필터 연동(클릭 = 토글)

| 블록 | 클릭 대상 | 동작 |
|---|---|---|
| 카테고리 분포 | 각 카테고리 행("그 외" 제외) | `toggleCategoryFilter(code)` — `state.category_code`가 이미 `code`면 `""`로 해제, 아니면 `code`로 설정. `#fnd-f-category` select 값도 동기화 후 `render()` |
| 월별 추이 | 각 월 열 | `toggleMonthFilter(month)` — `state.month` 토글 + `#fnd-f-month` select 동기화 + `render()` |
| 업체 상위 5 | 각 업체 행 | `toggleFirmFilter(name)` — `state.q` 토글(검색창에 업체명 채움/비움) + `#fnd-q` input 동기화 + `render()` |

세 토글 함수 모두 **새 상태 저장소를 만들지 않는다** — 기존 텍스트 검색/드롭다운 필터와 동일한 `state` 필드를 재사용하므로, 대시보드에서 카테고리를 클릭한 뒤 드롭다운을 열어보면 그 카테고리가 이미 선택돼 있고, 반대로 드롭다운에서 필터를 걸면 대시보드 카테고리 행에 `.on` 강조가 즉시 반영된다(양방향 정합, 별도 동기화 로직 불필요 — 두 UI가 같은 `state`를 읽고 쓰기 때문).

### 1.4 표시/숨김 규칙

`renderDash(matched)`는 `hasDash`가 false면 즉시 반환(no-op). `matched.length === 0`이면(필터 결과 0건, 또는 최초 fetch 실패로 `ROWS`가 없어 `matches()`가 전혀 통과하지 못하는 경우 포함) `dashEl.hidden = true`로 밴드 자체를 숨기고 반환 — 빈 대시보드("0건입니다" 껍데기)를 보여주지 않는다. 결과가 1건 이상이면 4개 렌더 함수를 순서대로 호출(`renderDashStats`→`renderDashCategories`→`renderDashMonths`→`renderDashFirms`)후 `dashEl.hidden = false`로 노출한다. 각 하위 렌더 함수는 자신의 컨테이너를 `innerHTML = ""`로 비운 뒤(컨테이너 clear 전용 — 계약 §1.5) `createElement`/`textContent`로 다시 채운다. 카테고리·월별 블록은 집계 자체가 0건(예: 필터 결과는 있지만 전부 `category_code` 공란)이면 `"표시할 데이터가 없습니다."` 안내 문단을 각자 렌더한다(밴드 전체를 숨기지 않고 블록 단위로만 비움 처리).

### 1.5 계약 보존(M1~M6과 동일 원칙)

- **innerHTML은 컨테이너 clear 전용.** 대시보드 렌더 함수 전체에서 `innerHTML = "..."`(데이터 삽입)는 0건이며, 유일한 사용은 `xxxEl.innerHTML = ""`(비우기)뿐이다 — `test_dash_no_innerhtml_data_injection`이 정규식으로 소스 내 모든 `\w+\.innerHTML\s*=\s*(.+);` 대입을 찾아 우변이 `""` 리터럴이 아니면 실패시킨다.
- **XSS-safe 렌더.** 모든 텍스트(카테고리 라벨·월 라벨·업체명·수치)는 `document.createTextNode`/`el().textContent`로 삽입한다(기존 `findings.js` 카드 렌더와 동일 패턴).
- **외부 리소스 0.** 신규 CDN·스크립트 태그·차트 라이브러리 마커(`cdn.`·`chart.js`·`d3.`·`echarts`·`<canvas>` 등)가 `findings.html`/`findings.js`에 없음을 `test_dash_no_new_external_resources`가 고정한다. `findings.html`의 `<script>` 태그는 여전히 `findings.js` 하나뿐이다.
- **`hasDash` 하위호환.** 다섯 엘리먼트 중 하나라도 없으면 대시보드 로직 전체가 조용히 비활성화되고 기존 검색/필터 흐름은 그대로 동작한다 — 템플릿과 스크립트 버전이 어긋나도(예: 캐시된 구 HTML + 신 JS) 페이지가 깨지지 않는다.

---

## 2. 시각 인코딩

### 2.1 토큰 재사용(신규 CSS 변수 0)

`findings.html`의 `<style>` 블록에 `.fnd-dash*` 셀렉터를 추가했지만 전부 기존 `grm.css` 커스텀 프로퍼티만 참조한다 — `--card`/`--line-2`/`--rad`(밴드 컨테이너, `.controls`와 동일 카드 스타일)·`--coral`/`--coral-2`/`--coral-tint`(막대·활성 강조·경고 칩)·`--strong`(hover 배경)·`--muted`/`--ink`/`--body`(텍스트 위계)·`--mono`(수치 폰트). `grm.css` 자체는 무수정.

### 2.2 수치 병기 원칙 — 색상 단독 전달 금지

카테고리 막대(`.fnd-dash-cat-bar`)와 월별 막대(`.fnd-dash-month-bar`)는 폭/높이로 상대 크기를 보여주지만, 옆에 항상 절대 수치(`.fnd-dash-cat-count`/월 열의 `title`+라벨)를 `--mono` 폰트로 병기한다 — 시각 장애·색각 이상 사용자도 막대 색(`--coral`)에 의존하지 않고 정확한 건수를 읽을 수 있다. 활성(선택된) 항목은 `.on` 클래스로 `--coral-tint` 배경을 추가하지만, 이 역시 보조 신호일 뿐 `aria-label`(예: `"{카테고리} 카테고리로 필터: {N}건"`)이 클릭 결과를 스크린리더에 명시한다.

### 2.3 마크 스펙

- **요약 스탯 칩**: `<span class="fnd-dash-chip">` — 기관별 칩은 중립(`--strong` 배경), "검토 필요"/"번역 대기" 칩은 경고 변형(`.warn`, `--coral-tint` 배경)으로 시각적으로 구분하되 텍스트 자체("검토 필요 N건")가 의미를 전달한다(색만으로 경고를 표시하지 않음).
- **카테고리 행**: 라벨(고정폭 96px, keep-all 없음 — 6종 이내 한글 라벨은 줄바꿈 리스크 낮음) + 트랙(`--line` 배경, `border-radius:99px`) + 막대(`--coral`, 폭 %) + 우측 정렬 수치.
- **월별 열**: 세로 막대(최대 높이 64px, `min-height:4px`로 0건에 가까운 달도 시각적으로 존재를 알 수 있게 최소 높이 보장) + 하단 라벨(다년도 데이터가 섞이면 `YY.MM`, 단일 연도면 `MM`만 — `multiYear` 판정으로 라벨 밀도 자동 조절).
- **업체 행**: 이름(좌측, `text-overflow:ellipsis`로 긴 상호 축약) + 수치(우측, `--mono`).

### 2.4 반응형

`.fnd-dash-grid`는 데스크톱 3열(`grid-template-columns:repeat(3,1fr)`)이고 `≤860px`에서 1열로 스택된다(`.fnd-search`/`.fnd-filters` 등 기존 `/findings/` 반응형 브레이크포인트와 별개로 대시보드 자체 미디어 쿼리를 둔 것 — 3블록이 나란히 있을 때 모바일에서 각 블록 폭이 너무 좁아지는 것을 방지).

---

## 3. 검증 기록

- **신규 테스트 6개**(`web/tests/test_render.py::WebFindingsRenderTest`):
  - `test_dash_shell_present_and_hidden_by_default` — `#fnd-dash` 및 4개 자식 컨테이너 id 존재, 여는 태그에 `hidden` 속성, 컨테이너가 전부 빈 채로 렌더됨을 확인.
  - `test_dash_compute_and_click_wiring_markers_present` — `computeStats`/`computeAgencyDist`/`computeCategoryDist`/`computeMonthTrend`/`computeFirmTop` 함수 선언과, 클릭 토글이 기존 `state.*`/`document.getElementById("fnd-f-category"|"fnd-f-month")`를 재사용해 `renderDash(matched)`를 호출하는 배선을 소스 마커로 확인.
  - `test_dash_accessibility_markers_present` — `role="button"`·`tabIndex = 0`·`aria-label`·`Enter`/`Space` 키 핸들링 마커 확인.
  - `test_dash_hides_when_zero_results` — `if (!matched.length) { dashEl.hidden = true; }` 소스 패턴 확인.
  - `test_dash_no_innerhtml_data_injection` — 전 `.innerHTML =` 대입이 `""` 비우기뿐인지 정규식 스캔.
  - `test_dash_no_new_external_resources` — CDN/차트 라이브러리/`<canvas>` 마커 부재 + `<script>` 태그 1개(findings.js)만 확인.
- **골든**: `web/tests/golden/findings.expected.html`만 재동결(`#fnd-dash` 빈 셸 삽입분만 diff) — `base.html`/`render.py` 등 공용 렌더 경로는 무접촉(다른 페이지 골든 byte-diff 0으로 확인).
- **전체 스위트**: 웹 95(위 신규 assertion 6개 포함)·루트 1378 passed+842 subtests green.
- **커밋**: `b11e7e3`.

---

## 4. 이월

- **M4 2단계 관찰** — M4 raw-only 적재(`ENABLE_FINDINGS_SUPABASE_APPEND=true`)의 머지 후 첫 daily run(매일 ~20:15 UTC 스케줄) 관찰은 이번 M7과 별개로 대기 중인 항목이다. 이 관찰 결과에 따라 `_FINDINGS_APPEND`(findings 직행 적재) 활성화 시점이 정해지고, 활성화되면 대시보드가 조망하는 데이터 볼륨이 실질적으로 늘기 시작한다 — M7은 그 전에 조망 계층을 먼저 마련해 둔 것이다.
- **번역 자동화** — M6 §6에서 이월된 항목 그대로다: 신규 findings의 `finding_text_ko` 채움은 여전히 사람/LLM이 `findings_translate.py --export`→번역→`--apply`를 주기적으로 실행하는 반자동 절차이며, 대시보드의 "번역 대기 N건" 칩이 이 잔여를 계속 표면화한다(자동화 착수 여부는 `_FINDINGS_APPEND` 활성화 이후 유입 속도를 보고 재평가).
- **WL 장문 분할** — FDA Warning Letter 원천은 `wl_body_excerpt`/`wl_body_full` 전문이 통째로 `finding_text` 1건에 담기는 구조라(§ `findings_extractors.py`), 여러 위반사항이 섞인 긴 WL 하나가 findings 통계에서 "1건"으로만 집계된다 — 개별 위반사항 단위로 분할해 카테고리/추이 집계의 해상도를 높이는 것은 이번 M7 범위 밖이며, 추출기(`findings_extractors.py`) 층의 별도 개선으로 이월한다.
- **대시보드 자체의 추가 고도화** — 카테고리/월별 조합 교차 필터, 대시보드 자체의 리셋 버튼, 인쇄/내보내기 등은 이번 M7에서 다루지 않았다(사용자 피드백 기반 후속 판단 대상).
