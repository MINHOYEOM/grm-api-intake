# GRM 웹 렌더러 (P2·P4)

`grm-web-card/v1` JSON → **정적 멀티페이지 사이트**(랜딩·아카이브·브리프 상세).
순수·결정론 빌더. 디자인 계약 = `GRM_웹_프로토타입_v4.html` + 검색/네비/모션
= `GRM_웹_P4_아카이브검색_프로토타입_v2.html`(CSS 는 `assets/grm.css` 로 동결 추출).
P4 = 아카이브 전 호 카드 **교차검색 + facet 필터**(정적 클라이언트사이드, 빌드시
`search-index.json` 파생 + progressive enhancement)·상세 카드 `document_id` 앵커·v2 네비/모션.

> **헤드리스 분리:** routine(수집 시스템)은 **데이터(JSON)만** 만들고, "그리는 일"은 이 렌더러가 맡는다.
> 렌더러는 JSON 값을 **그대로** 출력한다 — 사실·URL·숫자·업체명 재생성/교정/번역 **금지**.

## 구조
```
web/
├─ render.py            # 빌더(순수): data/briefs/*.json → dist/
├─ linkcheck.py         # 배포단계 링크체크(P3·C1): link_check enrich. 네트워크는 여기만 — render.py 순수성 보존
├─ templates/           # base(네비·모션) · landing · archive(검색 UI) · brief (Jinja2, autoescape on)
├─ partials/card.html   # 카드 1장 (grm-web-card/v1 card → v4 카드 마크업)
├─ assets/grm.css       # 디자인 동결 CSS(v4 + P4 네비/모션/검색 UI). 손으로 편집 금지 — 디자인 변경은 프로토타입 갱신 후 반영
├─ assets/archive.js    # 아카이브 교차검색(P4·정적 클라이언트사이드). search-index.json fetch → facet/검색/토글. 비골든
├─ data/briefs/*.json   # 입력(주차별 1파일). 현재 = 실 6/22. 규약 = data/briefs/README.md(C4)
├─ tests/
│  ├─ test_render.py    # 골든·결정론·무변형·escape·순수성 + 검색인덱스(WebSearchIndexTest)
│  ├─ test_linkcheck.py # 링크체크 모킹 단위테스트(200/404/503/timeout/KR-skip/HEAD→GET)
│  ├─ golden/           # 동결 기대 HTML + search-index*.expected.json (byte-diff)
│  └─ fixtures/multi/   # 합성 2건(06-08 산문·번역 / 06-15 병합) — 멀티 골든용
└─ dist/                # 빌드 산출(정적, assets/search-index.json 포함). git 비추적
```
> 배포 Action = `.github/workflows/grm-web-deploy.yml`(루트 기준). 수집(`grm-intake.yml`)과
> 완전 별도(D8). 테스트는 `tests/test_web_render.py`·`tests/test_web_linkcheck.py` shim 으로
> 공용 스위트에 합류.

## 빌드 · 미리보기 · 골든
```bash
python web/render.py                         # web/data/briefs → web/dist/
python web/render.py --data DIR --out OUT     # 임의 입력/출력
python web/tests/test_render.py --freeze      # 골든 (재)동결
python -m unittest discover -s tests          # 전체 스위트(웹 포함, CI 와 동일)

# 배포 파이프 로컬 재현(P3): 링크체크 enrich(비파괴) → 그 입력으로 렌더
python web/linkcheck.py --data web/data/briefs --out /tmp/checked   # link_check 주입(네트워크)
python web/render.py    --data /tmp/checked    --out web/dist        # enrich 입력으로 빌드
```
미리보기는 `web/dist/index.html` 을 열어 확인.

## 불변식 (깨지 말 것)
1. **순수 렌더** — JSON 값 무변형. 렌더러 보유 텍스트는 정적 카피(템플릿)+면책 캐논(brief.html)뿐.
2. **결정론** — 같은 JSON → byte 동일 HTML. `datetime.now`/난수 0, 정렬은 입력 파생, autoescape on, 출력 항상 LF/UTF-8.
3. **정적·$0** — 외부 fetch 0, 런타임 서버 0. 폰트는 v4 동일 CDN.
4. **멀티페이지** — 라우트별 개별 HTML. 링크는 페이지 깊이별 상대경로(호스트 무관).
5. **디자인 = v4** — `assets/grm.css` 수정 금지(디자인 변경은 v4 갱신 후 재추출). 한글에 mono 금지.

