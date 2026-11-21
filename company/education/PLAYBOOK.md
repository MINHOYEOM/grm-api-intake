# 용어·교육팀 · 교본·데이터·평가 (2026-11-21)

주간 용어(`company/education/glossary-weekly.md`)·용어 그림(`company/education/glossary-fig-daily.md`)·주간 퀴즈(`company/education/quiz-weekly.md`)가 각자 정본 프롬프트를 따른다. 이 문서는 세 직무가 공통으로 **배운 사람이 실제로 남기게 만드는 법**과 **잘했는지 재는 법**을 더한다.
우리 규칙과 부딪히면 정본 프롬프트(`docs/prompts/` 아래 세 문서)가 이긴다.

## 1. 교본 — 채택한 공개 자료

| 자료 | 가져온 것 | 쓰는 곳 |
|---|---|---|
| [Piotr Wozniak, Twenty rules of formulating knowledge](https://supermemo.com/en/blog/twenty-rules-of-formulating-knowledge) (SuperMemo, 학습 카드 설계의 고전; Anki 등이 따름) | **최소 정보 원칙**(한 항목에 한 가지, 질문은 짧고 답은 아주 짧게), **이해 못 하는 것은 외우지 않는다**(먼저 전체 그림, 그다음 부분), **집합·나열은 외우기 어렵다**(5개 넘는 나열은 의미 있는 묶음·순서로 쪼갠다), 그림은 말보다 오래 남는다(만드는 비용을 따져서), **출처와 날짜를 붙인다** | 용어 `easy_ko`(한 용어 = 한 가지 뜻), 그림 채택 질문, 퀴즈 개념 문항 |
| [미시간대 LSA, 좋은 객관식 문항 쓰기(2023)](https://prod.lsa.umich.edu/technology-services/news-events/all-news/teaching-tip-of-the-week/a-refresh-on-writing-quality-multiple-choice-questions.html) (대학 교수법 자료) | 문제문은 **보기를 보기 전에 답할 수 있는 완결 질문**, 부정어("아닌 것")는 피하고 쓰면 강조, 오답은 **정답과 길이·상세도가 비슷**하고 문법 단서가 없다, "모두 맞다/없다" 금지, "맞는 것 모두 고르시오"는 추측을 부른다, 문항끼리 서로 답을 알려주지 않는다. 고차 문항은 **상황·자료를 주고 해석하게** 한다 | 주간 퀴즈 작성·검토 |

## 2. 뺀 것과 이유
- Wozniak 의 빈칸 채우기(cloze)·개인 맞춤 예시·감정 활용: 우리 퀴즈는 4지선다 단일 정답으로 정해져 있고(`quiz_lint.py` 스키마), 용어 풀이는 정의에 없는 내용을 더하지 않는다.
- Mayer 의 멀티미디어 학습 원칙: 조회한 문서가 원칙 목록을 싣지 않아 확인하지 못했다 → 인용하지 않았다. 그림은 지금의 세 질문(`company/education/glossary-fig-daily.md`)을 유지한다.
- Vanderbilt 교수법센터 객관식 가이드: 주소가 다른 페이지로 넘어가 내용을 읽지 못해 뺐다(미시간대 자료로 대체).
- 간격 반복 앱·유료 학습 플랫폼 도구: 비용 0 원칙에 어긋난다.

## 3. 우리 규칙과 충돌한 곳 — 모두 우리 규칙 우선
1. 미시간대: 오답은 학습자가 흔히 하는 오해에서 뽑아라 ↔ `docs/prompts/GRM_주간퀴즈생성_프롬프트_v1.md` §3.4 는 **관련 용어·같은 주제 축 정의에서** 뽑으라고 한다. 같은 축 후보 안에서 "자주 헷갈리는 쪽"을 고르는 데만 쓰고, 근거 없는 오해를 지어내지 않는다.
2. 미시간대: 정답 위치를 고르게 → 우리는 이미 기계 게이트(정답 위치)가 있어 더 강하다. 따로 적용하지 않는다.
3. Wozniak: 일상 예시를 더하라 ↔ `docs/prompts/GRM_주간용어추가_프롬프트_v1.md` §2 "정의에 없는 내용을 더하지 않는다". 예시는 사례 연결(`web/data/glossary_cases.json`)로만 보인다.
4. Wozniak: 외울 때 날짜를 붙여라 ↔ 용어는 `definition_source` 에 문서 연도가 이미 들어 있다. 새 필드는 만들지 않는다.
5. 그림에 관한 "외우기 쉬움" 기준은 우리 세 질문(이해 속도·3초·정확성)과 겹쳐 **정확성이 이긴다** — 쉬워 보이게 구조를 단순화하다 틀린 그림은 올리지 않는다.

## 4. 데이터 — 읽을 것(모두 존재 확인)
저장소:
- 용어: `web/data/glossary.json`(`easy_ko`·`definition_source`·`related`) · `web/data/glossary_cases.json` · `web/data/glossary_cases_en.json` · `glossary_candidates.py`(후보) · `glossary_lint.py` · `glossary_cases_refresh.py`.
- 그림: `web/partials/glossary_fig`(폴더, 용어 id 별 한 벌) · `web/data/glossary_fig_review.json`(다시 볼 것 redo·그리지 않기로 한 것 skip 과 사유) · `glossary_figs.py`.
- 퀴즈: `web/data/quiz_bank.json`(`week`·`source_type`·`source_ref`) · `quiz_lint.py` · `quiz_freshness_check.py` · 최신 `web/data/briefs/` 발행본(사건 문항 근거).
- 정본 프롬프트: `docs/prompts/GRM_주간용어추가_프롬프트_v1.md` · `docs/prompts/GRM_용어그림_일일배치_프롬프트_v1.md` · `docs/prompts/GRM_주간퀴즈생성_프롬프트_v1.md`.

Supabase(공개 스키마, 읽기):
- `rum_path_daily_clean`(snap_date·request_path·visits) — 용어사전·퀴즈 경로 방문. 봇을 거른 `_clean` 뷰로 읽는다.
- `rum_ui_daily_clean`(page_kind·ui_kind·ui_id·ctx·total) — 탭·펼침·토글 클릭 익명 집계.
- `gsc_page_daily`·`gsc_query_daily` — 용어 페이지 검색 노출·클릭·검색어. 일반 단어 검색자는 우리 독자가 아닐 수 있어 클릭률만 보고 판단하지 않는다.
- `finding_term`(finding_id·term_id·surface·confidence) — 용어가 실제 지적사항에 몇 번 쓰였나(사례 연결 근거).
- 사용자별 데이터(구름이 성장 등)는 읽지 않는다. 숫자·개인정보는 이 문서에 쓰지 않는다.

## 5. 평가
성과 지표:
1. **용어 사전 쓰임** — 용어 경로 방문(`rum_path_daily_clean`)과 용어 → 지적사항 사례 연결 여부. 추가한 용어가 사례 연결 또는 제외 사유를 갖는 비율은 정본 프롬프트가 이미 강제한다.
2. **그림 판정 품질** — 채택한 그림 중 나중에 `web/data/glossary_fig_review.json` 의 redo 로 되돌아온 비율. 억지 그림 0이 목표이므로 **채택 수가 아니라 되돌림**을 센다(0장 회차는 정상).
3. **퀴즈 문항 품질** — 새 문항이 `quiz_lint.py`를 첫 시도에 통과한 비율과, 검토자가 모호·복수정답으로 되돌린 문항 수.

첫 달 점검 질문:
- 새 용어의 `easy_ko` 가 한 가지 뜻만 담았나, 한 문장에 두 개념이 섞인 것이 있었나?
- 그림 세 질문에서 '아니오'로 skip 한 사유가 장부에 남았고, 같은 용어를 두 번 판정하지 않았나?
- 퀴즈 오답이 같은 주제 축 안에서 뽑혔고 길이 차이로 정답이 드러나지 않았나(보기 길이 비교)?
- 이 문서의 교본 때문에 늘어난 점검이 회차 시간을 눈에 띄게 늘렸나?

2개월 판정(제안, 대표 확정 전):
- 유지: 그림 되돌림과 퀴즈 반려가 줄거나 0이고 용어 쓰임이 줄지 않았다.
- 수정: 퀴즈 반려가 같은 유형(길이 단서·부정어)으로 반복되면 그 항목을 `docs/prompts/GRM_주간퀴즈생성_프롬프트_v1.md` 에 올리자고 대표에게 제안.
- 폐기: 두 달 동안 한 번도 걸리지 않은 점검은 이 문서에서 뺀다.
