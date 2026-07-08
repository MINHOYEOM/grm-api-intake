# FIND-1 M1k Notion Export + July Backfill Runbook

> 날짜: 2026-07-08
> 범위: 실제 Notion Intake 데이터를 M1 backfill pipeline 입력으로 export한 뒤 SQLite 파일 apply까지 이어가는 운영 절차
> 금지: Notion write, Supabase write, Status/handoff 변경, 자동 운영 반영

## 목적

M1의 7월 작업은 스키마/코드 경계만으로 끝나지 않는다. 실제 운영 Intake 축적분을 `raw_signals`와 `findings(grm-finding/v1)` SQLite sidecar로 흡수해야 M1 백필이 닫힌다.

`findings_notion_export.py`는 그 첫 단계다. Notion Intake DB를 read-only로 조회하고, 각 page children에 저장된 raw API JSON을 복원해 기존 M1h planner가 그대로 읽을 수 있는 JSON을 만든다.

## 2026-07-08 운영 실행 결과

2026-07-08 현재 이 worktree에서 실제 7월 운영 백필을 완료했다.

- `.env` 저장 없이 공식 Notion API 인증값을 로컬 임시 환경변수로만 사용
- Notion MCP connector는 재연결 후 `GRM API Intake` DB fetch 성공
- 7월 운영 범위: `2026-07-01`~`2026-07-31`
- Notion export: page 117건 조회, routine/web-delta 운반 page 5건 제외, signal row 112건 export
- raw payload: 112/112 복원, `raw_fetch_missing=0`
- M1h plan: raw_signals 112, findings 24, blocking error 0
- M1i transaction dry-run: first pass raw_signals 112/findings 24 insert, replay duplicate 112/24, rollback 0/0
- M1j file apply: `grm-findings.sqlite3`에 raw_signals 112/findings 24 commit, `ready_for_search_export=true`
- 샘플 page fetch에서 `Raw API payload` JSON code block 복원 가능 확인
- GitHub Actions artifact에는 `brief_web_*.json`만 있고, Intake raw payload export가 없음

이번 실행은 Notion read-only API 조회와 로컬 SQLite 파일 write까지만 수행했다. Notion write, Supabase write, Status/handoff 변경, web publish 자동 반영은 없다. 운영 인증값은 문서나 저장소 파일에 기록하지 않는다.

## 1. Notion Intake export

7월 누적분만 대상으로 export:

```powershell
py findings_notion_export.py `
  --database-id 7784c71fb7b343749b2bee5d04db7926 `
  --run-date-from 2026-07-01 `
  --run-date-to 2026-07-31 `
  --output findings_notion_export_2026_07.json `
  --pretty
```

`NOTION_TOKEN`은 환경변수로 제공한다. CLI에 직접 넘길 수도 있으나 shell history 노출을 피하려면 환경변수를 권장한다.

성공 기준:

- `report.preflight.notion_api = read_only`
- `report.preflight.sqlite_write = not_used`
- `report.preflight.supabase_write = not_used`
- `report.preflight.status_handoff = not_used`
- `report.preflight.ready_for_backfill_plan = true`

`raw_fetch_missing > 0`이면 M1h로 넘기기 전에 해당 page의 raw children 보존 상태를 확인한다.

실제 결과:

- `report.query_pages = 2`
- `report.pages_seen = 117`
- `report.signal_rows_exported = 112`
- `report.raw_fetch_ok = 112`
- `report.raw_fetch_missing = 0`
- `report.rows_by_source = FDA 483 4, FDA Warning Letter 5, Federal Register 10, OpenFDA Recall 49, MFDS 28, ECA Academy 9, EMA 5, Health Canada 2`

## 2. M1h plan 생성

```powershell
py findings_backfill.py `
  --input notion-july=findings_notion_export_2026_07.json `
  --output findings_internal_backfill_dry_run_2026_07.json `
  --pretty
```

성공 기준:

- `report.preflight.ready_for_sqlite_append_dry_run = true`
- `report.preflight.blocking_errors = 0`

Duplicate과 findings 미생성 raw_signal은 review warning이다. Blocking error는 apply 전에 반드시 해소한다.

실제 결과:

- `report.raw_signals_unique = 112`
- `report.findings_unique = 24`
- `report.raw_signal_duplicates = 0`
- `report.finding_duplicates = 0`
- `report.preflight.blocking_errors = 0`
- `report.raw_signals_without_findings = 104`

## 3. M1i SQLite transaction dry-run

```powershell
py findings_backfill_sqlite.py `
  --plan findings_internal_backfill_dry_run_2026_07.json `
  --output findings_sqlite_backfill_dry_run_2026_07.json `
  --pretty
```

성공 기준:

- `report.ready_for_commit_review = true`
- first pass insert/replay duplicate/rollback counts가 모두 기대대로 기록됨

실제 결과:

- first pass: raw_signals 112 insert, findings 24 insert
- replay pass: raw_signals 112 duplicate, findings 24 duplicate
- rollback 후 count: raw_signals 0, findings 0
- `report.ready_for_commit_review = true`
- `report.blocking_errors = 0`

## 4. M1j SQLite file apply

```powershell
py findings_backfill_apply.py `
  --plan findings_internal_backfill_dry_run_2026_07.json `
  --db-path grm-findings.sqlite3 `
  --write-file `
  --output findings_sqlite_backfill_apply_2026_07.json `
  --pretty
```

성공 기준:

- `write_guard.committed = true`
- `report.ready_for_search_export = true`
- `report.blocking_errors = 0`

재실행 시 같은 DB에 duplicate skip으로 끝나야 한다. 이 멱등성이 깨지면 M2로 넘어가지 않는다.

실제 결과:

- `write_guard.committed = true`
- `write_guard.database_existed_before = false`
- `report.ready_for_search_export = true`
- `report.blocking_errors = 0`
- commit 후 count: raw_signals 112, findings 24

## 산출물 추적

운영 export와 SQLite sidecar는 로컬 산출물이며 git 추적 대상이 아니다.

- `findings_notion_export*.json`
- `findings_internal_backfill_dry_run*.json`
- `findings_sqlite_backfill_dry_run*.json`
- `findings_sqlite_backfill_apply*.json`
- `grm-findings.sqlite3`
- `*.sqlite3-shm`
- `*.sqlite3-wal`

## M1 완료 판정

M1 완료는 다음이 모두 참일 때로 본다. 2026-07-08 현재 모두 충족했다.

- M1a~M1k 테스트 통과
- 운영 Notion export 완료
- M1h/M1i/M1j report의 blocking error 0
- `grm-findings.sqlite3`에 실제 7월 Intake 기반 `raw_signals`/`findings`가 commit됨
- 기존 Notion write, handoff, Status, web publish, Supabase 흐름에 영향 없음
