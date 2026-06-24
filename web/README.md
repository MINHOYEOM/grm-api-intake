# GRM 웹 렌더러 (P2)

`grm-web-card/v1` JSON → **정적 멀티페이지 사이트**(랜딩·아카이브·브리프 상세).
순수·결정론 빌더. 디자인 계약 = `GRM_웹_프로토타입_v4.html`(CSS 는 `assets/grm.css` 로 동결 추출).

> **헤드리스 분리:** routine(수집 시스템)은 **데이터(JSON)만** 만들고, "그리는 일"은 이 렌더러가 맡는다.
> 렌더러는 JSON 값을 **그대로** 출력한다 — 사실·URL·숫자·업체명 재생성/교정/번역 **금지**.

## 구조
```
web/
├─ render.py            # 빌더(순수): data/briefs/*.json → dist/
├─ templates/           # base · landing · archive · brief (Jinja2, autoescape on)
├─ partials/card.html   # 카드 1장 (grm-web-card/v1 card → v4 카드 마크업)
├─ assets/grm.css       # GRM_웹_프로토타입_v4.html 의 <style> verbatim 추출(주석 없이 v4 본문 그대로). 손으로 편집 금지 — 디자인 변경은 v4 갱신 후 재추출
├─ data/briefs/*.json   # 입력(주차별 1파일). 현재 = 실 6/22
├─ tests/
│  ├─ test_render.py    # 골든·결정론·무변형·escape·순수성
│  ├─ golden/           # 동결 기대 HTML (byte-diff)
│  └─ fixtures/multi/   # 합성 2건(06-08 산문·번역 / 06-15 병합) — 멀티 골든용
└─ dist/                # 빌드 산출(정적). git 비추적
```

## 빌드 · 미리보기 · 골든
```bash
python web/render.py                         # web/data/briefs → web/dist/
python web/render.py --data DIR --out OUT     # 임의 입력/출력
python web/tests/test_render.py --freeze      # 골든 (재)동결
python -m unittest discover -s tests          # 전체 스위트(웹 포함, CI 와 동일)
```
미리보기는 `web/dist/index.html` 을 열어 확인. 라이브 배포·호스트 preview·승인→라이브
게이트는 **P3**(별도 GitHub Action).

## 불변식 (깨지 말 것)
1. **순수 렌더** — JSON 값 무변형. 렌더러 보유 텍스트는 정적 카피(템플릿)+면책 캐논(brief.html)뿐.
2. **결정론** — 같은 JSON → byte 동일 HTML. `datetime.now`/난수 0, 정렬은 입력 파생, autoescape on, 출력 항상 LF/UTF-8.
3. **정적·$0** — 외부 fetch 0, 런타임 서버 0. 폰트는 v4 동일 CDN.
4. **멀티페이지** — 라우트별 개별 HTML. 링크는 페이지 깊이별 상대경로(호스트 무관).
5. **디자인 = v4** — `assets/grm.css` 수정 금지(디자인 변경은 v4 갱신 후 재추출). 한글에 mono 금지.

## 빈 슬롯 · KO · 링크 상태 처리
- **빈 LLM 슬롯**(title_issue·summary·key_facts·implication·checks·tldr·번역): 빈 값이면 해당 블록/줄 **생략**. 실 6/22 는 산문이 전부 빈 placeholder → 코드 필드만 렌더되는 상태가 정상(구조 골든으로 유효).
- **KO 인용**: `translation == null|""` → 번역 줄 생략(원문만). 비KO 번역이 있으면 ①② 인터리브.
- **링크 상태**(`sources.link_check`): `ok|pending`→정상, `degraded`→⚠️, `broken`→"일시 접근불가"(클릭 비활성). P1 은 `pending` 고정, 실제 200 체크 주입은 **P3(D7)**.

## 스키마 v1 한계 → 결정론 파생 (v1.1 후보)
- **issue 번호**: 스키마에 없음 → `data/briefs` 의 `publish_date` 오름차순 순위(가장 오래된=1).
- **브리프 제목**: 스키마에 brief 단위 제목 없음 → `tldr[0]` 있으면 사용, 없으면 `publish_date` 파생 "{Y}년 {M}월 {N}주차".

## 범위 (P2)
✅ 렌더러·CSS 추출·렌더 골든·로컬 미리보기. ⛔ 배포 Action·링크체크(200)·승인 게이트(P3) · 아카이브 필터/검색 동작(P4, 칩은 시각만).
