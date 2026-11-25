# 해외 기준 조항 국문 참고 번역 지침 (2026-11-20)

21 CFR·EU GMP·ICH 조항 원문을 한국어로 옮긴다. 독자는 국내 제약 품질·GMP 실무자다.
세션·Codex·티로 모두 이 지침 하나를 쓴다. 검사: `py -3.12 -X utf8 clause_translate_kit.py qa <파일>`.
**이 지침 말고 다른 문서(GRM_SYSTEM.md 등)는 읽지 않는다.** 필요한 것은 작업 묶음 파일과 이 지침뿐이다.

## 왜 하나
국문 사이트의 조항 페이지 1,230장(21 CFR 236·EU GMP 579·ICH 415)이 영어 원문만 보여 줘서 구글에서 뺐다.
번역이 main 에 들어가면 다음 빌드에서 그 페이지가 국문 본문을 갖고 구글 색인으로 자동 복귀한다
(국문 300자 이상일 때. 짧은 조항은 번역돼도 '짧은 원문' 규칙으로 계속 빠진다).
시범 20조항에서 국문은 원문의 0.43배 길이였다 — 원문 약 700자 이상(1,230 중 약 320)만 번역 뒤 돌아온다.
그래서 `export` 는 그런 긴 조항을 먼저 내보낸다. 나머지 약 900장은 번역해도 300자 규칙에 걸린다(기준 조정은 대표 결정).

## 입력과 출력
- 작업 묶음: `clause_translate_kit.py export --out <폴더>` 가 만든 `CL-0001.json` 등. 항목은
  `{"id", "sha", "code", "title", "text"}` 배열이다.
- 출력: 같은 파일에 `"title_ko"`(원문 제목이 있을 때 필수)와 `"text_ko"` 를 더해
  `translations/clause_outbox/<같은 이름>.json` 으로 낸다. `code`·`title`·`text` 는 지워도 된다(검사기는 정본 원문과 대조한다).
- `id`·`sha` 는 **글자 하나도 바꾸지 않는다.** 원문이 바뀌면 해시가 달라져 번역이 자동으로 버려지고 다시 큐로 간다.
- 같은 조항이 여러 파일에 있으면 이름순 뒤 파일이 이긴다. 재번역은 기존보다 뒤에 오는 이름을 쓴다.

## 번역 규칙
1. **문단은 원문과 같은 수로.** 원문 문단은 빈 줄(`\n\n`)로 나뉜다. 번역도 같은 자리에서 빈 줄로 나눈다.
   문단을 합치거나 쪼개지 않는다. 요약하지 않는다. 모든 문장을 옮긴다.
2. **문단 머리 번호는 원문 그대로** 문단 맨 앞에 둔다: `(a)` `(1)` `(i)` `1.2` `4.10` 등.
3. **숫자와 인용은 원문 글자 그대로.** `§ 211.80`·`§§ 211.180 and 211.182`(→ `§§ 211.180 및 211.182`)·`40 CFR part 141`·
   `7 U.S.C. 135`·`ICH Q9`·`Annex 1`·`Chapter 4` 의 표기를 바꾸지 않는다(`§`→`제…조` 로 바꾸지 않는다).
   연도·백분율·온도·숫자 범위도 그대로.
4. **대문자 약어·코드는 그대로**: API, GMP, CGMP, HEPA, CAPA, FDA, ISO 14644-1, Q10 등.
   필요하면 처음 한 번 우리말을 덧붙인다(예: "고효율 미립자 공기(HEPA) 필터").
5. **법령 문체**: "shall" 은 "~하여야 한다", "shall not" 은 "~하여서는 아니 된다", "may" 는 "~할 수 있다".
   EU GMP·ICH 의 "should" 는 "~하여야 한다"가 아니라 "~해야 한다/~하는 것이 바람직하다" 가운데 원문의 강도대로
   ("should" = 권고 → "~해야 한다"로 통일). 해설·의견·괄호 보충 설명을 덧붙이지 않는다.