## 빈 슬롯 · KO · 링크 상태 처리
- **빈 LLM 슬롯**(title_issue·summary·key_facts·implication·checks·tldr·번역): 빈 값이면 해당 블록/줄 **생략**. 실 6/22 는 산문이 전부 빈 placeholder → 코드 필드만 렌더되는 상태가 정상(구조 골든으로 유효).
- **KO 인용**: `translation == null|""` → 번역 줄 생략(원문만). 비KO 번역이 있으면 ①② 인터리브.
- **링크 상태**(`sources.link_check`): `ok|pending`→정상, `degraded`→⚠️(살아있는 링크), `broken`→"일시 접근불가"(클릭 비활성). P1 은 `pending` 고정 — 실제 200 체크 주입은 배포단계 `linkcheck.py`(P3·C1)가 한다(아래 §링크체크).

## 스키마 v1 한계 → 결정론 파생 (v1.1 후보)
- **issue 번호**: 스키마에 없음 → `data/briefs` 의 `publish_date` 오름차순 순위(가장 오래된=1).
- **브리프 제목**: 스키마에 brief 단위 제목 없음 → `tldr[0]` 있으면 사용, 없으면 `publish_date` 파생 "{Y}년 {M}월 {N}주차".

## 링크체크 (P3·C1 — `linkcheck.py`)
배포 전 공식 링크 200 체크(D7). **렌더러와 분리** — 네트워크는 `linkcheck.py` 에만, `render.py`
순수성 보존. `web/data/briefs/*.json` 의 각 카드 `info_url`·`official_url` 에 HEAD(폴백 GET)
요청 → `sources.link_check` 를 enrich 한 **사본**을 `--out` 에 쓴다(원본 비파괴).
- **상태 산정**(false-broken 방지 우선 — `classify_status` + `check_url` 예외 분기와 일치):
  - `2xx/3xx` → `ok`
  - `401/403` · 봇월 인터스티셜(apology/challenge/captcha) · 교차호스트 에러 리다이렉트 → **보존**(`inconclusive` — link_check 미기록, 기존 상태 유지)
  - `404/410`(same-host) → `broken`  · 그 외 4xx(400·451…) → `broken`
  - `5xx`(500·502·503·504…) · `408` · `429` · 타임아웃 · **연결실패(DNS/refused)** → `degraded`  ← 일시 egress 보존
  - 그 외 요청예외(잘못된 URL·과다 리다이렉트) → `broken`
  - `*.go.kr`·`*.or.kr`(KR-egress) → 체크 스킵 → `ok` 보존  · 빈 URL → 보존(보통 `pending`)
- **비차단(D7)**: 깨진 링크는 *표시·보류*이지 배포 중단이 아니다 — 항상 exit 0(경고만).
- **KR-egress 오탐 방지**: 국내 정부/공공(`*.go.kr`·`*.or.kr`)은 클라우드 egress 차단·봇거부로
  false broken 위험 → **체크 스킵 → `ok` 유지**(`KR_SKIP_SUFFIXES`). 도메인 정책은 코드 상단 상수.
- **결정론 분리**: 네트워크라 비결정 → 렌더러 골든에 포함 금지. 자체 검증 = 모킹 단위테스트.

## 배포 · 승인→라이브 게이트 (P3·C2/C3)
`.github/workflows/grm-web-deploy.yml`(수집과 별도·최소권한·`workflow_dispatch` 독립 재실행, D8).
파이프: **linkcheck(비차단) → render(결정론) → Cloudflare Pages 배포**. 빌드는 우리 CI 의
Python(호스트 비종속) — 호스트는 정적 `dist` 업로드만 받는다(락인 최소, Vercel 로 바꾸려면 Deploy
스텝만 교체).
- **미리보기**: PR / 비-production 브랜치 push → preview URL + `dist` 아티팩트(D5 육안검토).
- **승인=라이브(D5)**: production 브랜치(Cloudflare 설정)로의 **사람 머지** = 라이브. 워크플로우에
  자동 라이브 승격 로직 0 — 이 머지가 유일한 사람 게이트. fork PR 은 시크릿 미전달 → 배포 자동 스킵.
- **사람 선행작업**(머지/시크릿은 사람만): ① Cloudflare Pages 프로젝트 생성(이름=`vars.CF_PAGES_PROJECT`,
  기본 `grm-weekly-brief`) + production 브랜치 지정·보호 ② GitHub Secrets 에 `CLOUDFLARE_API_TOKEN`
  (Pages 편집 최소 스코프)·`CLOUDFLARE_ACCOUNT_ID` 등록(코드/로그 노출 0) ③ (선택) Environments
  `production` required reviewer. **시크릿 미등록 동안에도** 빌드·아티팩트는 정상 산출(배포만 스킵·경고).
