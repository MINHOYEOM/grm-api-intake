# 종합상황실 · 월요일 운영 점검 — 모델 Haiku · 월 14:00 KST

PC 예약작업 `grm-monday-ops-routine` 중 **클라우드에서 볼 수 있는 것**을 옮긴 것이다. 워크플로 실행 이력(발송·수집 런)은 `/actions` 가 막혀 직접 못 보지만,
`claude/ops-status` 브랜치의 `status.json` 이 대신 알려 준다(`company/CLOUD.md` "Actions 다리"). 그것도 못 읽으면 `막힘:` 으로 남기고 PC 백업(월 15:30)이 그 항목만 한다.

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
5. **Actions 실행** — `git fetch origin claude/ops-status` 후 `git show FETCH_HEAD:status.json`. `generated_at` 이 3시간보다 오래됐거나 파일이 없으면
   `막힘: Actions — ops-status 가 오래됨/없음` 한 줄만 남기고 끝낸다(PC 백업이 이 줄을 보고 그 항목만 한다). 읽히면 `workflows.<파일>` 의 최근 5회로 판정한다:
   - `grm-watchlist-notify.yml` · `grm-newsletter-send.yml` — 이번 주 `event=schedule` 실행이 있고 `conclusion=success` 인가. 없거나 실패면 `★발송 런 없음/실패`
     (뉴스레터는 새 호가 없어 깨끗이 건너뛴 경우도 success 다. 크론 지각이 있으니 월 저녁까지 기록이 없을 때만 ★).
   - `grm-intake.yml` · `grm-findings-backfill-fetch.yml` — 최근 3회가 전부 실패면 `★수집/백필 연속 실패`.
   - `grm-web-publish.yml` — 최근 실행이 실패면 그 실행 URL 과 함께 `★발행 조립 실패`.
   - 발송 리포트 본문(emails_sent·errors)은 status.json 에 없다 — 적지 않는다(성공 여부까지만 본다).

## 기록·보고
`company/run/$D/ops.md` 하나만 쓰고(브랜치 사용법은 `brief-prep.md` "기록"과 같다) 커밋·push.
첫 3줄은 `CHARTER.md` 형식. 그 아래 한 줄에 한 가지: 발행 PR · 번역(공개 N, 주간 +M) · 경고 이슈 · 방치 PR · 막힘.
★ 가 있으면 맨 앞에. 정상이면 짧게 끝낸다.