6. **용어는 용어사전(`web/data/glossary.json` 의 term_ko)을 따른다.** 자주 나오는 것(검사기가 일부를 확인한다):

| 원문 | 번역 |
|---|---|
| quality control unit | 품질관리부서 |
| quality unit | 품질부서 |
| component(s) | 원료 |
| drug product | 의약품(완제 의미가 분명하면 완제의약품) |
| drug product container / closure | 의약품 용기 / 마개 |
| in-process material(s) | 공정 중 물질 |
| labeling | 표시자재 |
| identity, strength, quality, and purity | 확인, 함량, 품질 및 순도 |
| manufacture, processing, packing, or holding | 제조, 가공, 포장 또는 보관 |
| written procedures | 서면 절차 |
| batch / lot | 배치 / 로트 |
| batch production (and control) record | 배치 생산(및 관리) 기록 |
| master production and control record | 마스터 생산·관리 기록 |
| deviation | 일탈 |
| investigation | 조사 |
| validation / validated | 밸리데이션 / 밸리데이션하다 |
| qualification | 적격성평가 |
| calibration | 교정 |
| specification(s) | 규격 |
| quarantine | 격리 |
| reprocessing / reworking | 재가공 / 재작업 |
| yield / theoretical yield | 수율 / 이론 수율 |
| sanitation | 위생관리 |
| contamination / cross-contamination | 오염 / 교차오염 |
| aseptic processing | 무균공정 |
| sterile / sterilization | 무균 / 멸균 |
| pyrogen / depyrogenation | 발열성물질 / 발열성물질 제거 |
| audit trail | 감사추적 |
| data integrity | 데이터 완전성 |
| change control | 변경관리 |
| corrective and preventive action (CAPA) | 시정 및 예방조치(CAPA) |
| quality risk management | 품질위험관리 |
| pharmaceutical quality system | 의약품 품질시스템 |
| stability | 안정성 |
| complaint / recall | 불만 / 회수 |
| self-inspection | 자율점검 |
| outsourced activities | 위탁활동 |
| current good manufacturing practice | 현행 의약품 제조 및 품질관리기준(CGMP) |

7. 한자를 쓰지 않는다. 영어 낱말을 한글로 음차하지 않는다(밸리데이션·바이오버든 같은 업계 정착어는 예외).
   번역하지 않은 영어 문장을 남기지 않는다. 기관·법령의 고유 이름은 우리말로 옮기고 처음 한 번 원문을 괄호에 둔다
   (예: "연방 살충제·살균제·살서제법(Federal Insecticide, Fungicide, and Rodenticide Act)" — 괄호 안 영어는 검사에서 제외되지 않으니
   5낱말 이상이면 따옴표로 감싼다).
8. 제목(`title_ko`)은 원문 제목을 옮긴 짧은 명사구(끝 마침표 없음).

## 검사 (`web/clause_translation.py` `check` — CI `tests/test_clause_translation.py` 가 outbox 전체를 돌린다)
한글 있음·한자 없음 / 문단 수 같음 / 문단마다 숫자 전부 있음 / 문단 머리 번호 같음 / 인용(§·CFR·U.S.C.·FR·ICH Q·Annex·Chapter)
글자 그대로 / 대문자 약어 그대로 / 길이 비율 0.25~1.1(문단 120자 이상이면 문단마다 0.2 이상) / 용어(위 표의 일부) /
번역 안 된 영어 문장 없음 / 제목이 있으면 `title_ko` 있음. 하나라도 걸리면 그 항목은 실격이고 CI 가 빨갛게 된다.

## 처리 방식
- 채우는 쪽: 세션 하위 에이전트 또는 Codex(구독 경로). 묶음 20조항(약 1.6만 자) 단위.
- 한 PR 에 묶음 여러 개를 넣어도 된다. PR 의 CI 가 검사하고, 머지되면 다음 배포부터 국문 페이지에 번역이 실린다.
- 현황: `clause_translate_kit.py status` (done·failed·stale·pending).
