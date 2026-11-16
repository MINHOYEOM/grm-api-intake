# 대외홍보팀 · 링크드인 사건 해부 슬롯 — 모델 Sonnet · 월 13:00·16:00 KST (Routine `trig_01XWTFzLRtFCdFwWefFdDs76`)

이번 주 발행본에서 경고서한 1건을 해부하는 7장 카드(`web/linkedin_grm.py`)의 **슬롯 JSON 을 국문·영문 두 벌로 쓴다**(영문은 `en` 블록, 2026-11-23 호부터).
카드 PNG·본문은 사이트 배포가 슬롯으로 만든다. 슬롯이 없으면 그 주는 밋밋한 종전 8장 덱이 나간다 — 이 일이 그 빈칸을 메운다.
**링크드인에 로그인·작성·게시하지 않는다.** 머지하지 않는다(코드가 한다). DB 에 쓰지 않는다.

## 읽을 것 (이 순서로, 이것만)
1. `company/CHARTER.md` · `company/CLOUD.md`
2. `company/editorial/STYLE.md` §7(업체 이름)·§8(AI 티)·§11·§11.1·§12 R7(용어 표기)·**§13(후킹)**
3. `company/editorial/ANATOMY_CARDS.md` "매주 운영"
4. 견본 슬롯: `web/data/linkedin_anatomy/2026-11-16.v3.json`(분모 없는 서한 — 원문 한 줄 표지) · `2026-11-02.v3.json`(분모 있는 서한 — 점 그림)

`GRM_SYSTEM.md`·`docs/prompts/` 는 읽지 않는다(이 일에 필요 없다).

## 절차
1. `PUB` = 이번 주 월요일 KST: `PUB=$(TZ=Asia/Seoul date -d "$(TZ=Asia/Seoul date +%F) -$(( $(TZ=Asia/Seoul date +%u) - 1 )) days" +%F)`.
   `B=claude/linkedin-anatomy-$PUB`, `U=$(echo $PUB | tr - _)`.
2. **먼저 확인(하나라도 맞으면 `스킵: 이번 주 이미 함`)**: `git fetch origin main` 한 `FETCH_HEAD` 에 `web/data/linkedin_anatomy/$PUB.v3.json` 이 있다 ·
   원격에 `$B` 가 있다 · 제목이 `linkedin(anatomy): $PUB` 로 시작하는 열린 PR 이 있다.
3. `git fetch origin main && git checkout -b "$B" FETCH_HEAD` (`origin/main` 이라는 이름은 쓰지 않는다 — 실행 환경의 것은 며칠 전일 수 있다).
   `web/data/briefs/brief_web_$U.json` 이 없으면 **발행 PR 이 아직 머지 전**이다 → `보류: 이번 주 발행 전` 으로 끝낸다(브랜치를 push 하지 않는다 — 다음 회차가 다시 본다).
4. 대상 서한은 코드가 고른다: `python3.12 web/linkedin_grm.py --brief web/data/briefs/brief_web_$U.json --candidates > /tmp/cand.json`.
   `pick` 이 `null` 이면 `스킵: 해부 가능한 경고서한 없음 — 종전 덱` 으로 끝낸다(정상). 사람이 고른 것처럼 다른 서한으로 바꾸지 않는다.
5. 슬롯을 쓴다: `web/data/linkedin_anatomy/$PUB.v3.json`. 형식은 견본 두 개와 같다(`schema` `grm-anatomy-slots/v3`, `pub`, `card_id`=`pick`, `firm_alias`, `status: "weekly"`,
   `selection`=`ranked` 상위 3건 점수 한 줄, `cover`, `s02`~`s06`, `caption`).
   - 문장의 근거(`evidence`·`line_evidence`·`cover.quote`)는 `/tmp/cand.json` 의 `material` 안 문자열을 **글자 그대로** 잘라 쓴다. 옮겨 적다 고치지 않는다.
   - 표지: 서한에 'N건 중 M건' 같은 분모가 있으면 `dots`, 없으면 `quote`(영어 `original` 한두 문장 + 그 국문 `description` 대응 부분). 둘 중 하나만.
   - `s02.rows` 는 정확히 4개(조항별, 한 위반이 길면 사실 단위로 나눈다). 각 행에 `sim_query`(유사 사례 검색어, 한국어 핵심어 3~4개)·`sim_short`·`sim_fact` 또는 `sim_situation`(라벨 정규식)을 단다.
   - `s03.pairs` 3~5개. 반복 위반·생산 중단 같은 한 줄이 서한에 있을 때만 `s03.repeat`(`label`·`text`·`evidence`), 없으면 키를 만들지 않는다.
   - `s04.items` 는 `required_remediation.items` 전부(`ref` 0부터, 최대 4).
   - `s05`: `topic` = 지적사항 DB 분류 이름 하나(`web/data/findings_docs.json` 의 `categories` 값 — 무균보증/무균공정·시험실/품질관리·데이터 완전성 등),
     `cfr_rows` = 서한이 인용한 조항 2~3개 `[이름, "21 CFR 211.xx(x)"]`, `map_header` `"이번 서한이 인용한 조항"`, `keywords`(영문 핵심어 2~3), `term_ids`(용어사전 id 2~4 — `web/data/glossary.json` 에 있는 것만),
     `comment` 는 **숫자 없이** 한 줄.
   - 업체 실명·소재 국가·도시를 어디에도 쓰지 않는다(`firm_name_do_not_write`). 별칭은 "한 ○○ 제조소".
