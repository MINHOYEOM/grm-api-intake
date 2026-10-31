# 종합상황실 · 월요일 운영 점검 — 모델 Haiku · 월 14:00 KST

PC 예약작업 `grm-monday-ops-routine` 중 **클라우드에서 볼 수 있는 것**을 옮긴 것이다. 워크플로 실행 이력(발송·수집 런)은 `/actions` 가 막혀
클라우드에서 볼 수 없다 — 그 항목은 `막힘:` 으로 남기고 PC 백업(월 15:30)이 그 항목만 한다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · 이 파일. 같은 날 `company/run/$D/brief-prep.md`(있으면).

## 값
`D` = 오늘(KST). 결과 파일 = 브랜치 `claude/company-$D` 의 `company/run/$D/ops.md`.

## 점검 (읽기만)
1. **발행 준비** — `brief-prep.md` 의 첫 줄. 없으면 `brief-prep.md` 의 확인 3번(발행 PR·CI)만 직접 본다. 발행 PR 이 없으면 `★발행 PR 없음`.
2. **번역 진행** — Supabase 커넥터(`rfwixqqdljpmtjdlblct`)로 `select public.findings_stats();` → `totals.public_findings` / `totals.findings`.
   지난주 `ops.md`(브랜치 `claude/company-<지난 월요일>`)가 있으면 증가폭. 7일 증가 0 이면 `★번역 정체`.
   최근 7일 `chore(findings): 일일 번역 배치` PR 이 머지됐는지(`pulls?state=closed&per_page=30`).
3. **경고 이슈** — 열린 `automation-warning` 이슈 번호·제목. 지난주보다 새로 생긴 것에 ★.
4. **자동 PR 방치** — 열린 PR 중 제목이 `glossary(weekly):`·`quiz(weekly):`·`chore(findings):` 로 시작하고 만든 지 24시간 넘은 것.
5. `막힘: Actions — 뉴스레터·watchlist 발송 런, intake·backfill 런 확인` 한 줄을 그대로 남긴다(PC 백업이 이 줄을 보고 그 항목만 한다).

## 기록·보고
`company/run/$D/ops.md` 하나만 쓰고(브랜치 사용법은 `brief-prep.md` "기록"과 같다) 커밋·push.
첫 3줄은 `CHARTER.md` 형식. 그 아래 한 줄에 한 가지: 발행 PR · 번역(공개 N, 주간 +M) · 경고 이슈 · 방치 PR · 막힘.
★ 가 있으면 맨 앞에. 정상이면 짧게 끝낸다.

## 금지
머지 · 워크플로 실행 · Notion·Supabase 쓰기 · 코드 수정.
