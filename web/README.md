# GRM 웹 렌더러 (P2·P4)
<!-- v6 reskin 2026-06-25 -->

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
├─ newsletter.py        # [T1.3] 뉴스레터 — 순수 티저 빌더 + 발송 게이트 + SaaS-무관 NewsletterSender + BrevoSender(Campaigns API) + CLI. 수집/배포와 별도(D8)
├─ templates/           # base(네비·모션) · landing · archive(검색 UI) · brief · me(내 스크랩) · admin(운영 콘솔, env-gated)
├─ partials/card.html   # 카드 1장 (grm-web-card/v1 card → v4 카드 마크업)
├─ assets/grm.css       # 디자인 동결 CSS(v4 + P4 네비/모션/검색 UI). 손으로 편집 금지 — 디자인 변경은 프로토타입 갱신 후 반영
├─ assets/archive.js    # 아카이브 교차검색(P4·정적 클라이언트사이드). search-index.json fetch → facet/검색/토글. 비골든
├─ assets/reactions.js  # [S1] 카드 반응(하트·스크랩·회원) — supabase-js 로 Supabase 직접 호출·RLS. 매직링크 로그인·토글·공개 하트 집계·런타임 카운트/상태 주입(비골든·PE). env-gate(SUPABASE_URL/ANON_KEY) 시에만 로드
├─ assets/admin.js      # [A1] Admin 운영 콘솔 런타임 — Admin 로그인·GitHub Actions 실행·Brevo 구독자·Supabase 회원/반응 관리
├─ data/briefs/*.json   # 입력(주차별 1파일). 현재 = 실 6/22. 규약 = data/briefs/README.md(C4)
├─ tests/
│  ├─ test_render.py    # 골든·결정론·무변형·escape·순수성 + 검색인덱스(WebSearchIndexTest) + 구독폼(test_newsletter_form_conditional)
│  ├─ test_newsletter.py # [T1.3] 티저 빌더 결정론·무변형·provenance·게이트·면책 drift·Brevo 어댑터(fake-session) 단위테스트
│  ├─ test_linkcheck.py # 링크체크 모킹 단위테스트(200/404/503/timeout/KR-skip/HEAD→GET)
│  ├─ golden/           # 동결 기대 HTML + search-index*.expected.json (byte-diff)
│  └─ fixtures/multi/   # 합성 2건(06-08 산문·번역 / 06-15 병합) — 멀티 골든용
├─ migrations/001_reaction.sql  # [S1] Supabase(Postgres) 반응 테이블+RLS+공개 집계 테이블/heart_counts 뷰. 사람 1회 실행(배포물 아님·render 미복사)
├─ ../supabase/migrations/202607050001_admin_ops.sql  # [A1] Admin 권한·감사·뉴스레터 발송 로그 + 최초 Admin bootstrap
├─ ../supabase/migrations/202607050002_admin_ops_hardening.sql # [A1.1] 발송 실행 추적·중복 실발송 방지
├─ ../supabase/migrations/20260705033033_admin_ops_security_definer_hardening.sql # [A1.2] Admin SECURITY DEFINER RPC 노출 차단
├─ ../supabase/functions/admin-* # [A1] Admin Edge Functions(Supabase service role·GitHub Actions·Brevo)
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
3. **정적·$0 (콘텐츠 층)** — **카드 콘텐츠**(dist HTML: 사실·URL·요약)는 빌드 시 외부 fetch 0·런타임 서버 0·결정론 유지. 폰트는 v4 동일 CDN. **[반응 층 예외 — 2026-07-03 결정]** 하트·스크랩·회원·향후 구독제 등 **반응 계층**은 격리된 동적 계층(엣지 함수+스토어)으로 허용하되 (a) 카드 콘텐츠·provenance 를 재생성/변형 0 — **불투명 `card_id` 만 취급**, 카드 사실·원문 URL 백엔드 미경유, (b) **progressive enhancement** — 반응 층이 죽어도 정적 카드 열람 무영향, (c) 콘텐츠 순수/결정론(#1·#2)·콘텐츠 골든 **불침범**(반응 카운트·내상태는 런타임 클라이언트 주입 → 골든 밖, 검색동작 비골든 선례 동형). 근거·설계·단계=`GRM_웹_카드반응_하트스크랩공유_설계_2026-07-03.md`.
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

## 뉴스레터 구독 폼 (T1 — env-gated, 회원 시스템 없음)
랜딩·아카이브·상세 **전 페이지 푸터 위**에 이메일 1칸 구독 밴드(`base.html` `.subscribe`).
회원가입/로그인 아님 — 이메일+더블옵트인+수신거부(SaaS 처리)만.
- **env-param**: `GRM_NEWSLETTER_FORM_ACTION`(관리형 SaaS 호스팅 구독 endpoint). 빈 값(기본·테스트)
  이면 폼 블록 전체 생략 → **전 페이지 골든 byte-diff 0**(인증 메타와 동일 패턴). 프로덕션 repo var
  설정 시에만 노출. `_safe_url` 스킴가드(비http(s) 오설정 → ""→폼 미출력 fail-safe).
- **정적·$0 보존(불변식 #3)**: 폼은 브라우저가 SaaS 로 직접 POST(`method=post`) — 빌드/페이지로드
  시 외부 fetch·런타임 서버 0(제출 POST 는 외부 링크 클릭과 동형의 사용자 동작). 더블옵트인·수신거부·
  구독자 PII 는 SaaS 소유(Notion 비복제). 추적 파라미터는 SaaS 가 발송 시점 래핑 → 카드 원문/공식 URL
  (provenance 가드 대상)과 무관한 별개 endpoint = 무변형 보존.
- **디자인**: `.subscribe`/`.sub-form` 은 추가 컴포넌트(v6 프로토타입에 없던 밴드) — 디자인 토큰
  (`--coral`/`--soft`/`--line-2`/`--rad-s`)·`.btn.coral` 재사용, 입력 포커스는 `.searchbar input` 동형.
  **§4 한글 안전**(문안 자간0·대문자/mono 없음, 영문 `.kick` eyebrow 만). 검증 = `test_render.py::
  test_newsletter_form_conditional`(off=부재·on=action/email-only/PII 0/한글안전·비http fail-safe).
## 뉴스레터 발송 (T1.3 — `newsletter.py` + `grm-newsletter-send.yml`)
발행된 주차 web-card JSON 1건 → **티저 메일**(tldr + "전체 보기" + 섹션 `#sec-{그룹}` 앵커 +
면책 캐논)을 만들어 관리형 SaaS(**Brevo** Campaigns API)로 발송. 풍부한 카드는 웹에서 본다.
수집/배포와 **완전 별도**(별도 파일·트리거·`permissions: contents:read`·D8).
```bash
python web/newsletter.py --publish-date 2026-06-26 --mode validate --no-linkcheck   # 게이트만(오프라인)
python web/newsletter.py --publish-date 2026-06-26 --mode validate --out mail.html  # +링크체크·메일 HTML 산출
python web/newsletter.py --publish-date 2026-06-26 --mode test   # 테스트 발송(GRM_NEWSLETTER_TEST_EMAILS)
python web/newsletter.py --publish-date 2026-06-26 --mode send   # 실발송(멱등 → 캠페인 생성·sendNow)
```
- **발송 게이트 3겹**(되돌릴 수 없는 발송): ① 발행검증=`run_gates`(구조: 스키마·
  발행일·카드·면책 + provenance: 메일이 우리 페이지만 링크·추적 파라미터 0) — 무거운 Brief Lint/
  handoff provenance 는 발행 시점 Routine 에서 이미 실행, 여기선 web-card 무결성 재확인 ② 링크체크
  **승격**=`linkcheck.py` 재사용(broken→발송 **보류**, degraded/KR-egress 스킵은 비차단) ③ 멱등=
  캠페인명(`publish_date` 파생) 키(`find_campaign`→이미 발송 호 재발송 0). Admin 콘솔의 단일 Admin
  인증·감사 로그·중복 발송 차단을 운영 승인 경계로 삼아, Admin 버튼은 GitHub 추가 승인 없이 실발송
  워크플로우를 끝까지 실행한다.
- **무변형/provenance**: 메일은 `tldr`(verbatim)·섹션명·**우리 사이트 링크**만. 카드 사실·원문 인용·
  **카드 출처 URL(provenance 보호 대상)은 메일에 안 들어간다** — 딥링크는 `SITE_BASE_URL` 의 우리 페이지·
  `#sec-{그룹}` 앵커뿐(추적 파라미터 0). 클릭 추적은 SaaS 가 발송 시점에 자기 도메인으로 래핑 →
  우리 산출 URL·`web/data/briefs/*.json` 불변.
- **결정론**: 같은 입력 → 같은 subject·HTML(`now()`/난수 0). 제목=발행일+호수(요일 미표기 — web JSON
  에 `weekday_kst` 없음·산술 금지 클래스 차단). 면책 캐논(KO+EN)은 `brief.html` 과 동일 문안(drift 가드 테스트).
- **SaaS 격리**: 발송 API 는 `NewsletterSender` 인터페이스 뒤(`BrevoSender` 구현). MailerLite/Mailchimp
  등 교체 시 인터페이스 구현만 추가. `requests` 는 `BrevoSender` 안에서 지연 import(코어 순수성 보존).
- **사람 후속**: SaaS(Brevo) 가입 → `NEWSLETTER_API_KEY`(Secret, UI 직접 등록)·`GRM_NEWSLETTER_LIST_ID`/
  `SENDER_EMAIL`/`SENDER_NAME`/`TEST_EMAILS`(Variables)·발송 도메인 SPF/DKIM/DMARC·Environments
  `production` reviewer. (2026-06-30 vars·폼 action·도메인 인증 완료 — API 키 UI 등록·첫 발송만 잔여.)

## 카드 반응 계층 (S1 — 하트·스크랩·회원, env-gated)
`web/assets/reactions.js` + `web/migrations/001_reaction.sql`. **B안**: 브라우저가 `supabase-js` 로 Supabase 를
직접 호출(뉴스레터 Brevo 직접 POST 선례 동형)하고 **RLS** 가 "본인 반응만" 을 DB 레벨에서 강제 → **엣지 함수·
런타임 서버 0**(불변식 #3 반응 층 예외에 부합).
- **env-gate**: `SUPABASE_URL`·`SUPABASE_ANON_KEY`(repo Variables·공개값·anon key=publishable·RLS 로 보호) 둘 다
  설정 시에만 `render.py` `reactions_enabled=True` → `base.html`/`card.html` 반응 블록 출력. 미설정(기본·테스트)=
  전 페이지 골든 byte-diff 0(뉴스레터 form_action 선례 동형).
- **비골든·PE**: 카운트·내상태·로그인은 런타임 클라이언트 주입(콘텐츠 골든/결정론 불침범). 로드/로그인/네트워크
  실패해도 정적 카드 열람·공유(S0) 무영향.
- **provenance/PII**: 백엔드는 불투명 `card_id`(=card.anchor)만 — 카드 사실·원문 URL 미전송. email·신원 PII=
  Supabase 소유(뉴스레터 원칙 정합). service_role 키 미배선(anon key 만·RLS 로 보호).
- **사람 선행작업**: Supabase 프로젝트·URL/anon key repo Variables·Auth 매직링크+Redirect URL·`001_reaction.sql`
  실행. 상세 설계=`GRM_웹_S1_회원반응_Supabase_구현설계_2026-07-03.md`.
- **내 스크랩 페이지(`/me`)**: 반응 활성 시에만 빌드되는 정적 셸(`templates/me.html`). 로그인 사용자의 스크랩
  `card_id` 를 Supabase 에서 가져와 `search-index.json`(card_id→제목·기관·링크)으로 풀어 호를 넘나드는 목록을
  런타임 렌더(reactions.js). sitemap/canonical 제외(비색인·개인화). 헤더 로그인 상태 옆 "내 스크랩" 링크(런타임).

## Admin 운영 콘솔 (A1 — `/admin`, 단일 Admin)
`SUPABASE_URL`·`SUPABASE_ANON_KEY` 가 설정된 프로덕션 빌드에서만 `/admin/index.html` 이 생성된다.
robots.txt 는 `/admin/` 을 비색인 처리한다. 최초 Admin 이메일은 `yeomminho1472@gmail.com` 단일 계정이다.

- **접근 제어**: 브라우저는 Supabase Auth 로그인만 수행한다. 실제 권한은 DB `public.admin_user`
  + `private.is_admin()` 에서 확인한다. `202607050001_admin_ops.sql` 은 해당 이메일의 Auth 사용자가 존재하거나
  새로 생성될 때 자동으로 `Admin` 권한을 bootstrap 하고, `20260705033033_admin_ops_security_definer_hardening.sql`
  은 Admin helper RPC 노출을 차단한다.
- **기능**: 뉴스레터 실발송(`grm-newsletter-send.yml` `mode=send`), 웹 재배포, 수집 실행, 브리프 감사,
  Brevo 구독자 조회/추가/리스트 제거, Supabase Auth 회원 조회/인증/차단/차단해제, 반응 인사이트, 감사 로그,
  운영 준비도 점검.
- **실발송 보호**: Admin 버튼은 테스트 발송 없이 실제 `workflow_dispatch` 를 호출한다. 단, 같은 `publish_date`
  의 성공 요청(`github_status` 2xx)은 재요청을 차단하고, GitHub가 생성한 실제 run URL/id/status 를
  `newsletter_dispatch_log` 에 기록한다.
- **상태 진단**: 로그인 전 readiness 패널은 Edge Function 404(미배포), 500(시크릿/서버 설정), 401/403(배포됨·인증 대기)
  를 구분한다. 로그인 후 시스템 탭은 Supabase DB 테이블, GitHub workflow 존재 여부, Brevo 리스트/API 상태를 `health`
  엔드포인트로 확인한다. 백엔드 미배포 또는 런타임 오류 시에는 Edge Function secrets 와
  `GRM Admin Backend Deploy` 상태를 함께 점검하도록 안내한다.
- **백엔드**: `supabase/functions/admin-supabase`, `admin-github`, `admin-brevo`. 모든 함수는
  `verify_jwt=false` 로 배포하되, 함수 내부에서 `Authorization: Bearer <Supabase session>` 을 검증하고
  Admin 권한을 재확인한다. service role·GitHub PAT·Brevo API key 는 Edge Function secrets 로만 둔다.
- **배포**: `.github/workflows/grm-admin-backend-deploy.yml` 이 DB migration, Edge Function secrets, function deploy 를
  담당한다. 필요한 GitHub Secrets: `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`,
  `SUPABASE_SERVICE_ROLE_KEY`, `ADMIN_GITHUB_ACTIONS_TOKEN`(Actions write 가능한 fine-grained PAT),
  기존 `NEWSLETTER_API_KEY`. `SUPABASE_PROJECT_REF` 는 Secret/Variable 로 두거나 `vars.SUPABASE_URL`
  에서 자동 파생된다.
  워크플로우는 Edge Function `deno check` 를 먼저 수행하고, 시크릿 누락으로 skip 될 때는 GitHub Actions Step Summary 에
  구성/누락 항목을 표로 남긴다.

## 범위 (P3·P4)
✅ (P3) 링크체크·배포 Action(Cloudflare)·승인→라이브 게이트·입력 배선 규약.
✅ (P4) 아카이브 교차검색+facet(정적 클라이언트사이드·search-index.json·progressive enhancement)
   · 상세 document_id 앵커 · v2 네비/모션(사이트 전체·reduced-motion) · 전 골든 재동결.
⛔ Notion→JSON→commit 자동 파이프(후속) · 다크밴드 stale 플래그(후속) · Notion 병행→단순화(Cowork+사람 관찰).
