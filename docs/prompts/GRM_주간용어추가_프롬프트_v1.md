# GRM 주간 용어 추가 — 실행 규율 v1 (2026-09-28)

매주 화요일 예약작업(`grm-glossary-weekly`)이 이 문서를 **정본 규율**로 읽고 따른다. 목적은
용어사전을 주기적으로 넓히는 것이다 — 한 주에 **0~5개**. 좋은 후보가 없는 주는 추가 0이 정상이다
(억지로 채우지 않는다. 사이트·용어집 PDF 에도 "매주 추가"를 약속하지 않는다).

추가된 용어는 다음 배포에서 용어 페이지·색인·sitemap 에 실리고, **GMP 용어집 PDF 도 자동으로 새 판**이 된다
(`web/glossary_pdf.py`, 배포 스텝). 그래서 이 작업의 산출물은 `glossary.json`·`glossary_cases.json`·골든뿐이다.

---

## 0. 선행 가드 — 하나라도 걸리면 "스킵: <사유>"만 보고하고 끝낸다

1. `v15.0-implementation` 에서 `git fetch origin --prune`. 작업은 항상 `origin/main` 기준 새 worktree 에서 한다
   (본체 작업트리에서 직접 작업 금지 — 다른 세션의 미커밋 산출물을 쓸어 담는 사고 선례).
2. 이번 주 키 `YYYYWW`(ISO 주차). `git log origin/main --oneline -- web/data/glossary.json` 에
   `glossary(weekly): YYYYWW` 커밋이 이미 있으면 스킵(이번 주 완료).
3. 원격에 `glossary/weekly-YYYYWW` 브랜치나 그 브랜치의 **열린 PR** 이 있으면 스킵하지 말고 **이어받아 완주**한다
   (앞 회차가 중간에 끊긴 것이다 — 새로 만들면 같은 주에 PR 이 둘 생긴다).

## 1. 후보

```
python glossary_candidates.py --weeks 4 --top 30
```

최근 4호 브리프가 **스스로 괄호로 병기한 짝**(`배지충진(media fill)`, `Summary of Product Characteristics (SmPC)`)
중 사전에 없는 것을 카드 수 순으로 낸다. 후보일 뿐이다 — 아래 기준으로 고른다.

- **싣는다:** 제약 GMP·품질·규제 용어이고, **공식 규제 문서에 정의가 있는** 것.
  공식 문서 = 규제기관 용어집·가이드라인·법령(EU GMP·Annex, ICH, PIC/S, WHO TRS, 21 CFR, FDA Guidance, 식약처 고시·해설서 등).
- **싣지 않는다:** 일반어(`사항`·`사유`·`등급`·`중대`)·업체·제품·기관 이름·보고서 이름·정의를 공식 문서에서 찾지 못한 말.
- 사전에 같은 뜻의 표제어가 이미 있으면 새 용어 대신 그 항목의 `aliases` 에 표기만 더한다(이것도 한 건으로 친다).
- 후보 목록 밖이라도 그 주 브리프에 나온 GMP 용어면 넣어도 된다 — 근거(어느 카드)를 PR 본문에 적는다.

## 2. 항목 작성 — `web/data/glossary.json`

기존 항목과 **같은 필드·같은 형식**으로 파일 끝에 덧붙인다(기존 항목 수정 금지 — `aliases` 추가만 예외).

| 필드 | 규칙 |
|---|---|
| `id` | 영문 kebab-case(`media-fill`). 기존 id 와 겹치지 않게. |
| `term_ko` / `term_en` | 국문 표제어 / 영문 정식명(약어는 괄호). **`term_ko` + 짧은 영문(약어 있으면 약어) ≤ 30자** — 검색 결과 제목 절단 가드. 긴 정식명은 `aliases` 로. |
| `easy_ko` | 공식 정의를 쉬운 우리말 **한두 문장**, `~입니다.` 체. **정의에 없는 내용을 더하지 않는다**(일반론·추론·실무 조언 금지). |
| `easy_en` | `easy_ko` 와 같은 내용의 영어 한두 문장(공식 정의 문장을 그대로 써도 된다). |
| `definition_source` | `문서명 (연도), §조항 제목` — 기존 항목 형식 그대로(`EudraLex Volume 4, Annex 1 (2022), §11 Glossary: First Air`). |
| `source_url` | 그 문서의 **공식** URL(http/https). |
| `related` | 기존 id 1~3개(실재하는 것만). |
| `reg_refs` | 선택. 라벨은 **기존 항목에 이미 쓰인 라벨 형식**을 따른다(자료실 카탈로그와 맞아야 링크가 된다 — 예: `ICH Q1A(R2)` 는 링크가 안 된다). |
| `aliases` | 선택. 다른 표기·긴 정식명. |
| `detail_ko`/`detail_en` | **기본은 넣지 않는다**(2026-09-03 근거 없는 해설층 109건을 지운 결정). 넣는다면 조항 근거 문장만, 두 필드 짝으로. |