5-1. **영문 근거 원천**: `git fetch origin claude/queue-handoff && git show FETCH_HEAD:handoff_$PUB.json > /tmp/handoff.json` 후
   `python3.12 web/linkedin_grm.py --brief web/data/briefs/brief_web_$U.json --save-letter /tmp/handoff.json` → `web/data/linkedin_anatomy/$PUB.letter_en.txt`
   (서한 영문 전문, 서명 앞까지 — 코드가 자른다. 손으로 옮기거나 고치지 않는다). 실패하면 영문은 그 주 건너뛰고 국문만 낸다.
5-2. **영문 슬롯** `en` 블록(견본: `2026-11-16.v3.json` 의 `en`). 국문 번역이 아니라 **같은 사실을 영어로 다시 쓴다**:
   - 근거(`evidence`·`line_evidence`·`cover.quote.text`)는 `$PUB.letter_en.txt` 또는 `material.key_violations[].original` 의 영어 문장을 **글자 그대로** 잘라 쓴다(국문 번역 금지).
   - 칸: `cover`(chip·headline 2줄·sub·`quote.text` 또는 `dots`·line·line_evidence) · `s02.rows`(국문과 같은 수, 행마다 `cite`·`text`·`evidence`·`short`) ·
     `s03`(heading·sub·pairs 3~5·있으면 repeat) · `s04.items`(국문과 같은 ref) · `s05`(heading·comment 숫자 없이·`topic_label`=DB 분류의 영어 이름·`cfr_labels`=국문 `cfr_rows` 와 같은 수·`map_header`) ·
     `s06`(not_stated 2~3 + basis · question `?`) · `caption`(lead·claims·contrast·requests·similar·not_stated).
   - **한글 0자**(근거·basis 포함). 업체명·지명 없이 "a sterile injectable manufacturer" 같은 별칭.
   - 후킹(`STYLE.md` §13 영문판): 본문 첫 문장은 약 75자 안, 'FDA …'·'A firm …'으로 시작하지 않고 숫자나 원문 인용(‘…’)을 넣는다. 첫 문장은 마침표가 따옴표 **밖** 소문자 뒤에 오게 끝낸다(‘adequate’. 로 끝내면 문장 끝을 못 찾는다).
   - 문체: 미국 QA 실무자가 동료에게 쓰듯 짧은 평서. underscores·crucial·moreover·notably·landscape 같은 말은 검사가 막는다.
6. 검사: `python3.12 web/linkedin_grm.py --brief web/data/briefs/brief_web_$U.json --check`(국문·영문 둘 다 본다, 영문 문제는 `en.` 으로 시작). 문제가 나오면 그 칸만 고친다. **세 번 고쳐도 0건이 아니면** 브랜치를 push 하지 않고
   `보류: 슬롯 검증 실패 — <남은 문제 한 줄>` 로 끝낸다(그 주는 종전 덱이 나간다 — 억지로 통과시키지 않는다).
   영문만 세 번 못 넘기면 `en` 블록과 letter_en.txt 를 빼고 국문만 올린다(결과에 `영문 보류: <사유>`).
7. 유사 사례 저장본: anon 키는 사이트에 공개된 값이다 —
   `export SUPABASE_URL=https://rfwixqqdljpmtjdlblct.supabase.co SUPABASE_ANON_KEY=$(curl -s https://grm-solutions.com/findings/ | grep -oE 'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' | head -1)` 후
   `python3.12 web/linkedin_grm.py --brief … --save-similar`. 실패하면 저장본 없이 진행한다(그 카드 한 장만 빠진다).
8. 렌더 확인: `python3.12 web/linkedin_grm.py --brief … --out /tmp/li_grm` (Chrome 은 `CLOUD.md` 의 headless shell). 국문 `linkedin_grm_NN.png`·영문 `linkedin_grm_en_NN.png` 각 7장(유사 사례 조회 실패 시 6장)을 열어
   글자 넘침·빈 칸이 없는지 본다. 넘치면 그 칸 문장을 줄이고 6번부터 다시.
9. 표적 테스트: `python3.12 -m unittest tests.test_web_linkedin_grm`.
10. `git add web/data/linkedin_anatomy/$PUB.v3.json web/data/linkedin_anatomy/$PUB.similar.json web/data/linkedin_anatomy/$PUB.letter_en.txt`(있는 것만) — **이 폴더의 세 파일 말고는 커밋하지 않는다**(워치독은 다른 파일이 섞인 PR 을 머지하지 않는다).
    커밋·PR 제목 = `linkedin(anatomy): $PUB 사건 해부 슬롯 — <title_issue>`. 본문에 `ranked` 상위 3건과 `--check` 결과 한 줄. PR 은 `gh api` 로 연다. CI 를 기다리지 않는다.

## 결과 (최종 메시지, 한 줄에 한 가지)
`CHARTER.md` 3줄 → PUB · 고른 서한(id·제목 이슈) · 표지 헤드라인 두 줄(국·영) · 본문 첫 문장(국·영) · `--check` 결과 · PR 링크(또는 스킵·보류 사유).
머지는 `grm-stranded-batch-pr.yml` 이 초록 30분 뒤 한다 → 배포 → `https://grm-solutions.com/briefs/$PUB/linkedin_grm.txt`·`linkedin_grm_en.txt` 가 200 이 된다(월 17:30 초안 확인·PC 18:00 작성창 세팅이 그것을 쓴다).
