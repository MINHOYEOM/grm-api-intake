# 그림자 실행 규칙 — 편집팀 3분리 시범 (2026-W45·W46)

그림자 실행 = 실제 발행(현행 월 07:30 Routine, v16 원본)은 그대로 두고, 같은 주 같은 자료로
데스크·기자·교열이 **따로** 초안을 만들어 비교하는 것. **실제 발행 경로에 손대면 안 된다.**
이 파일의 규칙은 직무 지시문과 v16 본문보다 **우선한다.**

## 1. Notion 쓰기 전면 금지 (읽기만)
아래는 실제 발행 Routine 만 한다. 그림자 실행은 **하나도 하지 않는다.**
- Intake row Status 갱신 (Processed/Skipped/Error)
- handoff 마감 (Status→Processed, Title→`CONSUMED ...`), `DEFERRED` 블록 append
- `OPEN GRM Web Delta` / `OPEN GRM Web Deep Delta` 페이지 생성·수정
  (⚠️ 이 페이지를 만들면 브릿지 자동화가 git 에 커밋해 **실제 발행본을 덮을 수 있다**)
- 그 밖의 어떤 Notion 페이지·속성 생성·수정·이동

## 2. handoff 선택 (v16 [0단계] 1·2 와 다르다)
그림자 실행은 실제 발행(07:30)이 끝난 뒤 돌기 때문에 이번 주 handoff 는 이미 `CONSUMED` 다.
- `Type or Class`=`routine-handoff` 인 handoff 를 **Status 와 무관하게** 조회해
  `run_date_kst` 가 가장 큰 1건을 고른다. 그 날짜가 이번 주 월요일(KST)이 아니면
  `상태: 보류` · "이번 주 handoff 없음" 으로 종료한다.
- PL-10b 재유입 대조는 **그다음으로 큰** `run_date_kst` 의 CONSUMED handoff(=지난주)와 한다.
  (가장 큰 것은 이번 주 자기 자신이므로 그것과 대조하면 전 카드가 재유입으로 잘못 빠진다.)

## 3. 결과는 git 브랜치에만
- 브랜치: `claude/shadow-{run_date_kst}` (예: `claude/shadow-2026-11-02`). 없으면 `origin/main` 에서 만든다.
- 폴더: `company/shadow/{run_date_kst}/` 에 직무별 파일만 쓴다. 다른 경로는 건드리지 않는다.
- PR 을 열지 않는다. `main` 에 push·머지하지 않는다.

## 4. 한도는 실제 발행과 같게
- WebSearch 총 9회 · WebFetch 5회 (v16 [1단계]) — 비교가 공정하려면 더 쓰지도 덜 쓰지도 않는다.
- 다른 직무의 결과를 고치지 않는다. 이의가 있으면 자기 파일에 `이의:` 한 줄로 남긴다.
