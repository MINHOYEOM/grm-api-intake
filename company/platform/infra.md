# 수집·인프라팀 · 코드 — 모델 없음 (크론·워크플로가 일한다)

플랫폼 부문. 사람·Claude 가 매일 하는 일이 아니다: 수집기(`collect_*.py`)·워크플로(`.github/workflows/`)·DB 유지보수·백업이 **코드로** 돈다.
이 문서는 ① 이 코드를 고치는 사람(개발팀 세션)이 지킬 견고성 규칙 ② 이 코드가 제대로 도는지 읽는 팀(종합상황실·데이터품질팀)이 어디를 볼지 ③ 평가 기준을 적는다.
팀을 호출하는 방법은 없다 — 고치는 일은 개발팀(`company/platform/dev.md`) 일감으로 올린다.

## 읽을 것
`company/CHARTER.md` · `company/CLOUD.md` · `docs/ops_runbook.md`(자격증명 로테이션·장애 분기·백업 복원 정본) · 이 파일.

## 방법론 (공개 교본에서 우리에게 맞는 부분만)
출처: [urllib3 `Retry`](https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html)(MIT) ·
[Scrapy AutoThrottle](https://docs.scrapy.org/en/latest/topics/autothrottle.html)(코드 BSD-3, 문서 페이지에 라이선스 표기 없음 — 방식만 요약) ·
[Prometheus 알림 관행](https://prometheus.io/docs/practices/alerting/)(문서 저장소 Apache-2.0) ·
[healthchecks/healthchecks](https://healthchecks.io/docs/)(BSD-3, "죽은 사람 스위치" 개념만) ·
[Google SRE 책 · 분산 시스템 모니터링](https://sre.google/sre-book/monitoring-distributed-systems/)(CC BY-NC-ND 4.0 — 문장을 옮기지 않고 방식만 요약).
**수집 견고성**
- 재시도는 "다시 해도 안전한 요청"에만(읽기). 쓰기·되돌릴 수 없는 호출은 멱등 키(같은 입력 = 같은 결과)가 있을 때만 다시 한다. 연결 실패(요청이 서버에 안 닿음)와 읽기 실패(닿았고 부작용이 있을 수 있음)를 나눠 센다.
- 대기 시간은 지수로 늘리되 상한을 둔다(`간격 = 기본 × 2^시도 횟수`, 최대 값 고정). 서버가 `Retry-After` 를 주면 그 값을 따르되 상한으로 자른다. 다시 할 상태 코드는 429·503 등 명시한 것만(`status_forcelist`), 그 밖의 4xx 는 다시 해도 같으므로 바로 실패.
- 오류 응답은 속도를 올리지 않는다: 오류 응답의 지연은 간격을 늘리기만 하고 줄이지 않는다(자동 속도 조절의 규칙). 느려진 사이트를 더 세게 두드리면 차단된다.
- 한 번의 실행이 모든 소스를 끌고 죽지 않게 소스 단위로 실패를 가둔다. "오류 없는 0건"은 따로 센다 — 마지막으로 데이터가 들어온 날 대비(`source_silence.py` 가 이미 한다).
- 차단·막힘은 우회하지 않고 판정한다: 착수 전에 러너에서 닿는지 먼저 본다(`.github/workflows/grm-source-probe.yml`). 막힌 소스를 다른 길로 뚫지 않는다 — 판정 결과를 대표에게 알려 정하게 한다.
- 이 저장소의 현재 도우미(`grm_common.py` 의 `http_get_*`)는 429 에서 `Retry-After`(초 단위)를 읽고 5xx·네트워크 오류에서 `2^시도` 초를 기다린다. **알려진 한계**: `Retry-After` 가 날짜 형식이면 읽지 못하고 `2^시도` 로 떨어진다 · 대기에 무작위 흔들림(지터)이 없다. 고칠지는 개발팀 일감으로 대표가 정한다 — 이 문서는 고치지 않는다.
**관측·알림 (알림 피로 줄이기)**
- 사람에게 알리는 것은 "지금 사용자가 겪는 문제 + 사람이 할 일이 있는 것"만. 원인(CPU·한 번의 일시 실패)은 대시보드·로그로 둔다. 같은 알림이 매번 같은 기계적 대응이면 알림을 없애고 자동화한다.
- 배치는 "한 번 실패했다"가 아니라 "성공한 지 너무 오래됐다"로 알린다(예: 매일 도는 일이 이틀 넘게 성공이 없으면). 한 번의 실패가 사람을 부를 정도면 그 일을 더 자주 돌려 한 번의 실패를 견디게 만든다.
- 알림에는 어디를 볼지(실행 주소·표·화면)를 붙인다. 알림 메일은 쌓이면 소음이 된다 — 같은 건은 열린 이슈 하나로 합치고(이 저장소의 이슈 스로틀과 같은 생각), 새 이슈를 늘리지 않는다.
- "도는지"를 지키는 장치도 지킨다(감시의 감시): 감시가 죽은 채 초록으로 보이는 것이 가장 위험하다. 실행 이력이 아니라 **산출물이 오늘 생겼나**로 확인한다. 정해진 시각에 신호가 와야 하고 늦으면 알리는 "죽은 사람 스위치" 방식(기대 주기 + 유예 시간)이 기본 모양이다.
- 네 가지 신호로 사이트·수집을 본다: 지연(느려졌나) · 양(들어오는 문서가 평소와 비슷한가) · 오류(**200 인데 내용이 틀린 것 포함**) · 포화(저장공간·행 수·사용 한도 — 여기서는 DB 용량).
- 사건이 나면 사후 분석을 쓴다(무비난·원인은 시스템에서): 무엇이 보였나 → 언제부터 → 어떻게 알았나(사람이냐 감시냐) → 왜 감시가 못 잡았나 → 막는 검사 1개. 기준: 데이터 손실·발행 지연·감시가 못 잡고 사람이 발견한 사건. 실행 한 번 실패 같은 것은 쓰지 않는다.
뺀 것: Prometheus·Grafana·PagerDuty·Datadog 등 설치·유료 연동, 온콜 교대·에스컬레이션 체계(1인 운영), SEV 응답 시간표, SLO 계산 체계(구독자·방문 규모에서는 근거가 약함), Scrapy 도입·분산 크롤링.

## 데이터 (읽을 곳, 전부 읽기 전용)
| 무엇을 보나 | 어디서 | 비고 |
|---|---|---|
| 주요 워크플로 최근 5회 | 브랜치 `claude/ops-status` 의 `status.json`(30분마다 갱신, `generated_at`) | 수집(`grm-intake.yml`)·발행·번역·발송 등 12개. 3시간 넘게 안 갱신되면 `막힘: Actions` |
| 수집 한 바퀴 | `.github/workflows/grm-intake.yml`(매일 + 일요일 보강 크론) · 실행 요약의 health | 소스별 오류·0건 |
| 소스 무음·원천 대조 | `source_silence.py` · `source_reconcile.py`(`.github/workflows/grm-source-reconcile.yml`) | 건수 등식 |
| 발행 길 감시 | `.github/workflows/grm-publish-watchdog.yml` · `.github/workflows/grm-stranded-batch-pr.yml` | 월요일 침묵 실패·방치된 자동 PR |
| 사이트 | `.github/workflows/grm-site-probe.yml`(합성) · `.github/workflows/grm-site-error-watch.yml`(Cloudflare 실측) | 실패 이슈 |
| DB 용량·생존 | `.github/workflows/grm-db-size-health.yml` · 표 `public.ops_db_size_daily` (154) · 함수 `public.grm_db_size_status()` (154·165, 서비스 권한) · `.github/workflows/grm-supabase-keepalive.yml` | 무료 플랜 한도 |
| 백업 | `.github/workflows/grm-db-backup.yml`(주 1회, 암호화 아티팩트 90일) · `db_backup_verify.py` | 복원 절차 `docs/ops_runbook.md` §6 |
| 알림 소유자 | 표 `public.ops_alarm` (131) · `.github/workflows/grm-ops-alarm.yml`(매시) | 방치 판정 |
| 경고 이슈 | 열린 이슈 라벨 `automation-warning`·`automation-failure` | 이 API 는 PR 도 섞어 준다 |
| 열쇠·자격증명 | `docs/ops_runbook.md` §1 | 만료 의심 신호는 401/403 비일시 오류·특정 소스 연속 0건 |

## 평가
- 성과 지표(1~3개): ① 수집 실행 성공률과 **소스별 마지막 수집일**(조용히 멈춘 소스가 0 인가 — 가장 오래 무음인 소스의 일수) ② 사람이 먼저 알아챈 장애 수(감시가 먼저 알린 것과 구분 — 사람이 먼저면 감시 구멍 1건) ③ 알림 소음: 한 달에 열린 자동 이슈 중 "할 일이 없던" 것의 비율.
- 첫 달 점검 질문: 이번 달 장애 중 감시가 먼저 알린 것은 몇 건이고 사람이 먼저 안 것은? 열린 자동 이슈 중 같은 원인이 반복된 것은? 감시 자신이 죽었던 일은? 재시도가 차단·429 를 키운 징후(같은 소스의 연속 실패)는? 백업 복원 시험을 분기에 1회 했나?
- 2개월 판정: 두 달 동안 사람이 먼저 안 장애가 0 이면 감시 구성이 충분하다고 기록하고 알림 줄이기(같은 건 합치기)로 간다. 1건 이상이면 그 장애를 잡는 감시를 개발팀 일감으로 올린다. "할 일이 없던" 자동 이슈가 절반을 넘으면 그 이슈의 임계를 높이거나 이슈를 없애는 안을 대표에게 올린다.

## 금지
이 팀(코드) 영역을 고치는 Claude 세션도 `company/platform/dev.md` 의 금지를 따른다: 머지 · migration 적용·운영 DB 쓰기 · 비밀값 출력(서비스키는 로그에 마스킹) · 소스의 접근 제한 우회 · 유료 도구·플랜 제안(비용 0 원칙).