## 방법론 (공개 교본에서 우리에게 맞는 부분만 — 발행 준비 확인 `brief-prep.md` 와 공통)
출처: [Prometheus 알림 관행](https://prometheus.io/docs/practices/alerting/)(문서 저장소 Apache-2.0) ·
[healthchecks/healthchecks](https://healthchecks.io/docs/)(BSD-3, 개념만) ·
[PagerDuty 사고 대응 문서 · 사후 분석 절차](https://github.com/PagerDuty/incident-response-docs)(Apache-2.0) ·
[anthropics/knowledge-work-plugins · operations `runbook`](https://github.com/anthropics/knowledge-work-plugins/tree/main/operations)(Apache-2.0) ·
[Google SRE 책 · 분산 시스템 모니터링·사후 분석 문화](https://sre.google/sre-book/monitoring-distributed-systems/)(CC BY-NC-ND 4.0 — 문장을 옮기지 않고 방식만 요약).
- 알림은 증상 기준: 사용자(독자·대표)가 겪는 일이 아니면 별(★)을 달지 않는다. 한 번의 일시 실패(재실행으로 풀림)는 `★` 가 아니라 한 줄 참고다. 사람이 할 일이 있어야 `대표가 볼 것` 에 올린다. 늘 같은 기계적 대응이 필요한 항목은 "자동화 후보"로 적어 개발팀 일감으로 넘긴다.
- 배치는 "성공한 지 얼마나 됐나"로 판정: 주간 일은 한 주기 + 유예(크론 지각)를 넘겨 성공이 없을 때 ★. 이 팀이 `status.json` 에서 이미 쓰는 판정(3시간 넘은 `generated_at`·수집 연속 3회 실패·발송 월 저녁까지 기록 없음)이 같은 생각이다 — 위 점검 5번의 기준을 임의로 더 민감하게 바꾸지 않는다.
- 죽은 사람 스위치 시각으로 읽는다: 기대 시각(크론) + 유예 안에 신호(산출물·실행 기록)가 없으면 늦음, 유예까지 지나면 멈춤. 실행이 있었다 ≠ 일이 됐다 — 산출물(파일·PR·행)이 오늘 생겼는지로 확인한다.
- 알림 피로를 센다: 이번 주 ★ 중 대표가 실제로 움직인 것은 몇 개인가. 매주 같은 ★ 가 반복되면 그 ★ 의 임계를 높이거나 없애자고 제안한다(고치는 것은 개발팀).
- 사건은 사후 분석으로 닫는다(무비난): 기준 — 데이터 손실·발행 지연·사람이 먼저 발견한 장애. 형식 5줄: 무엇이 보였나 · 언제부터(타임라인은 링크된 증거로) · 어떻게 알았나(감시냐 사람이냐) · 왜 감시가 못 잡았나 · 막는 검사 1개와 그 담당(소유자 지정은 하되 끝까지 챙기는 것은 대표 몫).
- 같은 절차가 반복되면 런북으로 만든다: 목적 · 필요한 것 · 단계마다 "기대 결과"와 "실패하면" · 확인법 · 되돌리는 법 · 누구에게 올리나. 단계는 명령까지 구체적으로("스크립트를 돌린다" 금지).
뺀 것: Prometheus·Alertmanager·PagerDuty·Opsgenie 설치·유료 연동, 온콜 교대·에스컬레이션 표(1인 운영), SEV 등급별 응답 시간(SLA), SLO 오류 예산 계산, 사후 분석 회의 일정(3일·5영업일) — 회의 없는 구조라 5줄 문서로 대체.

## 데이터 (읽을 곳, 전부 읽기 전용)
| 무엇을 보나 | 어디서 | 비고 |
|---|---|---|
| 주요 워크플로 최근 5회 | 브랜치 `claude/ops-status` 의 `status.json` | `generated_at` 3시간 기준. 포함된 워크플로만 판정 가능 — 나머지는 `확인 못 함` |
| 열린 PR·체크런 | `gh api`(`company/CLOUD.md`) | 방치 PR 판정 |
| 경고 이슈 | 라벨 `automation-warning`·`automation-failure` 열린 이슈 | 이 API 는 PR 도 섞어 준다 |
| 번역 진행 | `select public.findings_stats();` (007·085) | 7일 증가 0 = 정체 |
| 알림 소유자·방치 | 표 `public.ops_alarm` (131) · 함수 `public.ops_alarm_neglected()` | 관리자 화면 `ops_home` 의 원천. 서비스 권한 전용이면 `확인 못 함` |
| 운영 지표 모음 | 함수 `public.ops_measure_all()` (128) | 서비스 권한 전용 |
| 방치 자동 PR 감시 | `.github/workflows/grm-stranded-batch-pr.yml` · `.github/workflows/grm-publish-watchdog.yml` | 이 팀의 판정과 겹치면 코드의 판정이 우선 |
| 지난주 결과 | 브랜치 `claude/company-<지난 월요일>` 의 `company/run/<날짜>/ops.md` | 증가폭·반복 ★ 비교 |

## 평가
- 성과 지표(1~3개): ① 실행 누락·실패를 **그날 안에** 알린 비율(분모 = 그 주 실제로 있었던 누락·실패; 사람이 먼저 안 것은 못 알린 것으로 센다) ② 문제 발생부터 대표에게 `대표가 볼 것` 이 도달하기까지 걸린 시간(건별 목록, 건수가 적어 평균·중앙값은 쓰지 않는다) ③ 오경보: ★ 중 확인해 보니 문제가 아니었던 것(분모 = ★ 총수).
- 첫 달 점검 질문: 이번 달 장애 중 이 점검이 먼저 알린 것과 대표가 먼저 안 것은? ★ 가 반복돼 대표가 무시하게 된 항목은? `확인 못 함` 이 늘 같은 항목에서 나오나(그렇다면 보는 길을 고쳐야 한다)? 한 줄로 끝나야 할 정상 주에 길게 쓴 일은?
- 2개월 판정: 두 달(점검 8회) 동안 먼저 알린 장애가 하나도 없고 대표가 먼저 안 것이 있으면 감시 대상·임계를 고칠 일감을 올린다. ★ 가 있었는데 대표가 움직인 것이 거의 없으면(예: 절반 미만) ★ 기준을 좁힌다. 문제가 전혀 없었고 ★ 도 없었으면 정상 — 주 1회를 유지하되 길이를 더 줄인다.

## 금지
머지 · Notion·Supabase 쓰기 · 코드 수정. 워크플로는 직접 부르지 않는다(요청 마커는 `brief-prep.md` 의 복구 절차에서만).
