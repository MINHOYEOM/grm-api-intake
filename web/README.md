# GRM 웹 렌더러 (P2)

`grm-web-card/v1` JSON → **정적 멀티페이지 사이트**(랜딩·아카이브·브리프 상세).
순수·결정론 빌더. 디자인 계약 = `GRM_웹_프로토타입_v4.html`(CSS 는 `assets/grm.css` 로 동결 추출).

> **헤드리스 분리:** routine(수집 시스템)은 **데이터(JSON)만** 만들고, "그리는 일"은 이 렌더러가 맡는다.
> 렌더러는 JSON 값을 **그대로** 출력한다 — 사실·URL·숫자·업체명 재생성/교정/번역 **금지**.

## 구조
```
web/
├─ render.py            # 빌더(순수): data/briefs/*.json → dist/
├─ linkcheck.py         # 배포단계 링크체크(P3·C1): link_check enrich. 네트워크는 여기만 — render.py 순수성 보존
├─ templates/           # base · landing · archive · brief (Jinja2, autoescape on)
├─ partials/card.html   # 카드 1장 (grm-web-card/v1 card → v4 카드 마크업)
├─ assets/grm.css       # GRM_웹_프로토타입_v4.html 의 <style> verbatim 추출(주석 없이 v4 본문 그대로). 손으로 편집 금지 — 디자인 변경은 v4 갱신 후 재추출
├─ data/briefs/*.json   # 입력(주차별 1파일). 현재 = 실 6/22. 규약 = data/briefs/README.md(C4)
├─ tests/
│  ├─ test_render.py    # 골든·결정론·무변형·escape·순수성
│  ├─ test_linkcheck.py # 링크체크 모킹 단위테스트(200/404/503/timeout/KR-skip/HEAD→GET)
│  ├─ golden/           # 동결 기대 HTML (byte-diff)
│  └─ fixtures/multi/   # 합성 2건(06-08 산문·번역 / 06-15 병합) — 멀티 골든용
└─ dist/                # 빌드 산출(정적). git 비추적
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

## 범위 (P3)
✅ 링크체크(200·비차단·KR-egress 관대)·배포 Action(Cloudflare)·승인→라이브 게이트·입력 배선 규약.
⛔ Notion→JSON→commit 자동 파이프(후속) · 아카이브 필터/검색 동작·다크밴드 stale 플래그(P4).
