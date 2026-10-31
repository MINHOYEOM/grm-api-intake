# 종합상황실 · 월요일 발행 준비 확인 — 모델 Haiku · 월 10:45 KST

PC 예약작업 `grm-monday-brief-publish` 의 **확인 부분**을 옮긴 것이다. 클라우드는 GitHub Actions 를 부를 수 없고(`company/CLOUD.md`)
심층분석을 새로 쓰는 일은 Haiku 의 몫이 아니므로, **확인·판정·기록만** 한다. 복구(브릿지 재실행·심층분석 백필)는 PC 백업(월 11:30)이 한다 —
이 파일의 결과가 `상태: 완료` 가 아니면 PC 백업이 전체 절차를 돈다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일.

## 값
`D` = 이번 주 발행일 = 오늘(KST) 날짜. `UNDER` = `D` 의 `-` 를 `_` 로. 결과 파일 = 브랜치 `claude/company-$D` 의 `company/run/$D/brief-prep.md`.
Notion Intake data source = `collection://d5b9634a-2bd7-4036-ba06-e4ad17ede288`.

## 확인 (순서대로, 고치지 않는다)
1. **델타 예치** — Notion 에서 `Type or Class`=`web-delta` 이고 이름에 `$D` 가 든 행. `OPEN`(New) 또는 `CONSUMED` 가 있으면 정상.
   행이 없으면 `search` 로 제목 `OPEN GRM Web Delta $D` 를 찾는다 — 부모 없는 독립 페이지면 `★델타 오예치(독립 페이지)`, 그것도 없으면 `★델타 없음`.
2. **main 반영** — `git fetch origin main` 후 `web/data/deltas/delta_$UNDER.json`·`deep_$UNDER.json` 이 있는지.
   델타가 OPEN 인데 파일이 없으면 브릿지가 아직 안 돈 것이다(크론이 13:37 까지 30분마다 돈다) → `대기: 브릿지`.
3. **발행 PR** — 열린 PR 중 head 가 `publish/brief-$D` 인 것. head sha 의 check-runs 가 전부 `success` 인지.
4. **심층분석** — PR 브랜치의 `web/data/briefs/brief_web_$UNDER.json` 을 `git show` 로 읽어, `"deep_analysis" in card` 인 카드마다
   ① `deep_analysis` 가 비어 있지 않은가 ② `fda-483` 카드는 `observations_ko` 의 `number` 가 `deterministic_detail.observations[].number` 와 1:1 이고
   `detail` 이 있는 관찰마다 `detail_ko` 가 있는가. 파이썬 몇 줄로 센다(눈으로 판정하지 않는다).

## 판정
- 1~4 가 전부 정상 → `상태: 완료` · `대표가 볼 것: 관리자 사이트에서 발행 승인`.
- 하나라도 아니면 → `상태: 보류` · 무엇이 빠졌는지(★델타 없음/오예치 · 대기: 브릿지 · 발행 PR 없음·CI 실패 · deep 누락 카드 id·층) 한 줄씩.
  `★델타 없음` 이면 `대표가 볼 것: claude.ai 에서 백업 Routine '지금 실행'` 을 쓴다.

## 기록
`git fetch origin claude/company-$D` — 있으면 그 위에, 없으면 `origin/main` 에서 브랜치를 만든다. `company/run/$D/brief-prep.md` 하나만 쓰고 커밋·push.
첫 3줄은 `CHARTER.md` 형식(`상태:` 로 시작) — PC 백업이 이 첫 줄을 읽고 건너뛸지 정한다. 다른 직무의 파일은 건드리지 않는다.

## 금지
발행 PR 머지 · Notion 쓰기(오예치 이동도 PC 백업이 한다) · 심층분석 생성 · Supabase 쓰기.