- 한국 업체·제품명을 싣지 않는다. 영문에 한국 이름을 로마자로 지어내지 않는다.
- JSON 은 `json.dumps(data, ensure_ascii=False, indent=2) + "\n"` — 기존 파일과 바이트 형식이 같다(쓰기 전 round-trip 확인).

## 3. 사례 결정 — `web/data/glossary_cases.json`

새 id 마다 `items` 또는 `excluded` 에 **정확히 한 번** 넣는다(테스트가 강제한다).

- `items`: `{"id", "q", "findings", "documents"}` — `q` 는 지적사항 본문 검색어. 건수는
  `glossary_cases_refresh.py` 의 `fetch_counts` 로 잰다(화면과 같은 RPC·본문 전용 검색, anon 키는
  `gh variable get SUPABASE_ANON_KEY`, URL 은 `gh variable get SUPABASE_URL`). **실제 지적 문장을 3개 이상 읽어**
  그 용어가 그 뜻으로 쓰였는지 확인한 뒤 넣는다. `q` 는 한 번 정하면 이후 주간 재측정이 바꾸지 않는다.
- `excluded`: `{"id", "term_ko", "reason"}` — 0건이거나, 다른 뜻으로 쓰인 문장이 섞여 링크가 오해를 부를 때.
- 영문 사례 정본(`glossary_cases_en.json`)은 건드리지 않는다(없는 용어는 영문 페이지에서 사례 링크가 빠질 뿐이다).

## 4. 검증 — 전부 green 이어야 PR

```
python glossary_lint.py
python -m unittest tests.test_glossary_lint tests.test_glossary_cases_refresh tests.test_glossary_candidates
cd web/tests && python -m unittest test_render -k lossary        # 용어사전 관련 클래스
python web/tests/test_render.py --freeze                          # 골든 재동결
git diff --numstat -- web/tests/golden
```

- 골든은 **용어가 실리는 파일만** 바뀌어야 한다(`glossary.expected.html`·`sitemap*`·`llms*`·`search-index*`).
  다른 골든이 바뀌면 멈추고 보고한다. 줄 수는 status 개수가 아니라 numstat 으로 본다(CRLF 착시).
- **전체 스위트(약 2시간)는 로컬에서 돌리지 않는다** — CI 가 본다.

## 5. PR

- worktree `..\_wt-glossary-weekly-YYYYWW` · 브랜치 `glossary/weekly-YYYYWW`.
- `git add` 는 경로 명시(`git add -A` 금지): `web/data/glossary.json web/data/glossary_cases.json web/tests/golden/…`.
- 커밋 제목: `glossary(weekly): YYYYWW 용어 N개 — 표제어1·표제어2…` (선행 가드 2가 이 문자열을 찾는다).
- PR 본문: 추가한 용어와 정의 출처, 사례 결정(q·건수 또는 제외 사유), **후보 중 뺀 것과 이유**.
- CI green 을 확인하고 직접 머지 → worktree 제거.

## 6. 보고

`추가 N개: 표제어… · PR #번호` 또는 `이번 주 추가 없음: <사유>`. 실패하면 원인을 적고 멈춘다(강행 금지 —
용어 추가는 급하지 않다. 다음 주에 다시 한다).

## 금지

기존 정의 수정 · 공식 출처 없는 정의 · `GRM_SYSTEM.md`·워크플로·다른 데이터 파일 수정 · Supabase 쓰기 ·
사이트 문구에 "매주 새 용어" 류 약속 추가.
