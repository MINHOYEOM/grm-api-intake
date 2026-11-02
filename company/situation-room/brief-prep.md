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
5. **편집팀 작동 여부** — 판정만 하고 `상태:` 판정(1~4)에는 넣지 않는다. 읽기만 한다.
   - 브랜치 `claude/company-$D` 의 `company/run/$D/` 에서 `desk.json` 의 `status`, `reporter.md` 첫 줄 `상태:`, `copyedit.json` 의 `status` 와 `published` 를 본다. 파일이 없으면 "없음".
   - Notion 인계서(제목 `OPEN … $D`, Intake) 의 Status 가 `CONSUMED` 인지 본다. 닫은 쪽은 `copyedit.json` 의 `published: true` 면 교열 마감, 그렇지 않고 `CONSUMED` 면 백업(09:30) 발행으로 본다.
   - 세 파일 중 `완료` 가 아닌 첫 단계(데스크 → 기자 → 교열)와 그 `상태:` 줄의 보류 사유를 적는다.

## 판정
- 1~4 가 전부 정상 → `상태: 완료` · `대표가 볼 것: 관리자 사이트에서 발행 승인`.
- 하나라도 아니면 → `상태: 보류` · 무엇이 빠졌는지(★델타 없음/오예치 · 대기: 브릿지 · 발행 PR 없음·CI 실패 · deep 누락 카드 id·층) 한 줄씩.
  `★델타 없음` 이면 `대표가 볼 것: claude.ai 에서 백업 Routine '지금 실행'` 을 쓴다.
- 편집팀 판정 (5번) — 셋 중 하나만 쓴다. 미작동이 아닌 경우도 원인 한 줄을 붙인다.
  - `작동`: desk·reporter·copyedit 전부 `완료` 이고 `copyedit.json` 의 `published: true`, 인계서를 교열이 닫았다.
  - `부분 작동(멈춘 단계: 데스크|기자|교열|마감)`: 파일 중 하나 이상은 있으나 위 조건이 아니다. 교열이 `완료` 인데 `published` 가 `false` 면 멈춘 단계는 `마감`.
  - `미작동·백업 발행`: 세 파일이 전부 없거나 데스크가 `완료` 가 아니고, 인계서가 `CONSUMED` 로 닫혀 있다(백업이 닫았다). 인계서가 아직 `OPEN` 이면 `미작동·백업 미발행`(대표 확인)로 쓴다.
  - 형식: `편집팀: <판정> — <원인 한 줄>` (원인은 멈춘 단계의 보류 사유, 파일 없음이면 `브랜치·파일 없음`).

## 기록
`git fetch origin claude/company-$D` — 있으면 그 위에, 없으면 `origin/main` 에서 브랜치를 만든다. `company/run/$D/brief-prep.md` 하나만 쓰고 커밋·push.
첫 3줄은 `CHARTER.md` 형식(`상태:` 로 시작) — PC 백업이 이 첫 줄을 읽고 건너뛸지 정한다. 3줄 바로 아래 4번째 줄에 `편집팀: …` 한 줄을 둔다(5번 판정). 다른 직무의 파일은 건드리지 않는다.

## 금지
발행 PR 머지 · Notion 쓰기(오예치 이동도 PC 백업이 한다) · 심층분석 생성 · Supabase 쓰기.
