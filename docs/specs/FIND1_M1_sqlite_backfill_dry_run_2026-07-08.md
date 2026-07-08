# FIND-1 M1i SQLite Backfill Transaction Dry-Run

> 날짜: 2026-07-08
> 범위: M1h backfill dry-run plan의 SQLite write 경계 검증
> 금지: SQLite 파일 write, Notion API 조회, Supabase write, Status/handoff 변경

## 목적

M1i는 M1h plan을 실제 SQLite 파일에 쓰기 전, 같은 DDL과 append helper를 `:memory:` 트랜잭션에서 검증한다.

검증 항목:

- `raw_signals`/`findings` DDL insert 가능 여부
- FK/unique 제약 동작
- exporter dry-run 메타 필드(`export_source`)가 저장소 insert를 깨지 않는지
- 같은 plan 재실행 시 duplicate skip으로 idempotent한지
- rollback 후 row count가 0으로 돌아오는지

## 실행 예시

M1h manifest에서 plan을 메모리로 만든 뒤 바로 SQLite dry-run:

```powershell
py findings_backfill_sqlite.py `
  --manifest tests/fixtures/findings_m1h_backfill_manifest.json `
  --output findings_sqlite_backfill_dry_run.json `
  --pretty
```

이미 생성한 M1h plan을 입력할 수도 있다.

```powershell
py findings_backfill_sqlite.py `
  --plan findings_internal_backfill_dry_run.json `
  --output findings_sqlite_backfill_dry_run.json `
  --pretty
```

생성 파일 `findings_sqlite_backfill_dry_run*.json`은 로컬 검증 산출물이므로 git 추적 제외한다.

## 현재 샘플 고정값

`tests/fixtures/findings_m1h_backfill_manifest.json` 기준:

- first pass: raw_signals inserted 6, findings inserted 7
- replay pass: raw_signals duplicate 6, findings duplicate 7
- after first pass counts: raw_signals 6, findings 7
- after replay pass counts: raw_signals 6, findings 7
- after rollback counts: raw_signals 0, findings 0
- blocking_errors: 0
- ready_for_commit_review: true

## 2026-07-08 운영 7월 dry-run 결과

`findings_internal_backfill_dry_run_2026_07.json` 기준:

- first pass: raw_signals inserted 112, findings inserted 24
- replay pass: raw_signals duplicate 112, findings duplicate 24
- after first pass counts: raw_signals 112, findings 24
- after replay pass counts: raw_signals 112, findings 24
- after rollback counts: raw_signals 0, findings 0
- blocking_errors: 0
- ready_for_commit_review: true

## Commit 경계

이 단계는 commit을 수행하지 않는다. `report.ready_for_commit_review=true`는 “SQLite 파일 write 경계로 넘겨도 됨”을 의미할 뿐, Supabase 적재나 운영 자동화를 승인하지 않는다. 운영 7월 dry-run은 M1j file apply로 이어져 로컬 `grm-findings.sqlite3`에 반영됐다.