- **$0(D9)**: GitHub Actions 무료분 + Cloudflare Pages 무료(무제한 대역폭). 유료 서비스 0.

## 입력 배선 (P3·C4)
routine(사람 실행 Claude)이 만든 grm-web-card/v1 JSON 을 `web/data/briefs/brief_web_{date}.json`
으로 커밋 → 그 커밋이 배포 Action 을 트리거(D8 공유 저장소 결합). 규약·중복일자 정책은
`web/data/briefs/README.md`.

## 아카이브 교차검색 + facet (P4 — `search-index.json` + `archive.js`)
빌드 시 `render.py` 가 전 호 카드를 모아 `dist/assets/search-index.json`(카드 1개=1엔트리
+ facet 메타 + 호 메타)을 **결정론·무변형**으로 파생한다. 검색·필터는 `archive.js`(정적
클라이언트사이드)가 이 인덱스로 수행 — **런타임 서버 0**.
- **무변형**: 인덱스는 카드 기존 값만 담는다(사실/URL/제목 재생성 0). `text` = `headline_target
  + title_issue + card_type + agency + facts[].value (+ summary·key_facts)` verbatim 결합(소문자화는
  클라이언트). `target`/`issue`/`href` 도 카드값 파생. null modality 보존.
- **facet 차원** = 기관·카테고리·제품군·기간(Tier/Evidence 제외, 확정). **실제 존재값만** 노출
  (agency/category/modality 알파벳, months 최신순). 빈 차원은 칩 그룹 자체 생략.
- **상세 카드 앵커 = `document_id`**(`card.id`). 상세 `<article id>`·TOC `href`·인덱스 `href` 가
  **모두** `_card_anchor()` 한 함수로 파생 → 검색결과→카드 점프가 항상 일치. id 없는 입력은
  `c{render_order}` 폴백.
- **인덱스 href 는 아카이브 페이지(깊이 1) 기준 상대경로**(`../briefs/{date}/index.html#{id}`).
  검색은 spec 상 아카이브에만 얹으므로 단일 산출물에 접두를 고정한다.
- **progressive enhancement / graceful**: 서버가 `#static-issues`(호 목록)를 항상 렌더 = baseline.
  검색창·필터·토글은 기본 `display:none`, `archive.js` 가 인덱스 fetch **성공 시에만** `body.js-search`
  로 노출하고 `#results` 를 동적 치환한다. JS 미지원/fetch 실패 → 정적 목록 그대로(열람 가능).
- **검색 동작은 비골든**(spec §1.5). 골든 = `search-index*.expected.json`(byte) + 정적 마크업.
  검색 로직 검증 = `WebSearchIndexTest`(구조·무변형·facet·정렬·앵커 href↔id 일치) + 결정론(2× 빌드 동일).
- **XSS**: 인덱스 값은 `archive.js` 의 `esc()`(`& < > " '`)로 텍스트·속성 모두 이스케이프 후 삽입.
  하이라이트는 이스케이프된 문자열에 정규식(메타문자 escape) 적용.

## 네비·모션 (P4 — 사이트 전체)
v2 로고타입(산스 볼드 `GRM` + 펄스 닷 + 구분선 + 모노 ASCII descriptor) + 네비 밑줄 인디케이터
+ 스크롤 그림자(`base.html`). 진입 페이드업(`.reveal`)·결과 행 stagger·토글 슬라이더·칩/행 호버.
**`@media (prefers-reduced-motion: reduce)` 존중**. 데이터 목록(`.issue`)은 정적 `opacity:0` 없음 →
애니메이션 미지원에도 가시. 기존 명암 토글·다크모드 유지. 한글에 자간·모노 금지(모노=ASCII만).

## 범위 (P3·P4)
✅ (P3) 링크체크·배포 Action(Cloudflare)·승인→라이브 게이트·입력 배선 규약.
✅ (P4) 아카이브 교차검색+facet(정적 클라이언트사이드·search-index.json·progressive enhancement)
   · 상세 document_id 앵커 · v2 네비/모션(사이트 전체·reduced-motion) · 전 골든 재동결.
⛔ Notion→JSON→commit 자동 파이프(후속) · 다크밴드 stale 플래그(후속) · Notion 병행→단순화(Cowork+사람 관찰).
