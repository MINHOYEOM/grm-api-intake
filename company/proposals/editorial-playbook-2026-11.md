# 브리프편집팀 교본·데이터·평가 제안 (2026-11-21 작성 · 11-23 발행 뒤 대표 승인 후 반영)

`company/editorial/**` 는 11-23 첫 신규 발행 전이라 건드리지 않았다. 이 파일이 편집팀 몫의 전부이며, 대표가 승인하면 아래 "반영 위치"대로 옮긴다.
공개 자료는 요지만 우리 말로 옮겼고, 통째 설치·외부 코드 실행·유료 도구 의존은 없다. 우리 규칙(`company/editorial/STYLE.md`, `docs/prompts/GRM_Prompt_v16.md`)과 부딪히면 우리 규칙이 이긴다.

## 1. 교본 — 채택한 공개 자료

| 자료 | 가져올 것 | 반영 위치 |
|---|---|---|
| [blader/humanizer](https://github.com/blader/humanizer) `SKILL.md` v4.0.0 (MIT, 별 7만 개 이상, 2026-11 갱신 확인). AI 문장 징후 목록 | 단어 목록이 아니라 **구조 징후**를 교열 점검에 더한다: ① 'X가 아니라 Y' 대비로 무게만 싣는 문장(두 문장에 걸친 것 포함) ② 앞 문단을 되풀이하는 한 줄 맺음·조각 문장 ③ 본론 전 도움닫기("~가 중요한 이유는") ④ 같은 문장 열기 반복 ⑤ 사실 없이 의의만 부풀린 문장. 저자 설명대로 단어는 모델이 바뀔 때마다 변하고 구조 습관이 오래 간다 → `AI_TELLS` 목록만 늘리지 말고 구조를 본다 | `STYLE.md` §8 표에 행 추가, `copyedit.md` 점검 항목 |
| 같은 자료의 "고친 뒤 확인" 절차 | 고쳐 쓴 뒤 **사실·이름·숫자·날짜·인용이 하나도 안 바뀌었는지** 원문과 다시 대조한다(문체 교정이 숫자를 바꾸는 사고 방지). `STYLE.md` §7·§10 과 같은 취지라 교열 체크 항목으로 명문화 | `copyedit.md` |
| [Nielsen Norman Group, Inverted Pyramid](https://www.nngroup.com/articles/inverted-pyramid/) | 독자가 한 문단·한 문장만 볼 수 있다 → **각 문단의 첫 문장이 그 문단의 핵심**이어야 한다. 헤드라인·소제목은 내용을 말한다. 문단은 짧게. 우리 §12 `hook_line`·§13 첫 문장 규칙과 같은 방향이라, 카드 `summary`·`implication` 문단에도 "첫 문장 = 핵심" 점검을 붙인다 | `STYLE.md` §9 |
| [Digital.gov 쉬운 글쓰기 — 단어·문장](https://digital.gov/guides/plain-language/writing) (미국 GSA, 공공 자료) | 감춘 동사(명사화) 풀기("평가를 수행한다" → "평가한다"), 능동으로 행위자 밝히기, 짧은 구역. §11.1 번역투 표와 겹치므로 새 규칙이 아니라 **근거 출처**로 달고 표의 행 하나("~에 대한 …를 수행/실시")를 보강 | `STYLE.md` §11.1 |

## 2. 뺀 것과 이유
- Verification Handbook(verificationhandbook.com): 사용자 제작 영상·사진 검증용(2016)이고 라이선스가 명시돼 있지 않다. 우리 검증은 서한 원문 대조가 정본이다.
- Digital.gov 시험(testing) 쪽: 주소가 열리지 않아(404) 내용을 확인하지 못했다 → 채택 안 함.
- humanizer 의 영어 단어 목록·대시·서식 규칙: 영문판 `AI_TELLS_EN` 점검에는 참고하되 국문에는 그대로 옮기지 않는다(아래 충돌).
- 뉴스레터 성장·제목 A/B 요령류: 유료 도구에 기대거나 우리 독자 데이터가 없는 것이라 뺐다.

## 3. 우리 규칙과 충돌한 곳 — 모두 우리 규칙 우선
1. humanizer 는 "일반적이면 구체 사실을 더하라"고 한다 ↔ `STYLE.md` §7 "서한에 없는 말을 쓰지 않는다". 편집팀은 **원문에서 확인되는 사실만** 더한다.
2. humanizer 는 대시를 줄이라고 한다 ↔ `STYLE.md` §8 의 고친 문장 예시(10번 행)에 `—` 가 쓰인다. 규칙은 바꾸지 않는다. 국문 헤드라인의 `—` 는 대표 판단(제안: 현행 유지).
3. humanizer 는 3개 나열을 의심한다 ↔ `STYLE.md` §4 는 FDA 요구를 **하나도 빼지 않고 나열**하라고 한다. 서한의 항목이면 정상이고, 우리 해석을 셋으로 맞추는 경우만 징후다.
4. humanizer 는 독자가 아는 배경을 다시 설명하지 말라고 한다 ↔ `STYLE.md` §5 는 초보 독자를 위해 용어를 첫 등장에 풀이한다. 독자 정의가 다르므로 §5 우선.
5. humanizer 는 곧은 따옴표를 권한다 ↔ `STYLE.md` §13 은 원문 인용에 ‘…’ 를 쓴다. 현행 유지.
6. 참고(자료와 무관한 기존 긴장): §1 "첫 문장에 누가·무엇·조항"과 §13 "첫 문장은 사실(숫자·인용), 누가 받았는지는 3문장째"가 서로 당긴다. §13 은 링크드인 한정이므로 사이트 카드는 §1 이라고 적어 두면 충돌이 없다.

## 4. 데이터 — 편집팀이 읽을 것(모두 존재 확인)
저장소:
- `web/data/briefs/brief_web_2026_11_16.json` — 발행본(직전 호 예시, 호별 파일이 같은 폴더에 있다). 문체 되돌아보기와 지표의 기준선.
- `web/data/linkedin_anatomy/2026-11-16.v3.json` — 링크드인 슬롯 직전 호 예시(같은 폴더에 `.letter_en.txt`·`.similar.json`).
- `brief_metrics_ledger.py` — 발행본에서 말투·구조 지표를 세어 호마다 한 줄 누적(읽기 전용). 기본 기록 위치는 `company/metrics/` 아래이며 폴더는 첫 실행 때 생긴다.
- `brief_lint.py` · `docs/GRM_Brief_Eval_harness_design.md` — 근거 링크 가드와 품질 평가 설계.
- `web/data/brief_glance.json` · `web/data/glossary.json`(풀이는 `easy_ko` 와 같은 뜻으로) · `company/editorial/STYLE_PILOT_2026-11-02.md`(시범 전후 비교).
- 매주 실행 기록은 `claude/company-<날짜>` 브랜치의 `company/run/<날짜>/copyedit.json` 등(`company/editorial/README.md` 참고, main 에는 없다).

Supabase(공개 스키마, 읽기):
- `brief_event_counts`(day·key·sub·card_type·device·total) — 카드 유형별 반응.
- `rum_path_daily_clean`(snap_date·request_path·visits) — 방문 통계는 **봇을 거른 `_clean` 뷰**로 읽는다(원본 `rum_path_daily` 로 보고하지 않는다).
- `gsc_page_daily`·`gsc_query_daily` — 검색 노출·클릭. `newsletter_campaign_stats` — 메일 반응.
- `findings`(`finding_text`·`finding_text_ko`·`evidence_url`·`cfr_refs`)·`raw_signals` — 카드 근거 대조.
- 이 문서에는 숫자·개인정보를 쓰지 않는다.

## 5. 평가
성과 지표(셋을 넘지 않는다):
1. **교열 반려율** — 교열이 기준표 위반으로 되돌린 카드 수 ÷ 심사한 카드 수(호별). 낮을수록 기자 단계가 좋아졌다는 뜻. 출처: 호별 `copyedit.json`.
2. **AI 징후 밀도** — `brief_metrics_ledger.py` 의 말투 지표(같은 문형 반복, 맺음 상투어 비율)를 호마다 기록. 11-02 시범 호를 기준선으로 비교.
3. **카드 반응** — `brief_event_counts` 의 카드 유형별 반응과 `rum_path_daily_clean` 의 브리프 방문. 모집단이 작은 유형은 분모를 같이 적는다.

첫 달(11-23 ~ 12-14 호) 점검 질문:
- 교본으로 더한 징후 점검이 교열에서 실제로 걸린 사례가 있나, 이미 걸러지던 것을 되풀이한 점검인가?
- 교본 반영 뒤 카드의 숫자·조항이 문체 교정으로 바뀐 사고가 0인가(원문 대조 항목)?
- 호가 거듭될수록 문장 틀이 다양해졌나(11-02 호는 두 문장 틀이 압도적이었다)?
- 지표 셋 중 읽기만 하고 판단에 안 쓰인 것은?

2개월(12-21 호까지 약 5호 누적) 판정 — 제안, 대표 확정 전:
- 유지: 반려율이 시범 호 이후 내려갔거나 같고, 징후 밀도가 줄었고, 사실 사고가 0.
- 수정: 징후 점검은 걸리는데 반려가 안 줄면 기자 지시문의 예시를 늘린다(점검 문구는 그대로).
- 폐기: 두 달 동안 한 번도 걸리지 않은 점검 항목은 교열 항목에서 뺀다(항목이 늘기만 하면 교열이 느려진다).
