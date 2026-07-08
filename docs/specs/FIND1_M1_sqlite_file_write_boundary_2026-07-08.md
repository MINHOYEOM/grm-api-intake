# FIND-1 M1j SQLite File Write Boundary

> 날짜: 2026-07-08
> 범위: M1h plan을 명시 guard 뒤 로컬 SQLite 파일에 적용하는 수동 write 경계
> 금지: Notion API 조회, Supabase write, Status/handoff 변경, 운영 자동화

## 목적

M1j는 M1h/M1i를 통과한 내부 백필 plan을 실제 SQLite 파일에 적용할 수 있는 최소 경계를 만든다.

파일 write는 기본 동작이 아니며, 다음 조건을 모두 만족해야 한다.

- `--write-file` 명시
- `--db-path` 명시
- 입력 plan이 M1i transaction dry-run에서 `ready_for_commit_review=true`
- orphan finding, validation error, skipped row 같은 blocking error 없음

## 실행 예시

샘플 manifest를 임시/로컬 SQLite 파일에 적용:

```powershell
py findings_backfill_apply.py `
  --manifest tests/fixtures/findings_m1h_backfill_manifest.json `
  --db-path grm-findings.sqlite3 `
  --write-file `
  --output findings_sqlite_backfill_apply.json `
  --pretty
```

`grm-findings.sqlite3`, `*.sqlite3-shm`, `*.sqlite3-wal`은 git 추적 제외한다.

## 현재 샘플 고정값

`tests/fixtures/findings_m1h_backfill_manifest.json` 기준:

- first apply: raw_signals inserted 6, findings inserted 7
- second apply: raw_signals duplicate 6, findings duplicate 7
- committed counts: raw_signals 6, findings 7
- blocking_errors: 0
- ready_for_search_export: true

## 2026-07-08 운영 7월 apply 결과

`findings_internal_backfill_dry_run_2026_07.json`을 `grm-findings.sqlite3`에 적용:

- database_existed_before: false
- first apply: raw_signals inserted 112, findings inserted 24
- committed counts: raw_signals 112, findings 24
- blocking_errors: 0
- ready_for_search_export: true

## 경계

이 단계는 SQLite 파일 write만 연다. Supabase 적재, Notion write, Status/handoff 변경, 대시보드/검색 UI 구현은 하지 않는다.

운영 Notion Intake read-only export(M1k)와 해당 export 기반 M1h/M1i/M1j apply가 2026-07-08에 성공했으므로 7월 M1 백필은 완료로 본다. 이후 작업은 M2 성격의 `findings.json` export, SQLite view, 검색 페이지 v0 또는 M3 성격의 Supabase 적재 설계로 분리한다.
